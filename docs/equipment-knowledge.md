# Equipment knowledge

Equipment recommendations are deterministic reference data in PostgreSQL. The
initial catalog, supported goal terms, stage windows, requirements, and
substitutions are seeded by Alembic revision `0014_equipment_knowledge`.

To add a discipline, event, resource, or substitution, create a new additive
Alembic reference-data migration. Do not add recommendation branches or an LLM
prompt for this knowledge. Athlete selections are scoped to the active training
goal and its `equipment_context_revision`.
