import asyncio
import sys
import types

from astrbot_plugin_bot_friend.main import BotFriendPlugin
from astrbot_plugin_bot_friend.tests.test_plugin_flow import _Context, _Event


def _plugin(delay):
    return BotFriendPlugin(
        _Context(),
        {
            "forw_delay_min": delay, "forw_delay_max": delay,
            "bot_friend_1": {"qq": "200", "names": "小饼", "wake_prefix": "-", "prompt": ""},
        },
    )


class _Ev(_Event):
    def chain_result(self, chain):
        return types.SimpleNamespace(chain=chain)

    def get_group_id(self):
        return "100"


def _run(plugin, event):
    async def go():
        return [r async for r in plugin.on_message(event)]
    return asyncio.run(go())


def test_forw_recalled_during_delay_is_not_sent_or_charged():
    charges = []

    async def charge(**kw):
        charges.append(kw)
        return True, None, None

    sys.modules["astrbot_ledger"] = types.SimpleNamespace(client=types.SimpleNamespace(charge=charge))
    sys.modules["astrbot_recall_sync"] = types.SimpleNamespace(is_recalled=lambda e: True)
    try:
        out = _run(_plugin(0.05), _Ev("/forw小饼 你是大笨蛋", sender_id="300"))
    finally:
        sys.modules.pop("astrbot_recall_sync", None)
        sys.modules.pop("astrbot_ledger", None)
    assert out == [] and charges == []


def test_forw_not_recalled_is_sent_after_delay():
    sys.modules["astrbot_recall_sync"] = types.SimpleNamespace(is_recalled=lambda e: False)
    try:
        out = _run(_plugin(0.05), _Ev("/forw小饼 你是大笨蛋", sender_id="300"))
    finally:
        sys.modules.pop("astrbot_recall_sync", None)
    assert len(out) == 1
    texts = [c.text for c in out[0].chain if hasattr(c, "text")]
    assert texts and texts[0].startswith("-")


def test_forw_delay_range_swapped_and_zero():
    import time
    sys.modules["astrbot_recall_sync"] = types.SimpleNamespace(is_recalled=lambda e: False)
    try:
        p = BotFriendPlugin(_Context(), {"forw_delay_min": 0.15, "forw_delay_max": 0.05,
                                         "bot_friend_1": {"qq": "200", "names": "小饼", "wake_prefix": "-", "prompt": ""}})
        t = time.time(); out = _run(p, _Ev("/forw小饼 hi", sender_id="300")); dt = time.time() - t
        assert len(out) == 1 and 0.04 <= dt <= 0.3
        p0 = BotFriendPlugin(_Context(), {"forw_delay_min": 0, "forw_delay_max": 0,
                                          "bot_friend_1": {"qq": "200", "names": "小饼", "wake_prefix": "-", "prompt": ""}})
        t = time.time(); out = _run(p0, _Ev("/forw小饼 hi", sender_id="300")); assert time.time() - t < 0.03 and len(out) == 1
    finally:
        sys.modules.pop("astrbot_recall_sync", None)
