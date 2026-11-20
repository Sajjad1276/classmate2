# ClassMate 2

Multi-school Telegram classroom memory — **button-first UX**.

Students and staff use reply keyboards and inline panels. No slash-command memorization required. Only `/start` opens the menu.

## UX principles

- Persistent reply keyboard as home
- Inline panels for choices (class, subject, day, record type)
- Multi-step FSM wizards for create flows
- Role-aware extra rows (approver / manager / platform admin)
- Group link: paste the connect code as a normal message

## Quick start

```bash
cp .env.example .env
# Set BOT_TOKEN, BOOTSTRAP_ADMIN_IDS and a strong POSTGRES_PASSWORD in .env.
docker compose up --build
# The one-shot migrate service runs alembic upgrade head before bot/worker start.
```

## Main flows (all via buttons)

1. `/start` → welcome message and the main menu
2. **🔑 ورود با کد دعوت** → student joins a class
3. **🏠 داشبورد من** → upcoming homework, exams and announcements
4. **➕ ثبت اطلاعات** → homework / exam / daily report wizard
5. Approvers tap ✅ / ❌ on the group card
6. **📤 ارسال تکلیف** → student submits text, a document or a photo
7. **📝 بررسی پاسخ‌ها** → class manager reviews submissions and sends feedback
8. **📊 نمرات من / 🟢 حضور من** → student learning history
9. **🗳 نظرسنجی‌ها** → polls and short quizzes
10. **👨‍🏫 ارزیابی کلاس** → manager records grades, attendance and polls
11. **🆘 پشتیبانی** → direct support via `@Mizanup`

## Stack

Python 3.12 · aiogram 3 · SQLAlchemy 2 · PostgreSQL · Redis · Docker

## Phase two learning workflows

- Student homework submissions with text, documents and photos
- Personal grades with feedback and calculated average
- Attendance history with present / absent / late / excused statuses
- Class polls and short quizzes with one-response-per-user protection
- Manager tools for entering grades, attendance and creating polls
- Semantic button styling: blue for navigation, green for confirmation, yellow for follow-up and red for cancellation
- Dedicated support entry point: `@Mizanup`

## Database migrations and delivery guarantees

- Revision `0001` uses a frozen schema snapshot in `src/classmate/db/schema_v0001.py`. Do not edit that snapshot; future schema changes require a new forward Alembic revision.
- Historical baseline revisions are intentionally non-destructive on downgrade. The original bootstrap did not record per-object ownership, so automatically dropping tables/indexes could delete production data. For schema rollback, restore a verified database backup and deploy a compatible application version.
- Telegram approval notifications and homework feedback are inserted into `telegram_outbox` in the same transaction as their business record. The worker leases rows, revalidates the target and record state, then delivers messages. Idempotency keys prevent duplicate queue rows.
- Outbox delivery is at-least-once, not exactly-once: a process crash after Telegram accepts a message but before the database records `SENT` can cause a retry. The Telegram send API does not provide a transaction shared with PostgreSQL.
- GitHub Actions validates fresh SQLite migrations, runs PostgreSQL migrations against an ephemeral PostgreSQL service, tests PostgreSQL `FOR UPDATE SKIP LOCKED` behavior, runs Ruff and strictly type-checks the outbox service.

## Welcome and help UX

The `/start` message introduces the dashboard, class invitation flow, homework submission, grades, attendance, polls and support. The **❓ راهنمای استفاده** button contains the full role-aware guide, including the manager-only review and evaluation tools.

After deploying phase two, run:

```bash
alembic upgrade head
```

## Performance and production notes

- Migration `0004` adds composite indexes for memberships, announcements, attendance, polls and reminder delivery.
- Authorization checks are evaluated against current active memberships and active class/school state; permission decisions are not kept in a stale in-process cache.
- Reminder recipient preferences are loaded with one indexed outer join instead of one query per member.
- Inline navigation edits existing panels where possible, and keyboard removal is silent so the chat stays compact.
- For production, run behind a process supervisor and prefer Telegram webhook mode when a stable HTTPS endpoint is available; polling remains the safe local-development default.
- PostgreSQL is bound to localhost for local administration only; Redis is internal to the Compose network. Do not publish either service publicly. Use a long, URL-safe `POSTGRES_PASSWORD` and a secret manager for production.
- Compose runs a one-shot migration service and blocks bot/worker startup until `alembic upgrade head` succeeds. Alembic requires `DATABASE_URL`, but does not require `BOT_TOKEN`.

Built to stay ahead of ClassMate 1 — cleaner architecture and a real product UI.
