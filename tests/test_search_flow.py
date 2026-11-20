"""Regression tests for search flow cancellation and keyboard styling."""

from types import SimpleNamespace

import pytest

from classmate.bot.keyboards import cancel_kb


@pytest.mark.asyncio
async def test_search_cancel_clears_fsm_and_restores_main_menu(monkeypatch):
    from classmate.bot.handlers import phase1

    fake_user = object()
    main_menu = object()

    class FakeState:
        cleared = False

        async def clear(self):
            self.cleared = True

    class FakeMessage:
        text = "❌ انصراف"
        chat = SimpleNamespace(type="private")
        from_user = SimpleNamespace(id=1234)
        replies = []

        async def answer(self, text, reply_markup=None):
            self.replies.append((text, reply_markup))

    async def fake_get_user(session, telegram_user, *, private=False):
        assert telegram_user.id == 1234
        assert private is True
        return fake_user

    async def fake_menu_for(session, user):
        assert user is fake_user
        return main_menu

    async def unexpected_search(*args, **kwargs):
        raise AssertionError("Search must not run after cancellation")

    monkeypatch.setattr(phase1, "get_user", fake_get_user)
    monkeypatch.setattr(phase1, "menu_for", fake_menu_for)
    monkeypatch.setattr(phase1, "search_records", unexpected_search)

    state = FakeState()
    message = FakeMessage()

    await phase1.search_submit(message, state, session=object())

    assert state.cleared is True
    assert message.replies == [("جست‌وجو لغو شد.", main_menu)]


def test_cancel_reply_keyboard_uses_danger_style():
    keyboard = cancel_kb()

    assert len(keyboard.keyboard) == 1
    assert len(keyboard.keyboard[0]) == 1
    cancel_button = keyboard.keyboard[0][0]
    assert cancel_button.text == "❌ انصراف"
    assert cancel_button.style == "danger"
