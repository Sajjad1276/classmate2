from types import SimpleNamespace

from classmate.bot.handlers.broadcast import render_broadcast_template
from classmate.bot.keyboards import main_menu


def test_broadcast_template_personalizes_and_escapes_recipient_values():
    user = SimpleNamespace(
        telegram_id=123456,
        username="sample_user",
        full_name="Ali <Admin>",
    )

    rendered = render_broadcast_template(
        "سلام {user_name} | {user_mention} | {user_username} | {user_id} "
        "| {current_date} | {bot_name} | {bot_username}",
        user=user,
        bot_name="ClassMate <Pro>",
        bot_username="@Classmatepro_bot",
        current_date="1405/09/05",
    )

    assert "Ali &lt;Admin&gt;" in rendered
    assert "tg://user?id=123456" in rendered
    assert "@sample_user" in rendered
    assert "123456" in rendered
    assert "1405/09/05" in rendered
    assert "ClassMate &lt;Pro&gt;" in rendered
    assert "@Classmatepro_bot" in rendered


def test_management_button_is_adjacent_to_invite_only_for_owner():
    owner_menu = main_menu(is_owner=True)
    join_row = next(
        row for row in owner_menu.keyboard
        if any(button.text == "🔑 ورود با کد دعوت" for button in row)
    )
    assert [button.text for button in join_row] == [
        "🔑 ورود با کد دعوت",
        "🔴 مدیریت",
    ]
    assert any(button.text == "🔴 مدیریت" and button.style == "danger" for button in join_row)

    regular_menu = main_menu(is_owner=False)
    assert all(
        button.text != "🔴 مدیریت"
        for row in regular_menu.keyboard
        for button in row
    )
