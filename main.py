"""
AstrBot 插件：Bot同类识别器
让Bot认识其他Bot同类，支持专属提示词、自动识别回复。
"""

import re

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

from .dialogue_director import (
    DialogueState,
    assess_reply,
    build_director_prompt,
    choose_cutoff,
    normalize_round_limits,
    parse_directed_output,
)

DEFAULT_REPLY_FORMAT_PROMPT = (
    "你的回复必须自然并严格遵守人物关系。正文通常12至30字，最多40字、"
    "最多两个短句，不得含有加粗、emoji或markdown。"
)


@register(
    "bot_friend_recognizer",
    "YourName",
    "让Bot认识其他Bot同类，支持专属提示词",
    "4.4",
    "https://github.com/ff302dq-cyber/astrbot_plugin_bot_friend"
)
class BotFriendPlugin(Star):

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.bot_friends = []
        self.dialogue_states: dict[str, DialogueState] = {}
        self._last_round_warning: tuple[int, int] | None = None
        self._reload_config()
        logger.info(f"[Bot同类] 插件已加载，同类数: {len(self.bot_friends)}")

    # ============================================================
    #  配置解析
    # ============================================================

    def _parse_bot_friends(self) -> list:
        """从10个槽位配置中解析bot同类列表"""
        friends = []
        for i in range(1, 11):
            key = f"bot_friend_{i}"
            group = self.config.get(key, {})
            if not group or not isinstance(group, dict):
                continue

            qq = str(group.get("qq", "")).strip()
            names_raw = str(group.get("names", "")).strip()
            wake_prefix = str(group.get("wake_prefix", "")).strip()
            prompt = str(group.get("prompt", "")).strip()

            if not qq:
                continue

            # 支持英文逗号和中文逗号分隔名字
            names = [
                n.strip()
                for n in names_raw.replace("，", ",").split(",")
                if n.strip()
            ]
            if not names:
                logger.warning(f"[BotFriend] 槽位{i} QQ={qq} 没有名字，跳过")
                continue

            friends.append({
                "qq": qq,
                "names": names,
                "wake_prefix": wake_prefix,
                "prompt": prompt
            })
        return friends

    def _reload_config(self):
        """重新加载配置"""
        self.bot_friends = self._parse_bot_friends()
        raw_max = int(self.config.get("max_auto_rounds", 4))
        raw_min = int(self.config.get("min_auto_rounds", 1))
        self.min_auto_rounds, self.max_auto_rounds = normalize_round_limits(
            raw_min, raw_max
        )
        if raw_max > 0 and raw_min > 0 and raw_min > raw_max:
            warning_key = (raw_min, raw_max)
            if self._last_round_warning != warning_key:
                logger.warning(
                    "[BotFriend] min_auto_rounds 大于 max_auto_rounds，"
                    "已将两项配置作为范围端点，"
                    f"按 {self.min_auto_rounds}～{self.max_auto_rounds} 轮随机处理"
                )
                self._last_round_warning = warning_key
        self.reply_format_prompt = str(
            self.config.get("reply_format_prompt", DEFAULT_REPLY_FORMAT_PROMPT)
            or DEFAULT_REPLY_FORMAT_PROMPT
        ).strip()

    def _find_by_name(self, name: str):
        for bot in self.bot_friends:
            if name in bot.get("names", []):
                return bot
        return None

    def _find_by_qq(self, qq: str):
        qq_str = str(qq)
        for bot in self.bot_friends:
            if str(bot.get("qq", "")) == qq_str:
                return bot
        return None

    def _find_by_wake_prefix(self, wake_prefix: str):
        """通过唤醒词查找同类"""
        for bot in self.bot_friends:
            if bot.get("wake_prefix", "") == wake_prefix:
                return bot
        return None

    def _get_all_names(self) -> list:
        names = []
        for bot in self.bot_friends:
            names.extend(bot.get("names", []))
        return names

    def _get_my_wake_prefix(self) -> str:
        """获取当前bot的唤醒词"""
        try:
            astrbot_config = self.context.get_config()
            wake_prefix = astrbot_config.get("wake_prefix", "/")
            if isinstance(wake_prefix, list) and len(wake_prefix) > 0:
                return str(wake_prefix[0])
            elif isinstance(wake_prefix, str) and wake_prefix:
                return wake_prefix
        except Exception as exc:  # noqa: BLE001 - 兼容不同 AstrBot 配置对象
            logger.debug(f"[BotFriend] 获取当前唤醒词失败: {exc}")
        return "/"

    def _strip_my_wake_prefix(self, msg: str, preserve_trailing: bool = False) -> str:
        """兼容事件阶段里消息可能带唤醒词或已去掉唤醒词。"""
        msg = msg or ""
        msg = msg.lstrip() if preserve_trailing else msg.strip()
        try:
            astrbot_config = self.context.get_config()
            wake_prefix = astrbot_config.get("wake_prefix", "/")
            if isinstance(wake_prefix, list):
                prefixes = [str(p) for p in wake_prefix if str(p)]
            elif isinstance(wake_prefix, str) and wake_prefix:
                prefixes = [wake_prefix]
            else:
                prefixes = []

            for prefix in sorted(prefixes, key=len, reverse=True):
                if msg.startswith(prefix):
                    stripped = msg[len(prefix):]
                    return stripped.lstrip() if preserve_trailing else stripped.strip()
        except Exception as exc:  # noqa: BLE001 - 兼容不同 AstrBot 配置对象
            logger.debug(f"[BotFriend] 去除当前唤醒词失败: {exc}")
        return msg

    def _get_message_chain(self, event: AstrMessageEvent):
        if hasattr(event, "message_obj") and hasattr(event.message_obj, "message"):
            message = event.message_obj.message or []
            if message:
                return message
        if hasattr(event, "message_chain") and event.message_chain:
            return event.message_chain
        return []

    def _get_event_text(self, event: AstrMessageEvent) -> str:
        text = "".join(
            str(comp.text or "")
            for comp in self._get_message_chain(event)
            if hasattr(comp, "text")
        )
        if text:
            return text
        return str(getattr(event, "message_str", "") or "")

    def _is_forw_like_event(self, event: AstrMessageEvent) -> bool:
        if self._strip_my_wake_prefix(getattr(event, "message_str", "")).startswith("forw"):
            return True
        for comp in self._get_message_chain(event):
            if hasattr(comp, "text") and self._strip_my_wake_prefix(comp.text).startswith("forw"):
                return True
        return False

    def _format_chain_summary(self, chain) -> str:
        parts = []
        for comp in chain:
            comp_type = comp.__class__.__name__
            if hasattr(comp, "text"):
                parts.append(f"{comp_type}({comp.text!r})")
            elif hasattr(comp, "id"):
                parts.append(f"{comp_type}(id={getattr(comp, 'id', None)})")
            else:
                parts.append(comp_type)
        return " -> ".join(parts)

    def _parse_forw_header(self, text: str):
        tail = text[4:] or ""
        if not tail:
            return None

        space_match = re.search(r"\s+", tail)
        if not space_match:
            return None

        name = tail[:space_match.start()].strip()
        rest = tail[space_match.end():].strip()
        if not name:
            return None
        return name, rest

    def _parse_forw_text(self, text: str):
        text = self._strip_my_wake_prefix(text, preserve_trailing=True)
        if not text.startswith("forw"):
            return None

        parsed = self._parse_forw_header(text)
        if not parsed:
            return None

        name, rest = parsed
        content_chain = []
        if rest:
            content_chain.append(Comp.Plain(rest))
        return name, content_chain

    def _parse_forw_name_only_text(self, text: str):
        text = self._strip_my_wake_prefix(text, preserve_trailing=True)
        if not text.startswith("forw"):
            return None

        name = (text[4:] or "").strip()
        if not name:
            return None

        for known_name in self._get_all_names():
            if name == known_name:
                return name
        return None

    def _parse_forw_chain(self, event: AstrMessageEvent):
        """解析 forw 指令，并保留正文后的 Face 等消息组件。"""
        chain = self._get_message_chain(event)
        if not chain:
            parsed = self._parse_forw_text(getattr(event, "message_str", ""))
            if parsed and parsed[1]:
                return parsed
            return None

        name = ""
        content_chain = []
        start_index = -1

        for idx, comp in enumerate(chain):
            if not hasattr(comp, "text"):
                continue

            parsed = self._parse_forw_text(comp.text)
            if parsed:
                name, text_content_chain = parsed
                content_chain.extend(text_content_chain)
                start_index = idx
                break

            name_only = self._parse_forw_name_only_text(comp.text)
            if name_only and idx + 1 < len(chain):
                name = name_only
                start_index = idx
                break

            return None

        if start_index == -1:
            parsed = self._parse_forw_text(getattr(event, "message_str", ""))
            if parsed and parsed[1]:
                return parsed
            return None

        content_chain.extend(chain[start_index + 1:])

        if not name or not content_chain:
            parsed = self._parse_forw_text(getattr(event, "message_str", ""))
            if parsed and parsed[1]:
                return parsed
            return None
        return name, content_chain

    def _is_forw_command_text(self, text: str) -> bool:
        return self._strip_my_wake_prefix(text).startswith("forw")

    def _prepend_reply_to_chain(self, event: AstrMessageEvent, chain: list) -> bool:
        try:
            msg_id = event.message_obj.message_id
            if not msg_id:
                return False
            if any(isinstance(comp, Comp.Reply) for comp in chain):
                return False
            chain.insert(0, Comp.Reply(id=str(msg_id)))
            return True
        except Exception as exc:  # noqa: BLE001 - 消息组件版本差异时跳过引用
            logger.debug(f"[BotFriend] 添加引用消息失败: {exc}")
            return False

    @staticmethod
    def _conversation_key(group_id: str, target_qq: str) -> str:
        scope = str(group_id or "private")
        return f"{scope}_{target_qq}"

    def _new_dialogue_state(self, unlimited: bool = False) -> DialogueState:
        return DialogueState(
            cutoff_round=(
                0
                if unlimited
                else choose_cutoff(
                    self.min_auto_rounds,
                    self.max_auto_rounds,
                )
            )
        )

    def _reset_dialogue(self, key: str, unlimited: bool = False) -> DialogueState:
        state = self._new_dialogue_state(unlimited=unlimited)
        self.dialogue_states[key] = state
        logger.info(f"[BotFriend] 对话状态已重置 key={key}, 轮数={state.cutoff_round}")
        return state

    def _get_dialogue_state(
        self, key: str, unlimited: bool = False
    ) -> DialogueState:
        state = self.dialogue_states.get(key)
        if state is None:
            state = self._reset_dialogue(key, unlimited=unlimited)
        return state

    @staticmethod
    def _plain_text_from_chain(chain: list) -> str:
        return "".join(
            str(comp.text or "") for comp in chain if hasattr(comp, "text")
        ).strip()

    def _mark_directed_response(
        self,
        event: AstrMessageEvent,
        key: str,
        auto_reply: bool,
        current_round: int = 0,
    ) -> None:
        event._bot_friend_directed_response = True
        event._bot_friend_state_key = key
        event._bot_friend_auto_reply = auto_reply
        event._bot_friend_current_round = current_round

    def _apply_directed_output(self, event: AstrMessageEvent) -> tuple[str, str]:
        """解析隐藏动作标签，只把reply正文留在最终消息链中。"""
        if not getattr(event, "_bot_friend_directed_response", False):
            return "", ""
        result = event.get_result()
        if not result or not result.chain:
            return "", ""

        text_components = [comp for comp in result.chain if hasattr(comp, "text")]
        if not text_components:
            return "", ""
        raw_text = "".join(str(comp.text or "") for comp in text_components)
        action, reply = parse_directed_output(raw_text, max_chars=40)
        text_components[0].text = reply
        for comp in text_components[1:]:
            comp.text = ""
        result.chain = [
            comp
            for comp in result.chain
            if not (hasattr(comp, "text") and not str(comp.text or "").strip())
        ]

        key = str(getattr(event, "_bot_friend_state_key", "") or "")
        state = self.dialogue_states.get(key)
        if state is not None and reply:
            state.correction = assess_reply(reply, state)
            state.add_message("B", reply)
            if action != "未标注":
                state.recent_actions.append(action)
        logger.info(
            f"[BotFriend] 导演输出 action={action}, reply={reply!r}, "
            f"correction={(state.correction if state else '')!r}"
        )
        return action, reply

    def _mark_force_prefix(self, event: AstrMessageEvent, wake_prefix: str):
        """记录本次回复必须带上的唤醒词前缀。"""
        if wake_prefix:
            event._bot_friend_force_prefix = wake_prefix

    def _mark_no_segmented_reply(self, event: AstrMessageEvent):
        """标记本次回复不走 AstrBot 的 LLM 正则分段。"""
        event._bot_friend_no_segmented_reply = True

    def _disable_segmented_reply_for_result(self, event: AstrMessageEvent) -> bool:
        """把本次结果标记为普通结果，避开 only_llm_result 分段。"""
        if not getattr(event, "_bot_friend_no_segmented_reply", False):
            return False

        result = event.get_result()
        if not result:
            return False

        try:
            from astrbot.core.message.message_event_result import ResultContentType

            if hasattr(result, "set_result_content_type"):
                result.set_result_content_type(ResultContentType.GENERAL_RESULT)
            elif hasattr(result, "result_content_type"):
                result.result_content_type = ResultContentType.GENERAL_RESULT
            logger.info("[BotFriend] 已将本次 tell/forw 回复标记为不参与 LLM 分段")
            return True
        except Exception as e:  # noqa: BLE001 - 兼容 AstrBot 不同结果类型
            logger.warning(f"[BotFriend] 禁用本次分段回复失败: {e}")
            return False

    def _ensure_result_prefix(self, event: AstrMessageEvent, wake_prefix: str) -> bool:
        """在最终回复结果中硬性补上同类唤醒词，避免只依赖模型自觉。"""
        if not wake_prefix:
            return False

        result = event.get_result()
        if not result or not result.chain:
            return False

        try:
            for comp in result.chain:
                if hasattr(comp, "text"):
                    text = comp.text or ""
                    if text.startswith(wake_prefix):
                        return False
                    comp.text = f"{wake_prefix}{text}"
                    logger.info(f"[BotFriend] 已硬性补充同类唤醒词前缀: {wake_prefix}")
                    return True

            result.chain.insert(0, Comp.Plain(wake_prefix))
            logger.info(f"[BotFriend] 已插入同类唤醒词前缀: {wake_prefix}")
            return True
        except Exception as e:  # noqa: BLE001 - 兼容不同消息组件实现
            logger.warning(f"[BotFriend] 硬性补充唤醒词失败: {e}")
            return False

    def _strip_trailing_wake_prefix(self, event: AstrMessageEvent, wake_prefix: str) -> bool:
        """清理句末多余的同类唤醒词，避免 *你好。* 被分段出单独唤醒词。"""
        if not wake_prefix:
            return False

        result = event.get_result()
        if not result or not result.chain:
            return False

        changed = False
        for comp in reversed(result.chain):
            if not hasattr(comp, "text"):
                continue

            text = comp.text or ""
            stripped = text.rstrip()
            if stripped == wake_prefix:
                comp.text = ""
                changed = True
                break
            if stripped.endswith(wake_prefix) and stripped != wake_prefix:
                comp.text = stripped[:-len(wake_prefix)].rstrip()
                changed = True
                break
            break

        if changed:
            result.chain = [
                comp for comp in result.chain
                if not (hasattr(comp, "text") and not (comp.text or "").strip())
            ]
            logger.info(f"[BotFriend] 已清理句末冗余同类唤醒词: {wake_prefix}")
        return changed

    # ============================================================
    #  功能1 & 2: tell/forw 命令处理 + 自动识别bot同类
    # ============================================================

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1000)
    async def on_message(self, event: AstrMessageEvent):
        """forw 走硬拼接直发，不经过大模型。"""
        self._reload_config()
        parsed = self._parse_forw_chain(event)
        if not parsed:
            if self._is_forw_like_event(event):
                logger.warning(
                    "[BotFriend] 疑似forw但未解析成功: "
                    f"message_str={getattr(event, 'message_str', '')!r}, "
                    f"chain={self._format_chain_summary(self._get_message_chain(event))}"
                )
            return
        name, content_chain = parsed

        bot_info = self._find_by_name(name)
        if not bot_info:
            return

        wake_prefix = bot_info.get("wake_prefix", "")
        target_qq = str(bot_info["qq"])
        logger.info(f"[BotFriend] forw硬转发: {name}, 对方唤醒词:{wake_prefix}")

        group_id = event.message_obj.group_id
        state_key = self._conversation_key(group_id, target_qq)
        state = self._reset_dialogue(state_key, unlimited=not bool(group_id))
        state.add_message("B", self._plain_text_from_chain(content_chain))

        if hasattr(content_chain[0], "text"):
            content_chain[0].text = f"{wake_prefix}{content_chain[0].text or ''}"
        else:
            content_chain.insert(0, Comp.Plain(wake_prefix))

        self._prepend_reply_to_chain(event, content_chain)

        event.stop_event()
        yield event.chain_result(content_chain)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        处理两个功能：
        1. tell{名字} {内容} 和 forw{名字} {内容} 命令
        2. 自动识别同类bot的消息并注入提示词
        """
        try:
            msg = self._get_event_text(event)
        except Exception as e:  # noqa: BLE001 - 兼容 AstrBot 不同事件对象
            logger.warning(f"[BotFriend] 获取消息内容失败: {e}")
            msg = str(getattr(event, "message_str", "") or "")

        msg = self._strip_my_wake_prefix(msg)

        logger.info(f"[BotFriend] 收到消息: {msg}, repr: {msg!r}")

        # ========== 功能1: 处理 tell/forw 命令 ==========
        if msg.startswith("forw"):
            # forw 应该已在 on_message 中硬拼接直发；如果漏到这里，直接截断，避免 LLM 拒绝或乱回。
            event.stop_event()
            return

        elif msg.startswith("tell"):
            # tell 模式
            space_idx = msg.find(" ", 4)
            if space_idx != -1:
                name = msg[4:space_idx].strip()
                content = msg[space_idx + 1:].strip()
                if name and content:
                    self._reload_config()
                    bot_info = self._find_by_name(name)
                    if bot_info:
                        wake_prefix = bot_info.get("wake_prefix", "")
                        friend_prompt = bot_info.get("prompt", "")
                        target_qq = str(bot_info["qq"])
                        logger.info(f"[BotFriend] tell命令: {name} -> {content}, 对方唤醒词:{wake_prefix}")
                        addition = ""
                        if friend_prompt:
                            addition += f"\n\n【同类关系提示】{friend_prompt}"
                        if self.reply_format_prompt:
                            addition += f"\n\n{self.reply_format_prompt}"
                        addition += (
                            f"\n\n【当前任务】你要对朋友「{name}」说话。"
                            "直接生成要说的正文，不要转述用户指令，不要解释任务。"
                            "唤醒词由代码添加，正文中不要重复唤醒词。"
                            "最终语气、态度和行为必须服从上方专属关系提示词。"
                        )
                        req.system_prompt += addition
                        self._mark_force_prefix(event, wake_prefix)
                        self._mark_no_segmented_reply(event)
                        group_id = event.message_obj.group_id
                        state_key = self._conversation_key(group_id, target_qq)
                        self._reset_dialogue(
                            state_key,
                            unlimited=not bool(group_id),
                        )
                        self._mark_directed_response(
                            event,
                            state_key,
                            auto_reply=False,
                        )
                        return

        # ========== 功能2: 自动识别同类bot ==========
        sender_id = str(event.get_sender_id())
        bot_info = self._find_by_qq(sender_id)
        if bot_info:
            # 只要 QQ 在同类名单中即可识别，不需要检查消息前缀
            # 因为发送方不会携带自己的唤醒词
            names = bot_info.get("names", [])
            friend_prompt = bot_info.get("prompt", "")
            sender_wake = bot_info.get("wake_prefix", "")  # 发送者的唤醒词，回复时要用这个
            name_str = "、".join(names)
            group_id = event.message_obj.group_id
            state_key = self._conversation_key(group_id, sender_id)
            state = self._get_dialogue_state(
                state_key,
                unlimited=not bool(group_id),
            )
            if group_id and state.cutoff_round == 0:
                logger.info(f"[BotFriend] 群{group_id}自动回复已关闭")
                event.stop_event()
                return
            if group_id and (
                state.finished or state.completed_rounds >= state.cutoff_round
            ):
                state.finished = True
                logger.info(
                    f"[BotFriend] key={state_key} 已完成{state.cutoff_round}轮，"
                    "不再继续自动回复"
                )
                event.stop_event()
                return

            state.add_message("A", msg)
            next_round = state.completed_rounds + 1
            is_final_round = bool(
                group_id and next_round == state.cutoff_round
            )
            addition = (
                f"\n\n【同类关系提示】当前和你说话的是你的同类「{name_str}」。"
            )
            if friend_prompt:
                addition += friend_prompt
            if self.reply_format_prompt:
                addition += f"\n\n{self.reply_format_prompt}"
            addition += "\n\n" + build_director_prompt(
                state,
                next_round=next_round,
                is_final_round=is_final_round,
            )
            if sender_wake:
                addition += (
                    f"\n唤醒词「{sender_wake}」由代码添加，reply中不要重复。"
                )
                self._mark_force_prefix(event, sender_wake)
                self._mark_no_segmented_reply(event)
            self._mark_directed_response(
                event,
                state_key,
                auto_reply=True,
                current_round=next_round,
            )
            req.system_prompt += addition
            logger.info(
                f"[Bot同类] 检测到同类「{name_str}」，"
                f"自动回复第{next_round}/{state.cutoff_round or '∞'}轮，"
                f"前缀「{sender_wake}」"
            )

    # ============================================================
    #  功能3: 处理自动回复的对话轮数限制和随机截断
    # ============================================================

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """
        检查是否需要截断对话，并处理轮数限制
        """
        sender_id = str(event.get_sender_id())

        result = event.get_result()
        if not result or not result.chain:
            return

        self._disable_segmented_reply_for_result(event)
        self._apply_directed_output(event)

        forced_prefix = getattr(event, "_bot_friend_force_prefix", "")

        # 找到发送者对应的同类信息
        bot_info = self._find_by_qq(sender_id)
        if not bot_info:
            self._ensure_result_prefix(event, forced_prefix)
            self._strip_trailing_wake_prefix(event, forced_prefix)
            return  # 发送者不是同类名单中的bot

        group_id = event.message_obj.group_id
        if not group_id:
            wake_prefix = forced_prefix or bot_info.get("wake_prefix", "")
            state_key = str(getattr(event, "_bot_friend_state_key", "") or "")
            state = self.dialogue_states.get(state_key)
            if state is not None and getattr(
                event, "_bot_friend_auto_reply", False
            ):
                state.completed_rounds += 1
            self._ensure_result_prefix(event, wake_prefix)
            self._strip_trailing_wake_prefix(event, wake_prefix)
            return  # 非群聊不处理

        state_key = str(getattr(event, "_bot_friend_state_key", "") or "")
        state = self.dialogue_states.get(state_key)
        current_round = int(
            getattr(event, "_bot_friend_current_round", 0) or 0
        )
        if state is not None and getattr(event, "_bot_friend_auto_reply", False):
            state.completed_rounds = max(state.completed_rounds, current_round)
            if state.completed_rounds >= state.cutoff_round:
                state.finished = True

        logger.info(
            f"[BotFriend] 与{bot_info['names'][0]}自动回复第"
            f"{current_round}/{state.cutoff_round if state else '?'}轮"
        )

        wake_prefix = forced_prefix or bot_info.get("wake_prefix", "")
        self._ensure_result_prefix(event, wake_prefix)
        self._strip_trailing_wake_prefix(event, wake_prefix)

    # ============================================================
    #  辅助指令
    # ============================================================

    @filter.command("bot同类列表")
    async def list_bot_friends(self, event: AstrMessageEvent):
        """查看已登记的bot同类"""
        self._reload_config()
        if not self.bot_friends:
            yield event.plain_result("当前没有登记任何bot同类哦～")
            return

        my_wake = self._get_my_wake_prefix()

        lines = ["【Bot同类列表】"]
        for i, bot in enumerate(self.bot_friends, 1):
            names = "、".join(bot.get("names", []))
            qq = bot.get("qq", "未知")
            wake = bot.get("wake_prefix", "无")
            cmds_tell = "、".join(
                [f"{my_wake}tell{n}" for n in bot.get("names", [])]
            )
            cmds_forward = "、".join(
                [f"{my_wake}forw{n}" for n in bot.get("names", [])]
            )
            lines.append(f"{i}. {names} (QQ: {qq}, 唤醒词: {wake})")
            lines.append(f"   组织语言：{cmds_tell}")
            lines.append(f"   直接转发：{cmds_forward}")

        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置bot对话")
    async def reset_counter(self, event: AstrMessageEvent):
        """管理员指令：重置自动回复计数"""
        self.dialogue_states.clear()
        yield event.plain_result("已重置所有群的bot对话轮数～")

    async def terminate(self):
        logger.info("[Bot同类] 插件已卸载")
