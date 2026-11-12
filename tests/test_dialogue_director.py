from astrbot_plugin_bot_friend.dialogue_director import (
    DIRECTOR_RULES,
    DialogueState,
    assess_reply,
    build_director_prompt,
    choose_cutoff,
    normalize_round_limits,
    parse_directed_output,
)


def test_round_limits_are_inclusive_and_reversed_values_are_normalized():
    assert normalize_round_limits(4, 6) == (4, 6)
    assert normalize_round_limits(4, 1) == (1, 4)
    assert normalize_round_limits(0, 6) == (0, 6)
    assert normalize_round_limits(4, 0) == (0, 0)
    assert choose_cutoff(4, 6, randint=lambda low, high: high) == 6
    assert choose_cutoff(4, 1, randint=lambda low, high: low) == 1
    assert choose_cutoff(4, 1, randint=lambda low, high: high) == 4
    assert choose_cutoff(0, 6) == 6


def test_director_prompt_is_relationship_first_compact_and_uses_a_b():
    state = DialogueState(cutoff_round=5)
    state.add_message("A", "来吃饭。")
    state.add_message("B", "给我留个位置。")
    state.recent_actions.append("表达偏好")
    prompt = build_director_prompt(state, next_round=2, is_final_round=False)

    assert "专属关系提示词优先级最高" in prompt
    assert "A是对方，B是你" in prompt
    assert "A：来吃饭。" in prompt
    assert "B：给我留个位置。" in prompt
    assert "严禁照抄" in prompt
    assert "逻辑和信息承接必须通顺" in prompt
    assert "最多40字" in prompt
    assert "动作标签仅供代码记录" in prompt
    assert "甲" not in prompt
    assert "乙" not in prompt
    assert "拿乔" not in prompt
    assert len(DIRECTOR_RULES) < 450


def test_final_round_requires_closure_without_new_hook():
    prompt = build_director_prompt(
        DialogueState(cutoff_round=4),
        next_round=4,
        is_final_round=True,
    )
    assert "这是最后一轮" in prompt
    assert "不得再提出新问题、新人物、新地点或新悬念" in prompt


def test_directed_json_keeps_action_internal_and_enforces_40_chars():
    action, reply = parse_directed_output(
        '{"action":"提出小条件","reply":"饭可以吃，窗边的位置要留给我。"}'
    )
    assert action == "提出小条件"
    assert reply == "饭可以吃，窗边的位置要留给我。"
    assert "提出小条件" not in reply

    action, reply = parse_directed_output(
        '{"action":"表达判断","reply":"' + "很" * 50 + '"}'
    )
    assert action == "表达判断"
    assert len(reply) == 40


def test_plain_text_fallback_and_meta_language_cleanup():
    action, reply = parse_directed_output("我要提出条件，你先说清楚。")
    assert action == "未标注"
    assert reply == "我有个条件，你先说清楚。"
    assert "我要提出条件" not in reply


def test_low_information_and_repetition_are_recorded_for_next_round():
    state = DialogueState(cutoff_round=5)
    assert assess_reply("来了。", state) == "回复只有简单附和或确认，没有推进"

    state.add_message("B", "给我留个靠窗的位置。")
    assert assess_reply("给我留靠窗的位置。", state) == "回复与最近内容高度重复"
    assert assess_reply("那我晚十分钟到，你别先开饭。", state) == ""
