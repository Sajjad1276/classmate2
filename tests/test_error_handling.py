from types import SimpleNamespace

import pytest

from classmate.bot.handlers import errors


class FakeState:
    def __init__(self):
        self.cleared = False

    async def clear(self):
        self.cleared = True


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (ValueError("bad input"), "ورودی قابل پردازش نیست"),
        (TypeError("bad type"), "ورودی قابل پردازش نیست"),
        (IndexError("missing"), "ورودی قابل پردازش نیست"),
        (PermissionError("denied"), "دسترسی ندارید"),
        (RuntimeError("internal"), "خطای موقت رخ داد"),
    ],
)
async def test_update_error_uses_safe_actionable_message(monkeypatch, exception, expected):
    sent = []

    async def fake_notify(update, text):
        sent.append(text)

    monkeypatch.setattr(errors, "_notify_user", fake_notify)
    state = FakeState()
    event = SimpleNamespace(
        update=SimpleNamespace(from_user=SimpleNamespace(id=123)),
        exception=exception,
        update_id=456,
    )

    assert await errors.on_update_error(event, state) is True
    assert state.cleared is True
    assert expected in sent[0]
    assert "bad input" not in sent[0]
    assert "internal" not in sent[0]


async def test_update_error_survives_fsm_cleanup_failure(monkeypatch):
    sent = []

    async def fake_notify(update, text):
        sent.append(text)

    monkeypatch.setattr(errors, "_notify_user", fake_notify)

    class BrokenState:
        async def clear(self):
            raise RuntimeError("storage unavailable")

    event = SimpleNamespace(update=SimpleNamespace(), exception=RuntimeError("db"), update_id=1)
    assert await errors.on_update_error(event, BrokenState()) is True
    assert sent == ["خطای موقت رخ داد. لطفاً چند لحظه بعد دوباره تلاش کنید."]
