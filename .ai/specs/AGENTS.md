# Spec Maintenance Rules

Specs live in `.ai/specs/`. They are behavioral contracts — not code docs.

## When to create a spec

- New command or handler added to the bot
- New user-facing setting or toggle
- New infrastructure concern (auth, rate limiting, persistence)

## When to update a spec

- Behavior changes (invariant or test scenario no longer matches code)
- Bug fixed that was covered by a spec
- Feature removed → mark `status: obsolete`

## When to update `validated`

Only when you have **verified** the behavior matches the spec (ran the bot, ran tests, or read the code carefully). Never update `validated` without verification.

## ID schema

| Prefix | Use |
|--------|-----|
| SVC-   | Core service pipelines (voice, text, photo) |
| FEA-   | User-facing features (sessions, settings, watch, approval) |
| INF-   | Infrastructure (auth, rate limiting, health, credentials) |

## Format

See the spec-writing skill or any existing spec in this directory.
~25 lines body · English · Invariants required · one concern per file.

## Index

| ID | Title | Status |
|----|-------|--------|
| SVC-001 | Voice pipeline | active |
| SVC-002 | Text message handling | active |
| SVC-003 | Photo message handling | active |
| FEA-001 | Session management | active |
| FEA-002 | User settings & persistence | active |
| FEA-003 | Tool approval flow | active |
| FEA-004 | Watch mode | active |
| FEA-005 | Compact session | active |
| FEA-006 | Automations | active |
| INF-001 | Rate limiting | active |
| INF-002 | Authorization | active |
| INF-003 | Credential management | active |
| INF-004 | Health check | active |
