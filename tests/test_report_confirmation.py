"""Daily-report submission requires an explicit private confirmation."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from classmate.bot.handlers.wizards import report_body, report_confirm
from classmate.bot.states import ReportForm
from classmate.core.enums import EntityType, RecordStatus
from classmate.db.models import DailyReport, TelegramOutbox
from classmate.services.records import approve_record, list_reports


def _telegram_user(user):
    return SimpleNamespace(
        id=user.telegram_id,
        username=user.username,
        full_name=user.full_name,
        first_name=user.full_name,
    )


def _message(user, text: str):
    return SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        text=text,
        from_user=_telegram_user(user),
        answer=AsyncMock(),
    )


def _callback(user):
    return SimpleNamespace(
        data="rpt:confirm",
        from_user=_telegram_user(user),
        message=SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=AsyncMock(),
            answer=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_entering_report_text_only_shows_preview_and_does_not_create_record(world):
    state = AsyncMock()
    state.get_data.return_value = {"class_id": world["class"].id}
    message = _message(world["student"], "امروز تمرین‌های ریاضی انجام شد.")

    await report_body(message, state, world["session"])

    state.set_state.assert_awaited_once_with(ReportForm.confirm)
    state.update_data.assert_awaited_once()
    preview_call = message.answer.call_args_list[0]
    assert "پیش‌نمایش گزارش روزانه" in preview_call.args[0]

    markup = preview_call.kwargs["reply_markup"]
    callbacks = {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    }
    assert callbacks == {"rpt:confirm", "rpt:edit", "rpt:cancel"}

    saved = (
        await world["session"].scalars(
            select(DailyReport).where(DailyReport.class_id == world["class"].id)
        )
    ).all()
    assert saved == []


@pytest.mark.asyncio
async def test_student_confirmation_creates_pending_record_but_does_not_publish(world):
    world["class"].group_chat_id = -100555001
    await world["session"].flush()
    state = AsyncMock()
    state.get_data.return_value = {
        "class_id": world["class"].id,
        "report_body": "دانش‌آموز متن گزارش را تأیید کرد.",
        "report_owner_telegram_id": world["student"].telegram_id,
    }
    callback = _callback(world["student"])

    await report_confirm(callback, state, world["session"])

    record = await world["session"].scalar(
        select(DailyReport).where(DailyReport.class_id == world["class"].id)
    )
    assert record is not None
    assert record.body == "دانش‌آموز متن گزارش را تأیید کرد."
    assert record.status == RecordStatus.PENDING
    state.clear.assert_awaited_once()

    outbox = (
        await world["session"].scalars(
            select(TelegramOutbox).where(
                TelegramOutbox.entity_type == EntityType.REPORT.value,
                TelegramOutbox.entity_id == record.id,
            )
        )
    ).all()
    assert outbox == []
    assert "درخواست در پنل" in callback.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_student_can_submit_multiple_reports_for_the_same_class_and_day(world):
    world["class"].group_chat_id = -100555010
    await world["session"].flush()

    for body in ("گزارش اول امروز", "گزارش دوم امروز"):
        state = AsyncMock()
        state.get_data.return_value = {
            "class_id": world["class"].id,
            "report_body": body,
            "report_owner_telegram_id": world["student"].telegram_id,
        }
        callback = _callback(world["student"])

        await report_confirm(callback, state, world["session"])

        assert "درخواست در پنل" in callback.message.edit_text.call_args.args[0]

    reports = (
        await world["session"].scalars(
            select(DailyReport)
            .where(DailyReport.class_id == world["class"].id)
            .order_by(DailyReport.id.asc())
        )
    ).all()
    assert len(reports) == 2
    assert [report.body for report in reports] == [
        "گزارش اول امروز",
        "گزارش دوم امروز",
    ]
    assert reports[0].report_date == reports[1].report_date
    assert all(report.status == RecordStatus.PENDING for report in reports)

    for report in reports:
        approved, _ = await approve_record(
            world["session"],
            EntityType.REPORT.value,
            report.id,
            world["approver"].id,
            world["school"].id,
            world["class"].id,
            "Asia/Tehran",
        )
        assert approved is True

    await world["session"].commit()
    published_reports = await list_reports(
        world["session"], world["class"].id, reports[0].report_date
    )
    assert len(published_reports) == 2


@pytest.mark.asyncio
async def test_operator_confirmation_publishes_only_after_click(world):
    world["class"].group_chat_id = -100555002
    await world["session"].flush()

    # Text entry itself is only a preview; it does not create a DailyReport.
    preview_state = AsyncMock()
    preview_state.get_data.return_value = {"class_id": world["class"].id}
    await report_body(
        _message(world["manager"], "گزارش روزانه اپراتور"),
        preview_state,
        world["session"],
    )
    saved_before_confirm = (
        await world["session"].scalars(
            select(DailyReport).where(DailyReport.class_id == world["class"].id)
        )
    ).all()
    assert saved_before_confirm == []

    confirm_state = AsyncMock()
    confirm_state.get_data.return_value = {
        "class_id": world["class"].id,
        "report_body": "گزارش روزانه اپراتور",
        "report_owner_telegram_id": world["manager"].telegram_id,
    }
    callback = _callback(world["manager"])

    await report_confirm(callback, confirm_state, world["session"])

    record = await world["session"].scalar(
        select(DailyReport).where(DailyReport.class_id == world["class"].id)
    )
    assert record is not None
    assert record.status == RecordStatus.APPROVED
    assert record.approved_by == world["manager"].id

    outbox = await world["session"].scalar(
        select(TelegramOutbox).where(
            TelegramOutbox.entity_type == EntityType.REPORT.value,
            TelegramOutbox.entity_id == record.id,
        )
    )
    assert outbox is not None
    assert outbox.target_type == "group"
    assert outbox.expected_record_status == RecordStatus.APPROVED.value
    assert outbox.action_type is None
    assert "برای بررسی" not in outbox.text
    assert "گزارش روزانه منتشر شد" in outbox.text
    assert "تأیید و منتشر شد" in callback.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_approving_student_report_publishes_to_group_without_inline_review_buttons(world):
    world["class"].group_chat_id = -100555003
    await world["session"].flush()
    report = DailyReport(
        class_id=world["class"].id,
        report_date=date.today(),
        title="گزارش دانش‌آموز",
        body="متن گزارش برای بررسی",
        created_by=world["student"].id,
        status=RecordStatus.PENDING,
    )
    world["session"].add(report)
    await world["session"].flush()

    ok, _ = await approve_record(
        world["session"],
        EntityType.REPORT.value,
        report.id,
        world["approver"].id,
        world["school"].id,
        world["class"].id,
        "Asia/Tehran",
    )
    assert ok is True
    await world["session"].commit()

    outbox = await world["session"].scalar(
        select(TelegramOutbox).where(
            TelegramOutbox.entity_type == EntityType.REPORT.value,
            TelegramOutbox.entity_id == report.id,
        )
    )
    assert outbox is not None
    assert outbox.expected_record_status == RecordStatus.APPROVED.value
    assert outbox.action_type is None
    assert "متن گزارش برای بررسی" in outbox.text


@pytest.mark.asyncio
async def test_student_homework_stays_private_until_operator_approves(world):
    from datetime import timedelta

    from classmate.services.records import create_homework

    world["class"].group_chat_id = -100555004
    await world["session"].flush()
    homework = await create_homework(
        world["session"],
        world["class"].id,
        world["subject"],
        "تکلیف دانش‌آموز",
        "شرح تکلیف",
        date.today() + timedelta(days=2),
        world["student"].id,
    )
    assert homework.status == RecordStatus.PENDING
    assert await world["session"].scalar(
        select(TelegramOutbox.id).where(
            TelegramOutbox.entity_type == EntityType.HOMEWORK.value,
            TelegramOutbox.entity_id == homework.id,
        )
    ) is None

    ok, _ = await approve_record(
        world["session"],
        EntityType.HOMEWORK.value,
        homework.id,
        world["approver"].id,
        world["school"].id,
        world["class"].id,
        "Asia/Tehran",
    )
    assert ok is True
    await world["session"].commit()

    published = await world["session"].scalar(
        select(TelegramOutbox).where(
            TelegramOutbox.entity_type == EntityType.HOMEWORK.value,
            TelegramOutbox.entity_id == homework.id,
        )
    )
    assert published is not None
    assert published.expected_record_status == RecordStatus.APPROVED.value
    assert published.action_type is None
    assert "برای بررسی" not in published.text
    assert "تکلیف جدید منتشر شد" in published.text


@pytest.mark.asyncio
async def test_report_edit_returns_to_body_without_creating_a_record(world):
    from classmate.bot.handlers.wizards import report_edit

    state = AsyncMock()
    state.get_data.return_value = {
        "class_id": world["class"].id,
        "report_body": "پیش‌نویس",
        "report_owner_telegram_id": world["student"].telegram_id,
    }
    callback = _callback(world["student"])
    callback.data = "rpt:edit"

    await report_edit(callback, state)

    state.set_state.assert_awaited_once_with(ReportForm.body)
    assert "متن اصلاح‌شده گزارش" in callback.message.answer.call_args.args[0]
    assert callback.message.answer.call_args.kwargs["reply_markup"] is not None
    saved = (
        await world["session"].scalars(
            select(DailyReport).where(DailyReport.class_id == world["class"].id)
        )
    ).all()
    assert saved == []


@pytest.mark.asyncio
async def test_report_cancel_removes_preview_and_creates_nothing(world):
    from classmate.bot.handlers.wizards import report_cancel

    state = AsyncMock()
    state.get_data.return_value = {
        "class_id": world["class"].id,
        "report_body": "متنی که لغو شد",
        "report_owner_telegram_id": world["student"].telegram_id,
    }
    callback = _callback(world["student"])
    callback.data = "rpt:cancel"

    await report_cancel(callback, state, world["session"])

    state.clear.assert_awaited_once()
    callback.message.delete = AsyncMock()
    saved = (
        await world["session"].scalars(
            select(DailyReport).where(DailyReport.class_id == world["class"].id)
        )
    ).all()
    assert saved == []
    assert "گزارش لغو شد" in callback.message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_trusted_operator_confirmation_publishes_report_after_click(world):
    world["class"].group_chat_id = -100555005
    await world["session"].flush()
    preview_state = AsyncMock()
    preview_state.get_data.return_value = {"class_id": world["class"].id}
    preview_message = _message(world["approver"], "گزارش ثبت‌شده توسط اپراتور")

    await report_body(preview_message, preview_state, world["session"])

    keyboard = preview_message.answer.call_args_list[0].kwargs["reply_markup"]
    confirm_button = keyboard.inline_keyboard[0][0]
    assert confirm_button.callback_data == "rpt:confirm"
    assert confirm_button.text == "✅ تأیید و انتشار گزارش"
    saved_before_confirm = (
        await world["session"].scalars(
            select(DailyReport).where(DailyReport.class_id == world["class"].id)
        )
    ).all()
    assert saved_before_confirm == []

    confirm_state = AsyncMock()
    confirm_state.get_data.return_value = {
        "class_id": world["class"].id,
        "report_body": "گزارش ثبت‌شده توسط اپراتور",
        "report_owner_telegram_id": world["approver"].telegram_id,
    }
    callback = _callback(world["approver"])
    await report_confirm(callback, confirm_state, world["session"])

    record = await world["session"].scalar(
        select(DailyReport).where(DailyReport.class_id == world["class"].id)
    )
    assert record is not None
    assert record.status == RecordStatus.APPROVED
    assert record.approved_by == world["approver"].id
    published = await world["session"].scalar(
        select(TelegramOutbox).where(
            TelegramOutbox.entity_type == EntityType.REPORT.value,
            TelegramOutbox.entity_id == record.id,
        )
    )
    assert published is not None
    assert published.target_chat_id == -100555005
    assert "گزارش روزانه منتشر شد" in published.text
