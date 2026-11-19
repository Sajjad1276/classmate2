# Architecture — ClassMate 2

## Processes

- `classmate.bot.main` — aiogram polling + routers
- `classmate.worker.main` — reminder leasing and Telegram delivery
- PostgreSQL — source of truth
- Redis — FSM storage in production

## Layers

- **bot/handlers** — I/O only
- **services** — use-cases, permissions, scheduling
- **domain** — pure helpers (dates)
- **db** — SQLAlchemy models + session

## DI

`DbSessionMiddleware` injects `session: AsyncSession` into every handler. Handlers must not open their own sessions.

## Tenant isolation

School → AcademicYear → Class. Access always goes through membership or school admin role. Platform admin is bootstrap-only.

## Reminders

`ReminderJob` rows with `dedupe_key`, `FOR UPDATE SKIP LOCKED`, lease recovery, exponential retry.
