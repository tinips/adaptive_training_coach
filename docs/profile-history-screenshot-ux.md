# Profile, history, and screenshot UX

## Decisions

- Telegram uses an inline-message navigation model, not native screens. The
  Profile root has **Done**; every nested section has **Back** to its immediate
  parent. There are no modal, deep-link, or browser-history affordances to
  preserve.
- Availability is the only profile edit held as an unconfirmed draft. Leaving
  its review now asks whether to discard the draft. Confirmed profile fields are
  saved immediately and therefore need no discard warning.
- Workout history keeps its existing daily/weekly bucketing and metrics. Its
  chart now states the bucket and unit, has tap/focus details and a text
  equivalent, reduces crowded labels, and exposes loading and empty states.
- A photo sent to the chat is the one workout-entry route. The former keyboard
  and `/add_workout` prompt duplicated that flow and have been removed.

## Duplicate policy

Screenshot imports have no provider activity ID. After the athlete confirms an
extraction, the importer hashes the discipline, start timestamp, duration, and
distance into an owner-scoped source identity. Repeated confirmation, retries,
or re-sending the same extracted workout therefore resolve to the same workout.
Near matches are intentionally not auto-merged because they can be legitimate
separate sessions.
