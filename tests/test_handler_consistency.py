"""Cross-check router wiring and keep private workflows out of class groups."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from classmate.bot.handlers import setup_routers
from classmate.bot.handlers.group import cb_group_new, cb_group_view
from classmate.bot.handlers.ui import render_pending_for_class
from classmate.bot.handlers.wizards import start_report_after_class
from classmate.bot.keyboards import group_panel
from classmate.bot.states import ReportForm


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
