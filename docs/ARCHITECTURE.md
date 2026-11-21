# Architecture — ClassMate 2

## Scope
ClassMate 2 is the learner-facing bot. It handles class invitation, educational queries, homework submission, learner history and support. Platform administration, school/class setup, content authoring, approvals, reviews, group connection and staff evaluation are intentionally out of scope and belong in `classmatework`.

## Processes
- `classmate.bot.main` — aiogram polling + learner routers
- `classmate.worker.main` — learner notification leasing and Telegram delivery
- PostgreSQL — source of truth
- Redis — FSM storage in production

## Layers
- **bot/handlers** — learner I/O only
- **services** — read-only educational queries and learner submissions
- **domain** — pure helpers (dates)
- **db** — SQLAlchemy models + session

## Access boundary
Access is limited to active student memberships in active classes. No platform-admin, school-admin or class-manager authorization path is evaluated by the active application.

## Historical schema
Alembic migrations are retained as a compatibility record. Legacy management tables and columns are not used by the active ClassMate 2 code and should be migrated only by the separate management repository when its ownership model is ready.
