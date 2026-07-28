# ExecPlans

An ExecPlan is the persistent, self-contained engineering record for a
substantial repository change. It is both an implementation guide and evidence
of what was validated.

Every active ExecPlan must keep these sections current:

- objective and user-visible outcome;
- repository state before work;
- architecture and security decisions;
- implementation phases with checkboxes;
- discoveries and assumptions;
- validation commands and observed results;
- failures encountered and fixes applied;
- external or manual blockers;
- final completion evidence.

Progress entries should describe working behavior, not merely files created.
When validation fails, record the failure and the repair before marking the
phase complete. The plan must remain understandable to an engineer who only has
the repository and the plan.

The active plan for this milestone is:

`execplans/onboarding-strava-vertical-slice.md`
