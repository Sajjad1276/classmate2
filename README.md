# ClassMate 2

Learner-facing Telegram classroom companion. ClassMate 2 now focuses exclusively on student education workflows; all platform, school, class, content, approval and administrative operations belong in the separate `classmatework` repository.

## UX principles
- Persistent learner reply keyboard as home
- Inline panels for class and educational choices
- Student onboarding through an invitation code
- Read-only educational views plus homework submission

## Quick start
```bash
cp .env.example .env
# Set BOT_TOKEN and POSTGRES_PASSWORD in .env.
docker compose up --build
```

## Learner flows
1. `/start` → learner menu
2. **🔑 ورود با کد دعوت** → join an existing class
3. **🏠 داشبورد من** → upcoming homework, exams and announcements
4. **🎓 بخش آموزشی** → homework, exams, timetable, daily reports, grades, attendance and polls
5. **📤 ارسال تکلیف** → submit text, a document or a photo
6. **🆘 پشتیبانی** → direct support via `@Mizanup`

## Scope boundary
This repository intentionally contains no platform-admin, school-admin, class-manager, content-authoring, approval/review, group-connection, scheduling-authoring or evaluation-management flows. Those capabilities are being developed independently in `classmatework`.

## Stack
Python 3.12 · aiogram 3 · SQLAlchemy 2 · PostgreSQL · Redis · Docker

## Database and delivery
The database migrations remain forward-compatible historical records. Existing management-related database tables/columns are preserved by migrations for safe deployment and data retention, but they are no longer mapped to active application behavior in ClassMate 2.

Transactional learner notifications continue to use the Telegram outbox and the worker's at-least-once delivery guarantees.
