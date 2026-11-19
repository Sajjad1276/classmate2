# ClassMate 2

**A cleaner, more maintainable multi-school Telegram classroom memory system.**

ClassMate 2 is the next evolution of the ClassMate idea: structured storage of daily reports, homework, exams, timetables and reminders — with human approval — delivered through Telegram.

This version is built from the ground up with production-grade practices that were missing or incomplete in the first version.

## Why ClassMate 2?

Compared to the original ClassMate MVP:

| Area | ClassMate 1 | ClassMate 2 |
|------|-------------|-------------|
| Handlers | One 70k+ file | Split routers by domain |
| Business logic | Mixed in handlers | Dedicated services layer |
| Dependency injection | Manual session management | Middleware-based DI |
| Roles & statuses | Plain strings | Proper Python Enums |
| FSM storage | Memory only | Redis-ready (Memory fallback) |
| Type safety | Partial | Strong typing + mypy-ready |
| Testing | Minimal | Service + permission tests from day 1 |
| Project structure | Flat | Clear layers (core / db / domain / services / bot) |

## Stack

- Python 3.12+
- aiogram 3.x
- SQLAlchemy 2 (async) + asyncpg
- Alembic
- PostgreSQL
- Redis (for FSM in production)
- Docker Compose
- pytest + ruff + mypy

## Quick start

```bash
cp .env.example .env
# Set BOT_TOKEN and BOOTSTRAP_ADMIN_IDS
docker compose up --build
```

## Project layout

```
src/classmate/
├── core/           # config, enums, logging
├── db/             # models, session, base
├── domain/         # pure business rules (dates, normalization)
├── services/       # use-cases and permissions
├── bot/
│   ├── handlers/   # one router per domain
│   ├── middlewares.py
│   ├── keyboards.py
│   └── main.py
└── worker/         # reminder delivery process
```

## Development principles

1. **Thin handlers** — parse input, call service, reply.
2. **Business logic lives in services** — testable without Telegram.
3. **Enums everywhere** for roles and record statuses.
4. **Explicit tenant isolation** — never trust a user-supplied class_id without membership check.
5. **Durable reminders** — database-backed jobs with leasing.

## Status

Foundation is being built. Core onboarding, records, approval flow and reminders will be added incrementally with tests.

---

Built to be one step ahead.
