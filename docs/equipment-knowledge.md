# Training catalog and equipment knowledge

Equipment is no longer the source of training intent. A confirmed primary goal
and optional supporting goal select reusable planning contexts; those contexts
then expose preferred and substitute execution options with explicit capability
requirements.

```text
Goal template
  -> target/supporting training context
  -> preferred/substitute execution option
  -> required/recommended/optional capability
  -> athlete AVAILABLE / UNAVAILABLE / implicit UNKNOWN
```

The global PostgreSQL catalog consists of `goal_templates`,
`training_contexts`, `goal_template_contexts`, `capabilities`,
`context_execution_options`, and `execution_option_capabilities`.
`athlete_capabilities` is the only athlete-owned layer. A capability can be
equipment, access, or a facility, and the same global capability can support
many contexts without duplication.

Revision `0022_dynamic_training_catalog` creates the model and an intentionally
general seed covering running, cycling, swimming, hiking, strength, triathlon,
HYROX, and obstacle racing. It replaces `equipment_catalog` and
`athlete_equipment` after a verified backfill. Its downgrade aborts because
dynamically generated catalog knowledge cannot be reconstructed in the old
tables; take a database backup before upgrading.

Unknown goals can extend the catalog only after athlete confirmation. The
compiled structured workflows first map all new templates to existing or new
contexts, then define options and capabilities for all new contexts in one
grouped call. Strict application validation and an advisory transaction lock
publish the complete proposal atomically. The model never writes the database,
supplies IDs, edits existing definitions, or runs from Equipment & access
callbacks.

The equipment review includes only capabilities reachable from the athlete's
current primary/supporting goal. Saving marks every visible capability
AVAILABLE or UNAVAILABLE while preserving unrelated answers. Feasibility and
substitutions are calculated from execution options and are advisory; a future
planner consumes `GoalExecutionAssessment` rather than interpreting catalog
rows itself.
