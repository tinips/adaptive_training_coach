"""First-week plans persist at schema v5, which carries session UUIDs.

docs/briefs/backlog/first-week-evaluator.md, "Stable session reference":
"Bump the plan schema version and define schema-v4 loading behavior."
Schema v4 already meant FirstWeekPlan (no session ids); v5 is the same
FirstWeekPlan shape, now with a code-generated ``id`` on every session. The
loader treats any version >= 4 carrying ``plan_kind`` as FirstWeekPlan
already, so no loader branch changes; this pins the write-side constant so
new writes are distinguishable from pre-identity v4 rows.
"""

from __future__ import annotations

from app.services.weekly_planning.constants import FIRST_WEEK_PLAN_SCHEMA_VERSION


def test_first_week_plan_schema_version_is_five() -> None:
    assert FIRST_WEEK_PLAN_SCHEMA_VERSION == 5
