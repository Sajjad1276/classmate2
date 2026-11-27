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
    assert any(button.callback_data == f"group:manage:{world['class'].id}:classes:0" for button in manager_buttons)

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
    student_markup = student_message.answer.await_args.kwargs["reply_markup"]
    student_buttons = [button for row in student_markup.inline_keyboard for button in row]
    assert any(button.callback_data == "ui:home" for button in student_buttons)
    assert not any((button.callback_data or "").startswith("group:manage:") for button in student_buttons)


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



def test_dashboard_and_class_picker_back_to_main_menu():
    from classmate.bot.handlers.phase1 import dashboard_empty_kb, dashboard_kb
    from classmate.bot.keyboards import class_picker

    dashboard_buttons = [
        button
        for row in dashboard_kb().inline_keyboard
        for button in row
    ]
    assert any(
        button.text == "‹ بازگشت" and button.callback_data == "ui:home"
        for button in dashboard_buttons
    )
    assert not any(button.callback_data == "dashboard:today" for button in dashboard_buttons)

    empty_buttons = [
        button
        for row in dashboard_empty_kb().inline_keyboard
        for button in row
    ]
    assert any(button.callback_data == "ui:home" for button in empty_buttons)

    picker = class_picker([(7, "دهم", "مدرسه")], action="homework", back_callback="ui:home")
    picker_buttons = [button for row in picker.inline_keyboard for button in row]
    assert any(button.callback_data == "ui:home" for button in picker_buttons)
    assert any(button.callback_data == "pick:homework:7:main_menu" for button in picker_buttons)


@pytest.mark.asyncio
async def test_group_selection_registers_automatically_and_notifies_both_chats(world):
    from classmate.bot.handlers.groups import group_chat_selected

    session = world["session"]
    manager = world["manager"]
    class_group = world["class"]
    class_group_id = class_group.id
    group_chat_id = -1001234567123

    state = AsyncMock()
    state.get_data.return_value = {"group_class_id": class_group_id}
    user = SimpleNamespace(
        id=manager.telegram_id,
        username=manager.username,
        full_name=manager.full_name,
        first_name=manager.full_name,
    )
    bot_user = SimpleNamespace(id=999001, username="classmate_test_bot")
    bot = SimpleNamespace(
        get_chat=AsyncMock(
            return_value=SimpleNamespace(type="supergroup", title="گروه آزمایشی")
        ),
        get_me=AsyncMock(return_value=bot_user),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status="administrator")
        ),
        send_message=AsyncMock(),
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private", id=manager.telegram_id),
        from_user=user,
        chat_shared=SimpleNamespace(request_id=class_group_id, chat_id=group_chat_id),
        bot=bot,
        answer=AsyncMock(),
    )

    await group_chat_selected(message, state, session)

    assert class_group.group_chat_id == group_chat_id
    assert class_group.group_chat_title == "گروه آزمایشی"
    assert class_group.group_link_code_hash is None
    assert class_group.member_join_code_hash
    state.clear.assert_awaited_once()
    bot.send_message.assert_awaited_once()
    assert "ثبت گروه در ClassMate" in bot.send_message.await_args.kwargs["text"]
    assert any(
        "گروه با موفقیت ثبت شد" in call.args[0]
        for call in message.answer.await_args_list
    )
    from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

    answer_calls = message.answer.await_args_list
    assert isinstance(answer_calls[0].kwargs["reply_markup"], ReplyKeyboardRemove)
    assert isinstance(answer_calls[-1].kwargs["reply_markup"], InlineKeyboardMarkup)
    assert not any(
        isinstance(call.kwargs.get("reply_markup"), ReplyKeyboardMarkup)
        for call in answer_calls
    )
    restored_buttons = [
        button
        for row in answer_calls[-1].kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert any(button.text == "‹ بازگشت به فهرست کلاس‌ها" for button in restored_buttons)


@pytest.mark.asyncio
async def test_group_management_panel_reflects_registration_state(world):
    from classmate.bot.handlers.groups import group_manage_panel

    manager = world["manager"]
    class_group = world["class"]
    callback = SimpleNamespace(
        data=f"group:manage:{class_group.id}",
        from_user=SimpleNamespace(
            id=manager.telegram_id,
            username=manager.username,
            full_name=manager.full_name,
            first_name=manager.full_name,
        ),
        bot=SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(id=90001)),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
        ),
        message=SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )

    await group_manage_panel(callback, world["session"])
    first_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    first_labels = [button.text for row in first_markup.inline_keyboard for button in row]
    assert "➕ اتصال گروه" in first_labels

    class_group.group_chat_id = -1001234567124
    class_group.group_chat_title = "گروه متصل"
    callback.message.edit_text.reset_mock()
    await group_manage_panel(callback, world["session"])
    second_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    second_labels = [button.text for row in second_markup.inline_keyboard for button in row]
    assert "🔄 تغییر گروه متصل" in second_labels



