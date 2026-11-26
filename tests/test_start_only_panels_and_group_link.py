from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from classmate.bot.middlewares import StartOnlyCommandMiddleware
from classmate.db.models import ClassMembership, User
from classmate.services.permissions import connect_group_by_code, hash_code, issue_class_group_link_code


@pytest.mark.asyncio
async def test_non_start_commands_are_rejected_and_start_payload_passes():
    middleware = StartOnlyCommandMiddleware()
    handler = AsyncMock(return_value="handled")

    blocked = SimpleNamespace(
        text="/join OLD_CODE",
        caption=None,
        answer=AsyncMock(),
    )
    result = await middleware(handler, blocked, {})

    assert result is None
    handler.assert_not_awaited()
    blocked.answer.assert_awaited_once()
    assert "/start" in blocked.answer.await_args.args[0]

    allowed = SimpleNamespace(
        text="/start join_CLASSCODE123",
        caption=None,
        answer=AsyncMock(),
    )
    result = await middleware(handler, allowed, {})

    assert result == "handled"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_deep_link_registers_the_student_in_the_class(world):
    from classmate.bot.handlers.start import cmd_start

    session = world["session"]
    code = "STARTJOIN42"
    world["class"].member_join_code_hash = hash_code(code)
    student = SimpleNamespace(
        id=909021,
        username="deep_link_student",
        full_name="دانش‌آموز لینک",
        first_name="دانش‌آموز لینک",
    )
    message = SimpleNamespace(
        text=f"/start join_{code}",
        chat=SimpleNamespace(type="private"),
        from_user=student,
        answer=AsyncMock(),
    )
    state = AsyncMock()

    await cmd_start(message, state=state, session=session)

    state.clear.assert_awaited_once()
    message.answer.assert_awaited()
    assert "ملحق شدید" in message.answer.await_args_list[0].args[0]
    user = await session.scalar(select(User).where(User.telegram_id == student.id))
    assert user is not None
    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == user.id,
        )
    )
    assert membership is not None
    assert membership.is_active is True


@pytest.mark.asyncio
async def test_class_manager_can_issue_and_consume_a_group_link_code(world):
    session = world["session"]
    code, class_group, expires_at = await issue_class_group_link_code(
        session,
        class_id=world["class"].id,
        actor=world["manager"],
    )

    assert class_group.group_link_code_hash == hash_code(code)
    assert class_group.group_link_code_expires_at == expires_at

    linked = await connect_group_by_code(
        session,
        code=code,
        chat_id=-1001234567890,
        chat_title="گروه آزمایشی",
        actor=world["manager"],
        bot_is_admin=True,
        actor_is_admin=True,
    )

    assert linked.id == world["class"].id
    assert linked.group_chat_id == -1001234567890
    assert linked.group_chat_title == "گروه آزمایشی"
    assert linked.group_link_code_hash is None
    assert linked.group_link_code_expires_at is None


@pytest.mark.asyncio
async def test_my_classes_panel_shows_group_link_controls_only_to_managers(world):
    from classmate.bot.handlers.ui import btn_classes

    manager_message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(
            id=world["manager"].telegram_id,
            username=world["manager"].username,
            full_name=world["manager"].full_name,
            first_name=world["manager"].full_name,
        ),
        answer=AsyncMock(),
    )
    await btn_classes(manager_message, session=world["session"])
    manager_markup = manager_message.answer.await_args.kwargs["reply_markup"]
    manager_buttons = [
        button
        for row in manager_markup.inline_keyboard
        for button in row
    ]
    assert any(button.callback_data == f"group:manage:{world['class'].id}" for button in manager_buttons)

    student_message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(
            id=world["student"].telegram_id,
            username=world["student"].username,
            full_name=world["student"].full_name,
            first_name=world["student"].full_name,
        ),
        answer=AsyncMock(),
    )
    await btn_classes(student_message, session=world["session"])
    assert student_message.answer.await_args.kwargs.get("reply_markup") is None


def test_only_start_is_registered_and_no_legacy_command_filters_remain():
    import ast
    from pathlib import Path

    source_root = Path(__file__).resolve().parents[1]
    bot_root = source_root / "src" / "classmate" / "bot"
    registered_commands = []
    command_filters = []

    for path in bot_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = (
                node.func.id if isinstance(node.func, ast.Name) else None
            )
            if function_name == "BotCommand":
                command = next(
                    (
                        keyword.value.value
                        for keyword in node.keywords
                        if keyword.arg == "command"
                        and isinstance(keyword.value, ast.Constant)
                    ),
                    None,
                )
                if command is not None:
                    registered_commands.append(command)
            elif function_name == "Command":
                command_filters.append((path.relative_to(source_root), node.lineno))

    assert registered_commands
    assert set(registered_commands) == {"start"}
    assert command_filters == []
