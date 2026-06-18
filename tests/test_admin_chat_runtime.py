import pytest

from sumbot.chat_approval import CHAT_APPROVAL_STATUS_LEFT, CHAT_APPROVAL_STATUS_REVIEWED
from sumbot.telegram_handlers.admin_chat_runtime import (
    allow_admin_managed_chat_readd,
    leave_admin_managed_chat,
)


class FakeBot:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.left_chat_ids = []

    async def leave_chat(self, chat_id: int) -> None:
        if self.fail:
            raise RuntimeError("leave failed")
        self.left_chat_ids.append(chat_id)


class FakeServices:
    def __init__(self):
        self.status_updates = []

    async def set_chat_approval_status(self, chat_id: int, status: str) -> None:
        self.status_updates.append((chat_id, status))


@pytest.mark.asyncio
async def test_leave_admin_managed_chat_sets_left_only_after_success():
    services = FakeServices()
    bot = FakeBot()

    await leave_admin_managed_chat(services, bot, -1001)

    assert bot.left_chat_ids == [-1001]
    assert services.status_updates == [(-1001, CHAT_APPROVAL_STATUS_LEFT)]


@pytest.mark.asyncio
async def test_leave_admin_managed_chat_preserves_flag_when_telegram_fails():
    services = FakeServices()

    with pytest.raises(RuntimeError, match="leave failed"):
        await leave_admin_managed_chat(services, FakeBot(fail=True), -1001)

    assert services.status_updates == []


@pytest.mark.asyncio
async def test_allow_admin_managed_chat_readd_replaces_left_flag():
    services = FakeServices()

    await allow_admin_managed_chat_readd(services, -1001)

    assert services.status_updates == [(-1001, CHAT_APPROVAL_STATUS_REVIEWED)]
