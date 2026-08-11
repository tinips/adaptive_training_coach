# Equipment knowledge

Equipment knowledge is deterministic reference data in PostgreSQL. Alembic
revision `0017_equipment_catalog` creates and seeds:

- `equipment_catalog`, keyed by discipline and stable lower-snake-case item;
- `athlete_equipment`, the global set of catalog items an athlete can use.

Catalog importance is fixed as `essential`, `recommended`, or `optional`.
Essential means required to train the discipline, but a listed substitution
also satisfies it. Substitutions are a small JSON array of keys in the same
discipline; they are intentionally not a generic rules subsystem.

The application resolves confirmed goal text to running, cycling, swimming,
hiking, and/or strength using bounded English aliases. Triathlon and Ironman
resolve to swim-bike-run; duathlon resolves to bike-run. Event date, baseline,
training stage, and race proximity do not affect importance or matching.

The Telegram review preselects the athlete's existing global access. Saving a
review replaces only rows for the reviewed disciplines and preserves all other
disciplines. Missing essentials do not block onboarding. The summary shows
missing essentials with alternatives and missing recommended items; optional
gaps are omitted.

Revision `0018_remove_obsolete_equipment` performs the authorized cleanup. It
reruns and verifies the explicit current-revision `AVAILABLE` backfill, rejects
unknown source codes, removes the seven `0014` equipment tables, removes legacy
`equipment_access` when present, drops the goal revision and raw profile
equipment columns, and removes the obsolete details step from database checks.
Discarded raw text and interpretation history can only be recovered from the
pre-cleanup PostgreSQL backup.

To change catalog knowledge, add an additive Alembic reference-data migration
using stable IDs. Do not add an LLM prompt, stage logic, or a generic rules
engine for equipment prioritization.
