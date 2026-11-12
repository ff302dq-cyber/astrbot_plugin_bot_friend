import asyncio
import types

from astrbot.api.message_components import Plain

from astrbot_plugin_bot_friend.main import BotFriendPlugin


class _Context:
    def get_config(self):
        return {"wake_prefix": ["/"]}


class _Result:
    def __init__(self, text):
        self.chain = [Plain(text)]


class _Event:
    def __init__(self, text, sender_id="200", group_id="100"):
        self.message_str = text
        self.message_chain = [Plain(text)]
        self.message_obj = types.SimpleNamespace(
            group_id=group_id,
            message=self.message_chain,
            message_id="m1",
        )
        self._sender_id = sender_id
        self._result = None
        self.stopped = False

    def get_sender_id(self):
        return self._sender_id

    def get_result(self):
        return self._result

    def stop_event(self):
        self.stopped = True


def _plugin():
    return BotFriendPlugin(
        _Context(),
        {
            "min_auto_rounds": 4,
            "max_auto_rounds": 4,
            "bot_friend_1": {
                "qq": "200",
                "names": "小饼",
                "wake_prefix": "-",
                "prompt": "你和小饼是多年朋友，平时说话直接但互相信任。",
            },
        },
    )


def test_auto_reply_injects_relation_first_director_and_hides_action():
    plugin = _plugin()
    event = _Event("来吃饭。")
    request = types.SimpleNamespace(system_prompt="原Persona")

    asyncio.run(plugin.on_llm_request(event, request))

    assert "【同类关系提示】" in request.system_prompt
    assert "【对话导演】" in request.system_prompt
    assert request.system_prompt.index("【同类关系提示】") < request.system_prompt.index(
        "【对话导演】"
    )
    assert "A是对方，B是你" in request.system_prompt
    assert "甲" not in request.system_prompt
    assert "乙" not in request.system_prompt
    assert event._bot_friend_directed_response is True

    event._result = _Result(
        '{"action":"提出小条件","reply":"可以，给我留个靠窗的位置。"}'
    )
    asyncio.run(plugin.on_decorating_result(event))

    assert event._result.chain[0].text == "-可以，给我留个靠窗的位置。"
    assert "提出小条件" not in event._result.chain[0].text
    state = plugin.dialogue_states["100_200"]
    assert state.completed_rounds == 1
    assert list(state.recent_actions) == ["提出小条件"]


def test_last_round_is_sent_then_following_message_is_stopped():
    plugin = _plugin()
    state = plugin._get_dialogue_state("100_200")
    state.completed_rounds = 3

    final_event = _Event("那就这么说定了。")
    final_request = types.SimpleNamespace(system_prompt="")
    asyncio.run(plugin.on_llm_request(final_event, final_request))
    assert "这是最后一轮" in final_request.system_prompt

    final_event._result = _Result(
        '{"action":"自然收束","reply":"好，位置和没说完的话都给你留着。"}'
    )
    asyncio.run(plugin.on_decorating_result(final_event))
    assert final_event._result.chain
    assert state.finished is True
    assert state.completed_rounds == 4

    next_event = _Event("我到了。")
    next_request = types.SimpleNamespace(system_prompt="")
    asyncio.run(plugin.on_llm_request(next_event, next_request))
    assert next_event.stopped is True
    assert next_request.system_prompt == ""