@pytest.mark.asyncio
async def test_notification_settings_from_main_menu_returns_to_main_menu(world):
    from classmate.bot.handlers.phase1 import notification_settings

    student = world["student"]
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        text="🔔 اعلان‌ها",
        from_user=SimpleNamespace(
            id=student.telegram_id,
            username=student.username,
            full_name=student.full_name,
            first_name=student.full_name,
        ),
        answer=AsyncMock(),
    )

    await notification_settings(message, world["session"])
    markup = message.answer.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert any(button.text == "‹ بازگشت" and button.callback_data == "ui:home" for button in buttons)



@pytest.mark.asyncio
async def test_group_picker_exposes_cancel_and_requires_admin_membership(world):
    from classmate.bot.handlers.groups import select_group_for_class

    manager = world["manager"]
    callback = SimpleNamespace(
        data=f"group:select:{world['class'].id}",
        from_user=SimpleNamespace(
            id=manager.telegram_id,
            username=manager.username,
            full_name=manager.full_name,
            first_name=manager.full_name,
        ),
        message=SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=AsyncMock(),
            answer=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    state = AsyncMock()

    await select_group_for_class(callback, state, world["session"])

    picker = callback.message.answer.await_args.kwargs["reply_markup"]
    button_rows = [[button.text for button in row] for row in picker.keyboard]
    assert ["❌ لغو انتخاب"] in button_rows
    request_button = next(
        button
        for row in picker.keyboard
        for button in row
        if button.request_chat is not None
    )
    assert request_button.request_chat.bot_is_member is True
    assert request_button.request_chat.user_administrator_rights.can_manage_chat is True
    assert request_button.request_chat.bot_administrator_rights.can_manage_chat is True
    assert request_button.request_chat.bot_administrator_rights.can_send_welcome_messages is False



def test_main_menu_is_compact_and_secondary_actions_are_in_more_menu():
    from classmate.bot.keyboards import main_menu, more_menu

    main_labels = [
        button.text
        for row in main_menu(is_owner=True).keyboard
        for button in row
    ]
    assert main_labels == [
        "📊 وضعیت امروز",
        "📚 بخش آموزشی",
        "🏫 کلاس‌های من",
        "🔑 ورود با کد دعوت",
        "🧰 امکانات بیشتر",
    ]
    assert "🔔 تنظیم اعلان‌ها" not in main_labels
    assert "⚙️ مدیریت" not in main_labels
    assert "🏫 کلاس‌ها" not in main_labels

    more_buttons = [
        button
        for row in more_menu(is_owner=True).inline_keyboard
        for button in row
    ]
    callbacks = {button.callback_data for button in more_buttons}
    assert {"p1:preferences:more", "more:help", "more:support", "broadcast:start", "owner:classes:0", "ui:home"} <= callbacks


@pytest.mark.asyncio
async def test_my_classes_and_owner_classes_panels_have_back_buttons(world):
    from classmate.bot.handlers.ui import _render_my_classes, _render_owner_classes

    manager = world["manager"]
    user_message = SimpleNamespace(answer=AsyncMock())
    await _render_my_classes(user_message, world["session"], manager, page=0)
    user_markup = user_message.answer.await_args_list[-1].kwargs["reply_markup"]
    user_callbacks = {
        button.callback_data
        for row in user_markup.inline_keyboard
        for button in row
    }
    assert "ui:home" in user_callbacks

    owner_message = SimpleNamespace(answer=AsyncMock())
    await _render_owner_classes(owner_message, world["session"], page=0)
    owner_markup = owner_message.answer.await_args_list[-1].kwargs["reply_markup"]
    owner_callbacks = {
        button.callback_data
        for row in owner_markup.inline_keyboard
        for button in row
    }
    assert "ui:more" in owner_callbacks


@pytest.mark.asyncio
async def test_group_management_panel_shows_live_status_help_and_origin_back(world):
    from classmate.bot.handlers.groups import group_manage_panel

    manager = world["manager"]
    class_group = world["class"]
    class_group.group_chat_id = -1001234567124
    class_group.group_chat_title = "گروه متصل"

    callback = SimpleNamespace(
        data=f"group:manage:{class_group.id}:classes:2",
        from_user=SimpleNamespace(
            id=manager.telegram_id,
            username=manager.username,
            full_name=manager.full_name,
            first_name=manager.full_name,
        ),
        bot=SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(id=90001)),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
        ),
        message=SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )

    await group_manage_panel(callback, world["session"])
    body = callback.message.edit_text.await_args.args[0]
    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    callbacks = {button.callback_data for button in buttons}

    assert "وضعیت اتصال: ✅" in body
    assert "\\n" not in body
    assert "\n" in body
    assert "نام گروه: <b>گروه متصل</b>" in body
    assert f"group:help:{class_group.id}:classes:2" in callbacks
    assert f"group:select:{class_group.id}:classes:2" in callbacks
    assert "ui:classes:2" in callbacks
    assert any("بررسی مجدد" in button.text for button in buttons)



