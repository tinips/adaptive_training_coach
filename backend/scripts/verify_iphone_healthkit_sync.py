"""Throwaway iPhone-impersonation client for the HealthKit sync POC.

This starts a REAL local API process and talks to it over REAL HTTP with
``httpx``, the same way the Coach Health Sync iOS app does. That is
deliberately different from ``backend/tests/api/test_mobile_sync.py``, which
drives the FastAPI app in-process over an ASGI transport against an
in-memory SQLite database. This script exists to additionally exercise the
parts pytest cannot: process startup with real environment variables, real
config loading, real HTTP routing, and a real PostgreSQL database — the
whole server-side stack a physical iPhone would actually hit.

It is intentionally NOT part of the pytest suite. It launches a subprocess,
binds a real socket, depends on an already-migrated external PostgreSQL
database, and mutates that database's rows directly (to simulate a pairing
code aging past its 10-minute TTL without an actual 10-minute sleep). None
of that fits the existing suite's hermetic, in-memory-SQLite, ASGI-transport
convention, so it stays a standalone operational script rather than a test.

Usage (PostgreSQL already migrated to head)::

    DATABASE_URL=postgresql+asyncpg://coach:coach@localhost:5432/adaptive_coach \\
        python backend/scripts/verify_iphone_healthkit_sync.py

Every assertion is a plain Python ``assert``; the script exits non-zero (an
uncaught ``AssertionError`` or ``SystemExit``) the moment one fails. Two
disposable athletes with random Telegram IDs are created on every run, so
re-running is always safe and never collides with a previous run.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import subprocess
import sys
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings  # noqa: E402
from app.db.models import Workout  # noqa: E402
from app.db.session import create_engine, create_session_factory  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.schemas.common import TelegramIdentity  # noqa: E402
from app.services.mobile_sync import MobileSyncService  # noqa: E402

DEFAULT_DATABASE_URL = "postgresql+asyncpg://coach:coach@localhost:5432/adaptive_coach"
SERVER_STARTUP_TIMEOUT_SECONDS = 20.0
SERVER_LOG_PATH = BACKEND_ROOT / "scripts" / ".verify_server.log"

Factory = async_sessionmaker[AsyncSession]

# Secret values minted during the run, checked against the server's own log
# output at the very end (step 5: log hygiene).
_collected_secrets: list[str] = []


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _iso8601_no_fraction(value: datetime) -> str:
    """Match Swift's ``JSONEncoder().dateEncodingStrategy = .iso8601`` output.

    Foundation's ISO8601DateFormatter (used by the ``.iso8601`` strategy)
    formats in UTC with no fractional seconds, e.g. ``2026-08-25T10:15:00Z``.
    """

    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _random_telegram_identity() -> TelegramIdentity:
    telegram_user_id = secrets.randbelow(900_000_000) + 100_000_000
    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        telegram_username=f"verify_script_{telegram_user_id}",
        first_name="ScriptAthlete",
    )


async def _stage_athlete(factory: Factory, identity: TelegramIdentity) -> uuid.UUID:
    async with factory() as session, session.begin():
        user, _created = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
            language_code=identity.language_code,
        )
        return user.id


async def _workout_count(factory: Factory, *, athlete_id: uuid.UUID) -> int:
    async with factory() as session:
        rows = list(
            await session.scalars(
                select(Workout).where(Workout.athlete_id == athlete_id)
            )
        )
        return len(rows)


async def _expire_pairing_code(factory: Factory, *, athlete_id: uuid.UUID) -> None:
    """Directly age a pairing code past its TTL without a real 10-minute wait.

    This bypasses the service layer on purpose: it is test setup, not a
    production code path. Everything asserted afterward still goes through
    the real HTTP API and the real service/repository code.
    """

    past = datetime.now(UTC) - timedelta(minutes=1)
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE mobile_sync_credentials "
                "SET pairing_code_expires_at = :past "
                "WHERE user_id = :athlete_id"
            ),
            {"past": past, "athlete_id": athlete_id},
        )


def _healthkit_payload(
    *,
    workout_uuid: uuid.UUID,
    started_at: datetime,
    ended_at: datetime,
    duration_seconds: int,
    distance_meters: float | None,
    calories_kcal: float | None,
    activity_type: str = "running",
) -> dict[str, object]:
    """Build a payload byte-for-byte matching HealthKitWorkoutSyncPayload.swift."""

    return {
        "workout_uuid": str(workout_uuid),
        "activity_type": activity_type,
        "started_at": _iso8601_no_fraction(started_at),
        "ended_at": _iso8601_no_fraction(ended_at),
        "duration_seconds": duration_seconds,
        "distance_meters": distance_meters,
        "calories_kcal": calories_kcal,
    }


async def _wait_ready(base_url: str) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + SERVER_STARTUP_TIMEOUT_SECONDS
    async with httpx.AsyncClient() as client:
        while True:
            try:
                response = await client.get(f"{base_url}/ready", timeout=1.0)
                if response.status_code == 200:
                    print(f"[setup] API is ready at {base_url}")
                    return
            except httpx.HTTPError:
                pass
            if loop.time() > deadline:
                raise RuntimeError(
                    f"Local API did not become ready in time; see {SERVER_LOG_PATH}"
                )
            await asyncio.sleep(0.2)


async def _run_checks(*, base_url: str, factory: Factory, settings: Settings) -> None:
    service = MobileSyncService(session_factory=factory, settings=settings)

    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
        # --- (a) seed a test athlete, (b) mint a pairing code the way
        # /connect_iphone does: by calling the same MobileSyncService the
        # bot command calls, against the real database. ---
        identity_a = _random_telegram_identity()
        athlete_a_id = await _stage_athlete(factory, identity_a)
        pairing_a = await service.issue_pairing_code(identity_a)
        _collected_secrets.append(pairing_a.code)
        print(f"[connect_iphone] issued pairing code for athlete A={athlete_a_id}")

        # --- (c) POST /v1/mobile/pair, assert a bearer token comes back ---
        installation_id_a = uuid.uuid4()
        pair_response = await client.post(
            "/v1/mobile/pair",
            json={
                "pairing_code": pairing_a.code,
                "installation_id": str(installation_id_a),
            },
        )
        assert pair_response.status_code == 200, pair_response.text
        token_a = pair_response.json()["access_token"]
        assert token_a and isinstance(token_a, str)
        assert pair_response.json()["token_type"] == "Bearer"
        _collected_secrets.append(token_a)
        print("[pair] PASS: valid pairing code returns a bearer token")

        headers_a = {"Authorization": f"Bearer {token_a}"}

        # --- (d) sync one synthetic workout, assert "inserted" ---
        workout_uuid = uuid.uuid4()
        started_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=45)
        ended_at = started_at + timedelta(minutes=40)
        payload = _healthkit_payload(
            workout_uuid=workout_uuid,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=2400,
            distance_meters=7500.0,
            calories_kcal=480.0,
        )
        first_sync = await client.post(
            "/v1/mobile/healthkit/workouts:sync",
            json={"workouts": [payload]},
            headers=headers_a,
        )
        assert first_sync.status_code == 200, first_sync.text
        first_result = first_sync.json()["results"][0]
        assert first_result["outcome"] == "inserted", first_result
        assert first_result["workout_uuid"] == str(workout_uuid)
        workout_id = first_result["workout_id"]
        print(f"[sync] PASS: first sync inserted workout_id={workout_id}")

        count_after_insert = await _workout_count(factory, athlete_id=athlete_a_id)
        assert count_after_insert == 1, count_after_insert

        # --- (e) replay the identical payload, assert "unchanged" + no
        # second row ---
        second_sync = await client.post(
            "/v1/mobile/healthkit/workouts:sync",
            json={"workouts": [payload]},
            headers=headers_a,
        )
        assert second_sync.status_code == 200, second_sync.text
        second_result = second_sync.json()["results"][0]
        assert second_result["outcome"] == "unchanged", second_result
        assert second_result["workout_id"] == workout_id
        count_after_replay = await _workout_count(factory, athlete_id=athlete_a_id)
        assert count_after_replay == 1, (
            f"expected no duplicate row, found {count_after_replay}"
        )
        print("[sync] PASS: identical replay is unchanged, no duplicate row")

        # --- (f) POST a modified version of the same UUID, assert "updated" ---
        modified_payload = _healthkit_payload(
            workout_uuid=workout_uuid,
            started_at=started_at,
            ended_at=ended_at + timedelta(minutes=5),
            duration_seconds=2700,
            distance_meters=8100.0,
            calories_kcal=520.0,
        )
        third_sync = await client.post(
            "/v1/mobile/healthkit/workouts:sync",
            json={"workouts": [modified_payload]},
            headers=headers_a,
        )
        assert third_sync.status_code == 200, third_sync.text
        third_result = third_sync.json()["results"][0]
        assert third_result["outcome"] == "updated", third_result
        assert third_result["workout_id"] == workout_id
        count_after_update = await _workout_count(factory, athlete_id=athlete_a_id)
        assert count_after_update == 1, (
            f"expected the same single row to be updated, found {count_after_update}"
        )
        print("[sync] PASS: modified payload for the same UUID is updated in place")

        # --- (g) invalid or expired pairing code returns 401 ---
        bogus_code_response = await client.post(
            "/v1/mobile/pair",
            json={
                "pairing_code": "NOTAREALCODE123",
                "installation_id": str(uuid.uuid4()),
            },
        )
        assert bogus_code_response.status_code == 401, bogus_code_response.text
        print("[pair] PASS: an invalid pairing code returns 401")

        identity_b = _random_telegram_identity()
        athlete_b_id = await _stage_athlete(factory, identity_b)
        pairing_b_expired = await service.issue_pairing_code(identity_b)
        _collected_secrets.append(pairing_b_expired.code)
        await _expire_pairing_code(factory, athlete_id=athlete_b_id)
        expired_response = await client.post(
            "/v1/mobile/pair",
            json={
                "pairing_code": pairing_b_expired.code,
                "installation_id": str(uuid.uuid4()),
            },
        )
        assert expired_response.status_code == 401, expired_response.text
        print("[pair] PASS: an expired pairing code returns 401")

        # --- (h) a bearer token belonging to another athlete cannot read or
        # write this athlete's workouts ---
        pairing_b = await service.issue_pairing_code(identity_b)
        _collected_secrets.append(pairing_b.code)
        pair_b_response = await client.post(
            "/v1/mobile/pair",
            json={
                "pairing_code": pairing_b.code,
                "installation_id": str(uuid.uuid4()),
            },
        )
        assert pair_b_response.status_code == 200, pair_b_response.text
        token_b = pair_b_response.json()["access_token"]
        _collected_secrets.append(token_b)
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Athlete B syncs a workout that reuses athlete A's HealthKit UUID.
        # The route accepts no athlete identifier in the payload at all, so
        # this also proves a colliding external identity cannot let one
        # athlete's token touch another athlete's row.
        collision_payload = _healthkit_payload(
            workout_uuid=workout_uuid,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=1800,
            distance_meters=5000.0,
            calories_kcal=300.0,
        )
        b_sync = await client.post(
            "/v1/mobile/healthkit/workouts:sync",
            json={"workouts": [collision_payload]},
            headers=headers_b,
        )
        assert b_sync.status_code == 200, b_sync.text
        b_result = b_sync.json()["results"][0]
        assert b_result["outcome"] == "inserted", b_result
        assert b_result["workout_id"] != workout_id, (
            "athlete B's sync must not resolve to athlete A's workout row"
        )

        async with factory() as session:
            workout_b = await session.get(Workout, uuid.UUID(b_result["workout_id"]))
            assert workout_b is not None
            assert workout_b.athlete_id == athlete_b_id, (
                "workout written by token B must be owned by athlete B, "
                f"got {workout_b.athlete_id}"
            )

        count_a_unaffected = await _workout_count(factory, athlete_id=athlete_a_id)
        assert count_a_unaffected == 1, (
            "athlete B's sync must not have touched athlete A's rows, "
            f"found {count_a_unaffected}"
        )
        print(
            "[isolation] PASS: token B's writes are owned by athlete B only; "
            "athlete A's workouts are untouched"
        )
        print(
            "[isolation] NOTE: this POC exposes no GET/read endpoint for "
            "HealthKit workouts, so only the write path is exercised here; "
            "there is nothing to call to prove read isolation."
        )

        # A token that does not belong to anyone must not authenticate either.
        garbage_token_response = await client.post(
            "/v1/mobile/healthkit/workouts:sync",
            json={"workouts": [collision_payload]},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert garbage_token_response.status_code == 401, garbage_token_response.text
        print("[sync] PASS: an unrecognized bearer token returns 401")


def _check_server_log_for_secrets(
    log_path: Path, *, secrets_to_check: list[str]
) -> None:
    print(
        f"\n[log-hygiene] scanning {log_path} for {len(secrets_to_check)} secret values"
    )
    content = log_path.read_text(encoding="utf-8", errors="replace")
    leaked = [value for value in secrets_to_check if value and value in content]
    assert not leaked, f"secret values leaked into server logs: {leaked!r}"
    print(
        "[log-hygiene] PASS: none of the pairing codes or bearer tokens issued "
        "during this run appear anywhere in the server's stdout/stderr log"
    )


def _spawn_server(*, port: int, server_env: dict[str, str]) -> subprocess.Popen[bytes]:
    """Launch the real uvicorn process; kept synchronous (blocking I/O only)."""

    with open(SERVER_LOG_PATH, "w", encoding="utf-8") as log_file:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=BACKEND_ROOT,
            env=server_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )


async def main() -> None:
    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    print(f"[setup] using DATABASE_URL={database_url}")

    settings = Settings(
        database_url=database_url,
        mobile_sync_enabled=True,
        environment="development",
    )
    engine = create_engine(settings)
    factory = create_session_factory(engine)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    server_env = dict(os.environ)
    server_env["DATABASE_URL"] = database_url
    server_env["MOBILE_SYNC_ENABLED"] = "true"
    server_env["ENVIRONMENT"] = "development"
    server_env["LOG_LEVEL"] = "INFO"

    server = await asyncio.to_thread(_spawn_server, port=port, server_env=server_env)

    try:
        await _wait_ready(base_url)
        await _run_checks(base_url=base_url, factory=factory, settings=settings)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        await engine.dispose()

    _check_server_log_for_secrets(SERVER_LOG_PATH, secrets_to_check=_collected_secrets)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
