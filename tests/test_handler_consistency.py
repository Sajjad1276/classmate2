"""Cross-check router wiring and keep private workflows out of class groups."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from classmate.bot.handlers import setup_routers
from classmate.bot.handlers.group import cb_group_new, cb_group_view
from classmate.bot.handlers.ui import btn_report, render_pending_for_class, run_view
from classmate.bot.handlers.wizards import start_report_after_class
from classmate.bot.keyboards import admin_menu, group_panel, main_menu, management_menu
from classmate.bot.states import ReportForm


def _telegram_user(user):
    return SimpleNamespace(
        id=user.telegram_id,
        username=user.username,
        full_name=user.full_name,
        first_name=user.full_name,
    )


def test_every_command_router_is_registered_in_the_root_dispatcher():
    root = setup_routers()

    registered = {router.name for router in root.sub_routers}

    assert {
        "group",
        "onboarding",
        "queries",
        "records",
        "phase1",
        "phase2",
        "ui",
        "wizards",
        "start",
        "approvals",
        "errors",
    }.issubset(registered)


def test_timetable_set_is_a_real_message_handler():
    from classmate.bot.handlers.records import router

    handler_names = {handler.callback.__name__ for handler in router.message.handlers}

    assert "timetable_set" in handler_names


def test_regular_members_have_a_private_daily_report_entry():
    labels = {
        button.text
        for row in main_menu().keyboard
        for button in row
    }

    assert "📋 ثبت گزارش روزانه" in labels


def test_school_admin_menu_does_not_offer_platform_only_school_creation():
    callbacks = {
        button.callback_data
        for row in admin_menu(is_platform_admin=False).inline_keyboard
        for button in row
    }

    assert "adm:class" in callbacks
    assert "adm:school" not in callbacks


def test_trusted_approver_management_menu_only_offers_review_queue():
    callbacks = {
        button.callback_data
        for row in management_menu(is_approver=True).inline_keyboard
        for button in row
    }

    assert callbacks == {"menu:pending", "ui:home"}


def test_group_panel_does_not_show_private_only_actions():
    keyboard = group_panel(class_id=123, class_name="کلاس نمونه")
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    }

    assert "g:view:pending:123" not in callbacks
    assert "g:new:report:123" not in callbacks
    assert "g:view:homework:123" in callbacks
    assert "g:new:hw:123" in callbacks


@pytest.mark.asyncio
async def test_old_group_pending_callback_is_blocked_before_loading_private_queue():
    callback = SimpleNamespace(
        data="g:view:pending:123",
        answer=AsyncMock(),
    )

    await cb_group_view(callback, session=AsyncMock())

    callback.answer.assert_awaited_once_with(
        "صف بررسی فقط از پنل خصوصی ربات در دسترس است.",
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_old_group_report_callback_does_not_start_fsm():
    callback = SimpleNamespace(
        data="g:new:report:123",
        answer=AsyncMock(),
    )
    state = AsyncMock()

    await cb_group_new(callback, state=state, session=AsyncMock())

    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs["show_alert"] is True
    assert "گفت‌وگوی خصوصی" in callback.answer.await_args.args[0]
    state.set_state.assert_not_awaited()
    state.update_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_queue_helper_refuses_group_targets(world):
    target = SimpleNamespace(
        chat=SimpleNamespace(type="supergroup"),
        answer=AsyncMock(),
    )

    await render_pending_for_class(
        target,
        world["session"],
        world["manager"],
        world["class"].id,
    )

    target.answer.assert_awaited_once_with(
        "صف بررسی فقط از پنل خصوصی ربات در دسترس است."
    )


@pytest.mark.asyncio
async def test_group_education_results_do_not_show_private_navigation(world):
    target = SimpleNamespace(
        chat=SimpleNamespace(type="supergroup"),
        answer=AsyncMock(),
    )

    await run_view(
        target,
        world["session"],
        "homework",
        world["class"].id,
        world["manager"],
        close_reply_keyboard=False,
    )

    target.answer.assert_awaited_once()
    assert "reply_markup" not in target.answer.await_args.kwargs


@pytest.mark.asyncio
async def test_report_wizard_cannot_be_started_in_a_group(world):
    message = SimpleNamespace(
        chat=SimpleNamespace(type="group"),
        answer=AsyncMock(),
    )
    state = AsyncMock()

    await start_report_after_class(
        message,
        state,
        world["session"],
        world["class"].id,
    )

    state.clear.assert_awaited_once()
    state.update_data.assert_not_awaited()
    state.set_state.assert_not_awaited()
    message.answer.assert_awaited_once()
    assert "خصوصی" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_regular_student_can_start_daily_report_from_private_menu(world):
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=_telegram_user(world["student"]),
        answer=AsyncMock(),
    )
    state = AsyncMock()

    await btn_report(message, state, world["session"])

    state.update_data.assert_awaited_once_with(class_id=world["class"].id)
    state.set_state.assert_awaited_once_with(ReportForm.body)
    message.answer.assert_awaited_once()
