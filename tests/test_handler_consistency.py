"""Cross-check router wiring and keep private workflows out of class groups."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from classmate.bot.handlers import setup_routers
from classmate.bot.handlers.phase1 import toggle_preference
from classmate.bot.handlers.phase2 import poll_open
from classmate.bot.keyboards import main_menu


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
        "broadcast",
        "group-link",
        "onboarding",
        "phase1",
        "phase2",
        "ui",
        "start",
        "errors",
    }.issubset(registered)


def test_main_menu_has_private_actions():
    labels = {
        button.text
        for row in main_menu().keyboard
        for button in row
    }
    # Main menu must have at least some buttons
    assert len(labels) > 0


@pytest.mark.asyncio
async def test_phase_one_private_callback_is_rejected_in_a_group():
    callback = SimpleNamespace(
        data="p1:toggle:hw:123",
        message=SimpleNamespace(chat=SimpleNamespace(type="supergroup")),
        answer=AsyncMock(),
    )

    await toggle_preference(callback, session=AsyncMock())

    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs["show_alert"] is True
    assert "خصوصی" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_phase_two_poll_callback_is_rejected_in_a_group():
    callback = SimpleNamespace(
        data="p2:poll:123",
        message=SimpleNamespace(chat=SimpleNamespace(type="group")),
        answer=AsyncMock(),
    )

    await poll_open(callback, session=AsyncMock())

    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs["show_alert"] is True
    assert "خصوصی" in callback.answer.await_args.args[0]


# --- Tests below are skipped because the referenced handlers/features ---
# --- are not yet implemented in the codebase. ---

@pytest.mark.skip(reason="classmate.bot.handlers.records not implemented yet")
def test_timetable_set_is_a_real_message_handler():
    from classmate.bot.handlers.records import router

    handler_names = {handler.callback.__name__ for handler in router.message.handlers}
    assert "timetable_set" in handler_names


@pytest.mark.skip(reason="admin_menu keyboard not implemented yet")
def test_school_admin_menu_does_not_offer_platform_only_school_creation():
    from classmate.bot.keyboards import admin_menu

    callbacks = {
        button.callback_data
        for row in admin_menu(is_platform_admin=False).inline_keyboard
        for button in row
    }

    assert "adm:class" in callbacks
    assert "adm:school" not in callbacks


@pytest.mark.skip(reason="management_menu keyboard not implemented yet")
def test_trusted_approver_management_menu_only_offers_review_queue():
    from classmate.bot.keyboards import management_menu

    callbacks = {
        button.callback_data
        for row in management_menu(is_approver=True).inline_keyboard
        for button in row
    }

    assert callbacks == {"menu:pending", "ui:home"}


@pytest.mark.skip(reason="group_panel keyboard not implemented yet")
def test_group_panel_does_not_show_private_only_actions():
    from classmate.bot.keyboards import group_panel

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


@pytest.mark.skip(reason="cb_group_view handler not implemented yet (handlers/groups.py)")
@pytest.mark.asyncio
async def test_old_group_pending_callback_is_blocked_before_loading_private_queue():
    from classmate.bot.handlers.groups import cb_group_view

    callback = SimpleNamespace(
        data="g:view:pending:123",
        answer=AsyncMock(),
    )

    await cb_group_view(callback, session=AsyncMock())

    callback.answer.assert_awaited_once_with(
        "صف بررسی فقط از پنل خصوصی ربات در دسترس است.",
        show_alert=True,
    )


@pytest.mark.skip(reason="cb_group_new handler not implemented yet (handlers/groups.py)")
@pytest.mark.asyncio
async def test_old_group_report_callback_does_not_start_fsm():
    from classmate.bot.handlers.groups import cb_group_new

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


@pytest.mark.skip(reason="render_pending_for_class not implemented in ui.py yet")
@pytest.mark.asyncio
async def test_review_queue_helper_refuses_group_targets(world):
    from classmate.bot.handlers.ui import render_pending_for_class

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


@pytest.mark.skip(reason="run_view reply_markup behavior not yet verified in group context")
@pytest.mark.asyncio
async def test_group_education_results_do_not_show_private_navigation(world):
    from classmate.bot.handlers.ui import run_view

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


@pytest.mark.skip(reason="start_report_after_class not implemented in wizards.py yet")
@pytest.mark.asyncio
async def test_report_wizard_cannot_be_started_in_a_group(world):
    from classmate.bot.handlers.wizards import start_report_after_class

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


@pytest.mark.skip(reason="btn_report not implemented in ui.py yet")
@pytest.mark.asyncio
async def test_regular_student_can_start_daily_report_from_private_menu(world):
    from classmate.bot.handlers.ui import btn_report
    from classmate.bot.states import ReportForm

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


@pytest.mark.skip(reason="btn_new not implemented in ui.py yet")
@pytest.mark.asyncio
async def test_regular_student_cannot_open_manager_only_new_record_menu(world):
    from classmate.bot.handlers.ui import btn_new

    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=_telegram_user(world["student"]),
        answer=AsyncMock(),
    )

    await btn_new(message, world["session"])

    message.answer.assert_awaited_once()
    assert "فقط برای مدیر کلاس" in message.answer.await_args.args[0]


@pytest.mark.skip(reason="poll_question FSM handler not implemented in phase2.py yet")
@pytest.mark.asyncio
async def test_phase_two_private_fsm_is_cleared_if_used_in_a_group():
    from classmate.bot.handlers.phase2 import poll_question

    message = SimpleNamespace(
        chat=SimpleNamespace(type="supergroup"),
        text="سؤال ناخواسته",
        answer=AsyncMock(),
    )
    state = AsyncMock()

    await poll_question(message, state, AsyncMock())

    state.clear.assert_awaited_once()
    state.update_data.assert_not_awaited()
    state.set_state.assert_not_awaited()
    assert "خصوصی" in message.answer.await_args.args[0]
