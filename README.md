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

## Welcome and help UX

The `/start` message introduces the dashboard, class invitation flow, homework submission, grades, attendance, polls and support. The **❓ راهنمای استفاده** button contains the full role-aware guide, including the manager-only review and evaluation tools.

After deploying phase two, run:

```bash
alembic upgrade head
```

## Performance and production notes

- Migration `0004` adds composite indexes for memberships, announcements, attendance, polls and reminder delivery.
- Role and class lists use a short in-process cache to reduce repeated permission queries; membership changes naturally expire within seconds.
- Reminder recipient preferences are loaded with one indexed outer join instead of one query per member.
- Inline navigation edits existing panels where possible, and keyboard removal is silent so the chat stays compact.
- For production, run behind a process supervisor and prefer Telegram webhook mode when a stable HTTPS endpoint is available; polling remains the safe local-development default.

Built to stay ahead of ClassMate 1 — cleaner architecture and a real product UI.
