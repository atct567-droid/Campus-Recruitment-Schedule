# Repository working instructions

- Preserve the original phase-1 tests and anonymous requests transport behavior.
- Reuse `RecruitmentClient`; do not bypass proxies, disable TLS, or infer live contracts from synthetic fixtures.
- Synthetic data must retain its provenance in SQLite, exports, reports, and calendar plans.
- Real collection requires `ENABLE_REAL_COLLECTION=true` and an explicitly verified mapping configuration.
- Calendar writes require the explicit `--enable-real-sync` flag and `ENABLE_REAL_CALENDAR_SYNC=true`; default is dry-run.
- Service-account JSON is read only from GOOGLE_CREDENTIALS or a private file; never log, commit, cache, or upload keys/tokens. GOOGLE_CALENDAR_ID comes from Actions Variables and is the sole API target.
- Preflight the target accessRole before writing. Scheduled writes additionally require ENABLE_SCHEDULED_CALENDAR_SYNC=true. Cache only opaque calendar scopes and public managed-event mappings, never other schedules or credentials.
- Never infer cancellation from a missing, failed, or partial response. Only an explicitly mapped trusted cancellation status may cancel an event.
- Never invent missing event times. Use `[start,end)` for conflicts; exclude ambiguous intervals from calendars.
- Personal activities and OAuth material belong in ignored private storage, never public reports, calendars, commits, or artifacts.
- Run `python -m pytest -q` and `ruff check src tests scripts` after meaningful changes. Include offline end-to-end and independent ICS parsing validation.
- Work on a feature branch, make reviewable commits, and record verified versus unverified outcomes in `docs/progress.md`.
