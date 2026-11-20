"""Regression tests for authorization, voting and reminder integrity."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from classmate.core.enums import EntityType, OutboxStatus, RecordStatus, ReminderStatus, Role
from classmate.db.models import (
    Attendance,
    ClassGroup,
    ClassMembership,
    Grade,
    Homework,
    Poll,
    PollOption,
    PollResponse,
    ReminderJob,
    School,
    TelegramOutbox,
    User,
)
from classmate.services.phase1 import archive_record
from classmate.services.phase2 import answer_poll, save_submission
from classmate.services.permissions import (
    activate_student_membership,
    connect_group_by_code,
    hash_code,
    user_can_access_class,
)
from classmate.services.records import create_homework
from classmate.services.outbox import enqueue_telegram_message
from classmate.services.users import ensure_user


@pytest.mark.asyncio
async def test_rejoining_with_student_invite_never_restores_manager_role(world):
    session = world["session"]
    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == world["manager"].id,
        )
    )
    membership.is_active = False
    await session.flush()

    restored = await activate_student_membership(
        session, world["class"].id, world["manager"].id
    )

    assert restored.is_active is True
    assert restored.role == Role.STUDENT


@pytest.mark.asyncio
async def test_revoked_membership_cannot_access_class(world):
    session = world["session"]
    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == world["student"].id,
        )
    )
    membership.is_active = False
    await session.flush()

    assert not await user_can_access_class(
        session, world["student"], world["class"].id
    )


@pytest.mark.asyncio
async def test_inactive_user_cannot_be_resolved(world):
    user = world["student"]
    user.is_active = False
    await world["session"].flush()

    with pytest.raises(PermissionError, match="غیرفعال"):
        await ensure_user(
            world["session"],
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name,
            private_chat_started=True,
        )


@pytest.mark.asyncio
async def test_poll_rejects_foreign_option_and_non_member(world):
    session = world["session"]
    first = Poll(
        class_id=world["class"].id,
        question="نظرسنجی اول",
        created_by=world["manager"].id,
    )
    second = Poll(
        class_id=world["class"].id,
        question="نظرسنجی دوم",
        created_by=world["manager"].id,
    )
    session.add_all([first, second])
    await session.flush()

    first_option = PollOption(poll_id=first.id, label="الف", position=0)
    foreign_option = PollOption(poll_id=second.id, label="ب", position=0)
    outsider = User(telegram_id=1004, full_name="کاربر خارج از کلاس")
    session.add_all([first_option, foreign_option, outsider])
    await session.flush()

    assert not await answer_poll(
        session, first.id, world["student"].id, foreign_option.id
    )
    assert await answer_poll(session, first.id, world["student"].id, first_option.id)
    assert not await answer_poll(session, first.id, outsider.id, first_option.id)

    responses = list(
        (
            await session.scalars(
                select(PollResponse).where(PollResponse.poll_id == first.id)
            )
        ).all()
    )
    assert len(responses) == 1
    assert responses[0].option_id == first_option.id


@pytest.mark.asyncio
async def test_submission_requires_active_class_membership(world):
    session = world["session"]
    homework = Homework(
        class_id=world["class"].id,
        subject_id=world["subject"].id,
        title="تکلیف",
        description="توضیح",
        due_on=date.today() + timedelta(days=2),
        status=RecordStatus.APPROVED,
        created_by=world["manager"].id,
    )
    session.add(homework)
    await session.flush()

    outsider = User(telegram_id=2005, full_name="کاربر خارج از کلاس")
    session.add(outsider)
    await session.flush()

    with pytest.raises(PermissionError, match="عضو فعال"):
        await save_submission(
            session,
            homework.id,
            outsider.id,
            text="پاسخ کاربر خارج از کلاس",
        )


@pytest.mark.asyncio
async def test_archiving_homework_cancels_pending_reminders(world):
    session = world["session"]
    homework = Homework(
        class_id=world["class"].id,
        subject_id=world["subject"].id,
        title="تکلیف آرشیوی",
        description="توضیح",
        due_on=date.today() + timedelta(days=2),
        status=RecordStatus.APPROVED,
        created_by=world["manager"].id,
    )
    session.add(homework)
    await session.flush()

    reminder = ReminderJob(
        class_id=world["class"].id,
        entity_type=EntityType.HOMEWORK.value,
        entity_id=homework.id,
        target_type="private",
        target_id=world["student"].telegram_id,
        message_text="یادآوری",
        scheduled_at=datetime.now(timezone.utc),
        status=ReminderStatus.PENDING,
        dedupe_key=f"test-homework-{homework.id}",
    )
    outbox = await enqueue_telegram_message(
        session,
        target_type="private",
        target_chat_id=world["student"].telegram_id,
        recipient_user_id=world["student"].id,
        class_id=world["class"].id,
        entity_type="homework",
        entity_id=homework.id,
        expected_record_status="APPROVED",
        text="بازخورد تکلیف",
        idempotency_key=f"feedback:{homework.id}",
    )
    session.add(reminder)
    await session.flush()

    ok, _ = await archive_record(
        session, EntityType.HOMEWORK.value, homework.id, world["manager"]
    )

    assert ok is True
    assert homework.status == RecordStatus.CANCELLED
    assert reminder.status == ReminderStatus.CANCELLED
    assert outbox.status == OutboxStatus.CANCELLED


@pytest.mark.asyncio
async def test_homework_rejects_subject_not_linked_to_class(world):
    session = world["session"]
    second_class = ClassGroup(
        academic_year_id=world["year"].id,
        name="دهم ۱۰۲",
        grade="دهم",
        section="۱۰۲",
    )
    session.add(second_class)
    await session.flush()

    with pytest.raises(ValueError, match="متعلق به این کلاس نیست"):
        await create_homework(
            session,
            second_class.id,
            world["subject"],
            "نباید ثبت شود",
            "درس به کلاس دوم وصل نشده است",
            None,
            world["manager"].id,
        )


@pytest.mark.asyncio
async def test_stale_membership_cannot_access_inactive_class(world):
    session = world["session"]
    world["class"].is_active = False
    await session.flush()

    assert not await user_can_access_class(
        session, world["student"], world["class"].id
    )



@pytest.mark.asyncio
async def test_telegram_outbox_enqueue_is_idempotent_and_detects_key_collisions(world):
    session = world["session"]
    world["class"].group_chat_id = -1001234567890
    args = {
        "target_type": "group",
        "target_chat_id": world["class"].group_chat_id,
        "class_id": world["class"].id,
        "entity_type": "homework",
        "entity_id": 321,
        "expected_record_status": "PENDING",
        "text": "درخواست جدید برای بررسی",
        "action_type": "homework",
        "action_id": 321,
        "idempotency_key": "approval:homework:321",
    }

    first = await enqueue_telegram_message(session, **args)
    second = await enqueue_telegram_message(session, **args)

    assert first.id == second.id
    assert first.status == OutboxStatus.PENDING
    assert await session.scalar(
        select(TelegramOutbox.id).where(
            TelegramOutbox.idempotency_key == "approval:homework:321"
        )
    ) == first.id

    with pytest.raises(ValueError, match="reused with a different payload"):
        await enqueue_telegram_message(
            session, **{**args, "text": "متن دیگری با همان کلید"}
        )



@pytest.mark.asyncio
async def test_legacy_join_command_does_not_restore_a_revoked_manager_role(world):
    from classmate.bot.handlers.onboarding import join_class

    session = world["session"]
    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == world["manager"].id,
        )
    )
    membership.is_active = False
    code = "ABCD2345"
    world["class"].member_join_code_hash = hash_code(code)
    world["class"].member_join_code_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    await session.flush()

    sent = []

    async def answer(message):
        sent.append(message)

    message = SimpleNamespace(
        text=f"/join {code}",
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(
            id=world["manager"].telegram_id,
            username=world["manager"].username,
            full_name=world["manager"].full_name,
            first_name=world["manager"].full_name,
        ),
        answer=answer,
    )

    await join_class(message, session)

    assert membership.is_active is True
    assert membership.role == Role.STUDENT
    assert sent and "ملحق شدید" in sent[0]


@pytest.mark.asyncio
async def test_group_link_requires_class_manager_and_both_telegram_admins(world):
    code = "WXYZ2345"
    cls = world["class"]
    cls.group_link_code_hash = hash_code(code)
    cls.group_link_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    session = world["session"]

    with pytest.raises(PermissionError, match="مدیر همین کلاس"):
        await connect_group_by_code(
            session,
            code=code,
            chat_id=-1001234567001,
            chat_title="گروه هدف",
            actor=world["student"],
            bot_is_admin=True,
            actor_is_admin=True,
        )

    with pytest.raises(PermissionError, match="مدیر یا مالک گروه"):
        await connect_group_by_code(
            session,
            code=code,
            chat_id=-1001234567001,
            chat_title="گروه هدف",
            actor=world["manager"],
            bot_is_admin=True,
            actor_is_admin=False,
        )

    with pytest.raises(PermissionError, match="ربات باید"):
        await connect_group_by_code(
            session,
            code=code,
            chat_id=-1001234567001,
            chat_title="گروه هدف",
            actor=world["manager"],
            bot_is_admin=False,
            actor_is_admin=True,
        )


@pytest.mark.asyncio
async def test_group_link_cannot_overwrite_existing_class_or_group_binding(world):
    from classmate.db.models import ClassGroup

    session = world["session"]
    cls = world["class"]
    code = "EFGH2345"
    cls.group_link_code_hash = hash_code(code)
    cls.group_link_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    cls.group_chat_id = -1001234567002

    with pytest.raises(ValueError, match="از قبل به گروه دیگری"):
        await connect_group_by_code(
            session,
            code=code,
            chat_id=-1001234567003,
            chat_title="گروه جدید",
            actor=world["manager"],
            bot_is_admin=True,
            actor_is_admin=True,
        )

    other = ClassGroup(
        academic_year_id=world["year"].id,
        name="دهم ۱۰۲",
        grade="دهم",
        section="۱۰۲",
        group_chat_id=-1001234567004,
    )
    session.add(other)
    await session.flush()
    cls.group_chat_id = None
    code2 = "JKLM2345"
    cls.group_link_code_hash = hash_code(code2)
    cls.group_link_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    with pytest.raises(ValueError, match="قبلاً به کلاس دیگری"):
        await connect_group_by_code(
            session,
            code=code2,
            chat_id=-1001234567004,
            chat_title="گروه تکراری",
            actor=world["manager"],
            bot_is_admin=True,
            actor_is_admin=True,
        )


@pytest.mark.asyncio
async def test_group_link_rejects_expired_code_and_inactive_school(world):
    session = world["session"]
    cls = world["class"]
    code = "PQRS2345"
    cls.group_link_code_hash = hash_code(code)
    cls.group_link_code_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    with pytest.raises(ValueError, match="منقضی"):
        await connect_group_by_code(
            session,
            code=code,
            chat_id=-1001234567005,
            chat_title="گروه",
            actor=world["manager"],
            bot_is_admin=True,
            actor_is_admin=True,
        )

    cls.group_link_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    world["school"].is_active = False
    assert not await user_can_access_class(session, world["student"], cls.id)
    with pytest.raises(ValueError, match="معتبر نیست"):
        await connect_group_by_code(
            session,
            code=code,
            chat_id=-1001234567005,
            chat_title="گروه",
            actor=world["manager"],
            bot_is_admin=True,
            actor_is_admin=True,
        )



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            "/timetable_set شنبه | 0 | ریاضی | 08:00-08:45",
            "شماره زنگ باید بین ۱ تا ۲۰ باشد.",
        ),
        (
            "/timetable_set شنبه | 1 | ریاضی | 09:00-08:45",
            "ساعت پایان باید بعد از ساعت شروع باشد.",
        ),
    ],
)
async def test_legacy_timetable_command_rejects_invalid_slots(world, command, expected):
    from classmate.bot.handlers.records import timetable_set

    replies = []

    async def answer(text):
        replies.append(text)

    message = SimpleNamespace(text=command, answer=answer)
    await timetable_set(message, world["session"])

    assert replies == [expected]


@pytest.mark.asyncio
async def test_student_enrollment_rejects_inactive_class_or_school(world):
    session = world["session"]
    world["school"].is_active = False

    with pytest.raises(ValueError, match="فعال نیست"):
        await activate_student_membership(
            session, world["class"].id, world["student"].id
        )


@pytest.mark.asyncio
async def test_group_link_rejects_code_without_expiration(world):
    session = world["session"]
    cls = world["class"]
    code = "TUVW2345"
    cls.group_link_code_hash = hash_code(code)
    cls.group_link_code_expires_at = None

    with pytest.raises(ValueError, match="فاقد تاریخ انقضا"):
        await connect_group_by_code(
            session,
            code=code,
            chat_id=-1001234567099,
            chat_title="گروه",
            actor=world["manager"],
            bot_is_admin=True,
            actor_is_admin=True,
        )


@pytest.mark.asyncio
async def test_school_creation_rejects_unknown_timezone(world, monkeypatch):
    from classmate.bot.handlers import onboarding

    async def admin_user(*args, **kwargs):
        return SimpleNamespace(id=world["manager"].id, is_platform_admin=True, is_active=True)

    monkeypatch.setattr(onboarding, "_user", admin_user)
    replies = []

    async def answer(text):
        replies.append(text)

    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        text="/school_new مدرسه آزمایشی | ۱۴۰۶-۱۴۰۷ | Invalid/Not_A_Zone",
        from_user=SimpleNamespace(id=world["manager"].telegram_id),
        answer=answer,
    )

    await onboarding.school_new(message, world["session"])

    assert replies == ["منطقه زمانی نامعتبر است. نمونه معتبر: Asia/Tehran"]
    assert await world["session"].scalar(
        select(School.id).where(School.name == "مدرسه آزمایشی")
    ) is None



@pytest.mark.asyncio
async def test_reminder_worker_cancels_delivery_for_inactive_school(world):
    from classmate.core.enums import ReminderStatus
    from classmate.db.models import ReminderJob
    from classmate.services.records import create_homework
    from classmate.worker.main import _job_is_deliverable

    session = world["session"]
    homework = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "تکلیف یادآوری",
        "شرح",
        date.today() + timedelta(days=1),
        world["student"].id,
    )
    homework.status = RecordStatus.APPROVED
    world["class"].group_chat_id = -1001234567011
    session.add(
        ReminderJob(
            class_id=world["class"].id,
            entity_type="homework",
            entity_id=homework.id,
            target_type="group",
            target_id=world["class"].group_chat_id,
            message_text="یادآوری",
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            status=ReminderStatus.PENDING,
            dedupe_key="inactive-school-reminder",
        )
    )
    await session.flush()
    job = await session.scalar(
        select(ReminderJob).where(
            ReminderJob.dedupe_key == "inactive-school-reminder"
        )
    )

    world["school"].is_active = False
    assert await _job_is_deliverable(session, job) is False


@pytest.mark.asyncio
async def test_workers_reclaim_processing_rows_with_missing_lease(world):
    from classmate.worker.main import _claim_outbox_ids, claim_jobs

    session = world["session"]
    now = datetime.now(timezone.utc)
    reminder = ReminderJob(
        class_id=world["class"].id,
        entity_type="homework",
        entity_id=1,
        target_type="group",
        target_id=-1001234567012,
        message_text="یادآوری",
        scheduled_at=now - timedelta(minutes=1),
        status=ReminderStatus.PROCESSING,
        locked_at=None,
        dedupe_key="orphaned-reminder-lease",
    )
    outbox = TelegramOutbox(
        target_type="group",
        target_chat_id=-1001234567012,
        class_id=world["class"].id,
        text="پیام صف",
        scheduled_at=now - timedelta(minutes=1),
        status=OutboxStatus.PROCESSING,
        locked_at=None,
        idempotency_key="orphaned-outbox-lease",
    )
    session.add_all([reminder, outbox])
    await session.flush()

    claimed_reminders = await claim_jobs(session)
    claimed_outbox_ids = await _claim_outbox_ids(session)

    assert reminder in claimed_reminders
    assert reminder.status == ReminderStatus.PROCESSING
    assert reminder.locked_at is not None
    assert outbox.id in claimed_outbox_ids
    assert outbox.status == OutboxStatus.PROCESSING
    assert outbox.locked_at is not None



@pytest.mark.asyncio
async def test_student_enrollment_rejects_inactive_user(world):
    world["student"].is_active = False
    with pytest.raises(PermissionError, match="فعال نیست"):
        await activate_student_membership(
            world["session"], world["class"].id, world["student"].id
        )



@pytest.mark.asyncio
async def test_personal_records_hide_data_after_membership_revocation(world):
    from classmate.services.phase2 import student_attendance, student_grades

    session = world["session"]
    student = world["student"]
    class_group = world["class"]
    session.add(
        Grade(
            class_id=class_group.id,
            student_id=student.id,
            subject_id=world["subject"].id,
            source_type="manual",
            score=18,
            max_score=20,
            graded_by=world["manager"].id,
        )
    )
    session.add(
        Attendance(
            class_id=class_group.id,
            student_id=student.id,
            attendance_date=date.today(),
            status="present",
            marked_by=world["manager"].id,
        )
    )
    await session.flush()

    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == class_group.id,
            ClassMembership.user_id == student.id,
        )
    )
    membership.is_active = False
    await session.flush()

    assert await student_grades(session, student.id, [class_group.id]) == []
    assert await student_attendance(session, student.id, [class_group.id]) == []


@pytest.mark.asyncio
async def test_personal_records_hide_data_when_school_is_inactive(world):
    from classmate.services.phase2 import student_attendance, student_grades

    session = world["session"]
    student = world["student"]
    class_group = world["class"]
    session.add(
        Grade(
            class_id=class_group.id,
            student_id=student.id,
            subject_id=world["subject"].id,
            source_type="manual",
            score=19,
            max_score=20,
            graded_by=world["manager"].id,
        )
    )
    session.add(
        Attendance(
            class_id=class_group.id,
            student_id=student.id,
            attendance_date=date.today(),
            status="present",
            marked_by=world["manager"].id,
        )
    )
    world["school"].is_active = False
    await session.flush()

    assert await student_grades(session, student.id, [class_group.id]) == []
    assert await student_attendance(session, student.id, [class_group.id]) == []


@pytest.mark.parametrize(
    ("score", "max_score", "valid"),
    [
        (18.0, 20.0, True),
        (float("nan"), 20.0, False),
        (float("inf"), 20.0, False),
        (18.0, float("inf"), False),
        (-1.0, 20.0, False),
        (21.0, 20.0, False),
    ],
)
def test_grade_values_reject_non_finite_and_out_of_range_numbers(
    score, max_score, valid
):
    from classmate.services.phase2 import validate_grade_values

    if valid:
        validate_grade_values(score, max_score)
    else:
        with pytest.raises(ValueError):
            validate_grade_values(score, max_score)



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "command_text", "expected"),
    [
        (
            "new_homework",
            "/new_homework ریاضی | تمرین | حل شود",
            "فقط مدیر کلاس می‌تواند تکلیف ثبت کند.",
        ),
        (
            "new_exam",
            "/new_exam ریاضی | آزمون | فصل ۱ | ۱۴۰۶/۰۱/۰۱ ۱۰:۰۰",
            "فقط مدیر کلاس می‌تواند امتحان ثبت کند.",
        ),
        (
            "new_report",
            "/new_report گزارش روزانه",
            "فقط مدیر کلاس می‌تواند گزارش روزانه ثبت کند.",
        ),
    ],
)
async def test_legacy_record_commands_require_class_manager(
    world, monkeypatch, handler_name, command_text, expected
):
    from classmate.bot.handlers import records

    async def fake_context(_message, _session, class_id=None):
        return (world["class"], world["year"], world["school"]), world["student"]

    monkeypatch.setattr(records, "_ctx", fake_context)
    replies = []

    async def answer(text):
        replies.append(text)

    message = SimpleNamespace(
        text=command_text,
        chat=SimpleNamespace(type="group"),
        answer=answer,
    )
    await getattr(records, handler_name)(message, world["session"])

    assert replies == [expected]
