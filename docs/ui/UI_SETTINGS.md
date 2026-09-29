# Settings (`/settings`)

Configuration **status** — never secrets. Data: `GET /api/settings`, `GET /api/me`, `GET /api/preferences`.

| Section | Content | Actions |
|---|---|---|
| Appearance | Theme: System · Light · Dark | select (applies immediately; stored per browser) |
| Account | Signed-in profile name; whether sign-in is required or this is loopback-only local mode | Sign out (only when sign-in is required) |
| Job Preferences | Current keywords, location, posted-within, jobs per run | Link → Job Alerts |
| Resume | Master resume path, roles count, skills count, stated years, sections | Link → Master Resume |
| Integrations | Job sources and services (Configured / Not configured), LLM mode + provider chain | — |
| System | Security posture: secrets stay server-side; sign-in required vs local mode | Link → Logs |

Rules: booleans and labels only — no keys, tokens, sheet ids or hashes. No fake toggles.
