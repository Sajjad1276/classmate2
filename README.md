# ClassMate 2

**A cleaner, more maintainable multi-school Telegram classroom memory system.**

ClassMate 2 stores daily reports, homework, exams, timetables and reminders with human approval — without AI in the MVP.

## Why ClassMate 2 is ahead of v1

| Area | ClassMate 1 | ClassMate 2 |
|------|-------------|-------------|
| Handlers | One huge file | Split routers by domain |
| Business logic | Mixed in handlers | Services layer |
| DI | Manual sessions | `DbSessionMiddleware` |
| Roles / statuses | Strings | Python `StrEnum` |
| FSM | Memory only | Redis-ready + Memory fallback |
| Worker | Basic | Lease + retry + recovery |
| Tests | Minimal | Users + enums + growing |
| Typing / CI | Partial | ruff + pytest + mypy-ready |

## Stack

- Python 3.12+, aiogram 3, SQLAlchemy 2 async, asyncpg, Alembic
- PostgreSQL, Redis (FSM in production)
- Docker Compose

## Quick start

```bash
cp .env.example .env
# BOT_TOKEN + BOOTSTRAP_ADMIN_IDS (your Telegram numeric id)
docker compose up --build
```

Migrations:

```bash
alembic upgrade head
```

## First-time flow

1. `/start` in private
2. `/school_new نام مدرسه | ۱۴۰۵-۱۴۰۶`
3. `/class_new شناسه_مدرسه | دهم | ۱۰۴ | دهم ۱۰۴`
4. `/class_code شناسه_کلاس` → get connect + join codes
5. In group: `/connect کد`
6. Students: `/join کد` in private
7. `/subject_add ریاضی`
8. `/new_homework ریاضی | تمرین | مسائل ۱-۵ | ۱۴۰۵/۰۸/۰۱`
9. Approver taps تأیید on the group message
10. `/homework` / `/exams` / `/today` / `/week`

## Commands (core)

- Onboarding: `/school_new`, `/class_new`, `/class_code`, `/connect`, `/join`, `/classes`
- Records: `/subject_add`, `/new_homework`, `/new_exam`, `/new_report`, `/timetable_set`
- Queries: `/homework`, `/exams`, `/today`, `/week`, `/pending`
- Approval: inline buttons + `/pending`

## Layout

```
src/classmate/
├── core/          # config, enums, logging
├── db/            # models, session
├── domain/        # pure date rules
├── services/      # permissions, records, notifications, users
├── bot/
│   ├── handlers/  # start, onboarding, records, queries, approvals, common
│   ├── middlewares.py
│   └── main.py
└── worker/        # durable reminder delivery
```

## Principles

1. Thin handlers — services own business rules.
2. Enums for roles and record lifecycle.
3. Never trust a class_id without membership/role check.
4. Approved-only lists; pending is separate.
5. Reminders are durable jobs with leasing.

Built to stay one step ahead of ClassMate 1.
