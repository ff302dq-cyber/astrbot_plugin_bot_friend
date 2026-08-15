from __future__ import annotations

import json
import random
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher

ACTION_LABELS = (
    "表达偏好",
    "表达判断",
    "轻微玩笑",
    "提出小条件",
    "作出小决定",
    "暂不答应",
    "软化态度",
    "回扣前文",
    "自然收束",
    "追问回避",
    "指出矛盾",
    "要求说清",
    "轻微吃醋",
    "揭穿嘴硬",
    "抓住双关",
    "轻微反击",
)

LOW_INFORMATION_REPLIES = {
    "好",
    "好的",
    "好啊",
    "好呀",
    "嗯",
    "嗯嗯",
    "来了",
    "来啦",
    "马上来",
    "知道了",
    "行",
    "可以",
    "没问题",
}

DIRECTOR_RULES = """【对话导演】
人物设定和上方专属关系提示词优先级最高。本规则只改善推进方式，不得改变性格、称呼、亲疏程度或既有关系；有冲突时放弃导演动作。
先承接当前原话或最近对话中已经存在的词、要求、情绪和事实，再选择一种逻辑成立的推进方式。可表达偏好或判断、轻微玩笑、提出小条件、作小决定、暂不答应、软化、回扣或收束；只有前文确有依据时，才可追问回避、指出矛盾、要求说清、轻微吃醋、揭穿嘴硬、抓双关或轻微反击。
不得只复述、附和、催促或换词重说；不得凭空声称对方隐瞒、失约、撒谎或做过某事。每轮只推进一个重点，逻辑和信息承接必须通顺。正文通常12至30字，最多40字、最多两个短句。
例：A说“来吃饭。”，B可表达一个有依据的小偏好。例子只说明推进方式，严禁照抄措辞、套用句式或带入例中事实。
动作标签仅供代码记录，绝对不能写进reply，也不能说“我要提出条件”“我在试探”或解释对话策略。"""


@dataclass(slots=True)
class DialogueState:
    cutoff_round: int
    completed_rounds: int = 0
    finished: bool = False
    recent_messages: deque[tuple[str, str]] = field(
        default_factory=lambda: deque(maxlen=4)
    )
    recent_actions: deque[str] = field(default_factory=lambda: deque(maxlen=2))
    correction: str = ""

    def add_message(self, role: str, text: str) -> None:
        cleaned = clean_message_text(text)
        if cleaned:
            self.recent_messages.append((role, cleaned))


def normalize_round_limits(min_rounds: int, max_rounds: int) -> tuple[int, int]:
    minimum = max(0, int(min_rounds))
    maximum = max(0, int(max_rounds))
    if maximum == 0:
        return 0, 0
    if minimum == 0:
        return 0, maximum
    if minimum > maximum:
        return maximum, minimum
    return minimum, maximum


def choose_cutoff(
    min_rounds: int,
    max_rounds: int,
    randint: Callable[[int, int], int] = random.randint,
) -> int:
    minimum, maximum = normalize_round_limits(min_rounds, max_rounds)
    if maximum == 0:
        return 0
    if minimum == 0 or minimum == maximum:
        return maximum
    return randint(minimum, maximum)


def clean_message_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def build_director_prompt(
    state: DialogueState,
    next_round: int,
    is_final_round: bool,
) -> str:
    transcript = "\n".join(
        f"{role}：{text}" for role, text in state.recent_messages
    ) or "（暂无可用历史）"
    recent_actions = "、".join(state.recent_actions) or "无"
    correction = (
        f"\n上一轮问题：{state.correction}。本轮必须纠正，不能重复相同意思。"
        if state.correction
        else ""
    )
    ending = (
        "这是最后一轮：回扣已有内容，给出符合关系的态度或小决定；不得再提出新问题、新人物、新地点或新悬念。"
        if is_final_round
        else "从有上下文依据的方式中选择一个，避免重复最近动作。"
    )
    round_text = (
        f"当前为第{next_round}/{state.cutoff_round}轮"
        if state.cutoff_round > 0
        else f"当前为私聊第{next_round}轮，不设自动截断"
    )
    return f"""{DIRECTOR_RULES}

【本轮状态】
{round_text}；最近动作：{recent_actions}。
最近对话（A是对方，B是你）：
{transcript}
{ending}{correction}
最终语气、态度和行为必须服从专属关系提示词。

只输出一行合法JSON，不要Markdown：
{{"action":"从可用动作中选择的内部标签","reply":"实际发送的正文，不含唤醒词"}}"""


def _strip_code_fence(text: str) -> str:
    return re.sub(
        r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE
    ).strip()


def _extract_json_object(text: str) -> dict | None:
    cleaned = _strip_code_fence(text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _remove_meta_language(text: str) -> str:
    replacements = {
        "我要提出条件": "我有个条件",
        "我来提出条件": "我有个条件",
        "我在试探你": "我想听你的真话",
        "我要进行试探": "我想听你的真话",
        "我要回扣前文": "说回刚才",
        "我要自然收束": "那就这样",
    }
    cleaned = text
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    cleaned = re.sub(r"(?:本轮|当前)动作\s*[:：]\s*[^，。！？\n]+[，。！？]?", "", cleaned)
    cleaned = re.sub(r"(?:对话|回复)策略\s*[:：]\s*[^，。！？\n]+[，。！？]?", "", cleaned)
    return cleaned.strip()


def parse_directed_output(raw_text: str, max_chars: int = 40) -> tuple[str, str]:
    raw = clean_message_text(_strip_code_fence(str(raw_text or "")))
    data = _extract_json_object(raw)
    if data is not None:
        action = clean_message_text(data.get("action", ""))
        reply = clean_message_text(data.get("reply", ""))
    else:
        action_match = re.search(r'["\']action["\']\s*[:：]\s*["\']([^"\']+)', raw)
        reply_match = re.search(r'["\']reply["\']\s*[:：]\s*["\']([^"\']+)', raw)
        action = clean_message_text(action_match.group(1)) if action_match else ""
        reply = clean_message_text(reply_match.group(1)) if reply_match else raw
    if action not in ACTION_LABELS:
        action = "未标注"
    reply = _remove_meta_language(reply)
    if len(reply) > max_chars:
        reply = reply[:max_chars].rstrip()
    return action, reply


def _normalized_for_comparison(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).lower()


def assess_reply(reply: str, state: DialogueState) -> str:
    normalized = _normalized_for_comparison(reply)
    if normalized in LOW_INFORMATION_REPLIES:
        return "回复只有简单附和或确认，没有推进"
    previous_replies = [
        text for role, text in state.recent_messages if role == "B"
    ]
    for previous in previous_replies:
        previous_normalized = _normalized_for_comparison(previous)
        if not previous_normalized or not normalized:
            continue
        similarity = SequenceMatcher(None, normalized, previous_normalized).ratio()
        if similarity >= 0.82:
            return "回复与最近内容高度重复"
    return ""
