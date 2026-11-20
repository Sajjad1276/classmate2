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
# BOT_TOKEN + BOOTSTRAP_ADMIN_IDS
docker compose up --build
alembic upgrade head
```

## Main flows (all via buttons)

1. `/start` → main menu
2. **مدیریت سامانه** → ساخت مدرسه / کلاس (admin)
3. **مدیریت کلاس** → کد اتصال و عضویت، درس، برنامه
4. In the class group: paste the **connect code**
5. **عضویت با کد** → student joins
6. **ثبت جدید** → تکلیف / امتحان / گزارش (wizard)
7. Approvers tap ✅ / ❌ on the group card
8. **تکالیف / امتحانات / برنامه / گزارش‌های امروز** to view

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

After deploying phase two, run:

```bash
alembic upgrade head
```

Built to stay ahead of ClassMate 1 — cleaner architecture and a real product UI.