@pytest.mark.asyncio
async def test_group_picker_closes_inline_panel_before_showing_reply_keyboard(world):
    from aiogram.types import ReplyKeyboardMarkup
    from classmate.bot.handlers.groups import select_group_for_class

    manager = world["manager"]
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        edit_text=AsyncMock(),
        answer=AsyncMock(),
    )
    callback = SimpleNamespace(
        data=f"group:select:{world['class'].id}:classes:0",
        from_user=SimpleNamespace(
            id=manager.telegram_id,
            username=manager.username,
            full_name=manager.full_name,
            first_name=manager.full_name,
        ),
        message=message,
        answer=AsyncMock(),
    )
    state = AsyncMock()

    await select_group_for_class(callback, state, world["session"])

    message.edit_text.assert_awaited_once()
    assert message.edit_text.await_args.kwargs["reply_markup"] is None
    picker_markup = message.answer.await_args.kwargs["reply_markup"]
    assert isinstance(picker_markup, ReplyKeyboardMarkup)
    state.set_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_group_picker_restores_only_previous_inline_panel(world):
    from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
    from classmate.bot.handlers.groups import cancel_group_picker_message

    manager = world["manager"]
    state = AsyncMock()
    state.get_data.return_value = {
        "group_class_id": world["class"].id,
        "group_origin": "classes",
        "group_page": 0,
    }
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(
            id=manager.telegram_id,
            username=manager.username,
            full_name=manager.full_name,
            first_name=manager.full_name,
        ),
        bot=SimpleNamespace(),
        answer=AsyncMock(),
    )

    await cancel_group_picker_message(message, state, world["session"])

    state.clear.assert_awaited_once()
    assert message.answer.await_count == 2
    assert isinstance(
        message.answer.await_args_list[0].kwargs["reply_markup"],
        ReplyKeyboardRemove,
    )
    restored_markup = message.answer.await_args_list[1].kwargs["reply_markup"]
    assert isinstance(restored_markup, InlineKeyboardMarkup)
    buttons = [button for row in restored_markup.inline_keyboard for button in row]
    assert any(button.callback_data == "ui:classes" for button in buttons)
    assert all(
        not isinstance(call.kwargs.get("reply_markup"), ReplyKeyboardMarkup)
        for call in message.answer.await_args_list
    )



def test_inline_menus_use_one_full_width_button_per_row_and_main_menu_keeps_original_layout():
    from classmate.bot.keyboards import class_picker, information_menu, main_menu, more_menu
    from classmate.bot.handlers.phase1 import dashboard_kb

    main_rows = main_menu(is_owner=True).keyboard
    assert [len(row) for row in main_rows] == [2, 2, 1]

    inline_menus = {
        "more": more_menu(is_owner=True).inline_keyboard,
        "education": information_menu().inline_keyboard,
        "dashboard": dashboard_kb().inline_keyboard,
        "class_picker": class_picker(
            [(1, "دهم", "مدرسه"), (2, "یازدهم", "مدرسه")],
            action="calendar",
            back_callback="ui:home",
            page=0,
            page_size=1,
        ).inline_keyboard,
    }

    for menu_name, rows in inline_menus.items():
        assert rows, f"{menu_name} menu should not be empty"
        assert all(len(row) == 1 for row in rows), (
            f"{menu_name} menu has a multi-button row: "
            f"{[[button.text for button in row] for row in rows]}"
        )
