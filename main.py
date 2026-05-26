"""
AstrBot 插件：Bot同类识别器
让Bot认识其他Bot同类，支持专属提示词、自动识别回复。
"""

import re
import random
from collections import defaultdict

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain


@register(
    "bot_friend_recognizer",
    "YourName",
    "让Bot认识其他Bot同类，支持专属提示词",
    "1.0.0",
    "https://github.com/ff302dq-cyber/astrbot_plugin_bot_friend"
)
class BotFriendPlugin(Star):

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.bot_friends = self._parse_bot_friends()
        self.auto_reply_counter = defaultdict(int)
        self.max_auto_rounds = int(self.config.get("max_auto_rounds", 10))
        self.min_auto_rounds = int(self.config.get("min_auto_rounds", 3))
        self.reply_format_prompt = self.config.get("reply_format_prompt",
            "你的回复必须自然并严格遵守人物关系。回复严禁超过20字，不得含有任何加粗、格式、emoji、markdown。")
        # 记录每组对话的随机截断值
        self.random_cutoff = {}
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
            logger.info(f"[Bot同类] 加载同类: {'/'.join(names)} (QQ:{qq}, 唤醒词:{wake_prefix or '无'})")

        return friends

    def _reload_config(self):
        """重新加载配置"""
        self.bot_friends = self._parse_bot_friends()
        self.max_auto_rounds = int(self.config.get("max_auto_rounds", 10))
        self.min_auto_rounds = int(self.config.get("min_auto_rounds", 3))
        self.reply_format_prompt = self.config.get("reply_format_prompt",
            "你的回复必须自然并严格遵守人物关系。回复严禁超过20字，不得含有任何加粗、格式、emoji、markdown。")

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
        except Exception:
            pass
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
        except Exception:
            pass
        return msg

    def _get_message_chain(self, event: AstrMessageEvent):
        if hasattr(event, "message_chain") and event.message_chain:
            return event.message_chain
        if hasattr(event, "message_obj") and hasattr(event.message_obj, "message"):
            return event.message_obj.message or []
        return []

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
        except Exception:
            return False

    def _init_random_cutoff(self, group_id: str, target_qq: str):
        """初始化随机截断值"""
        key = f"{group_id}_{target_qq}"
        if key not in self.random_cutoff:
            if self.min_auto_rounds > 0 and self.max_auto_rounds > self.min_auto_rounds:
                self.random_cutoff[key] = random.randint(self.min_auto_rounds + 1, self.max_auto_rounds)
            else:
                self.random_cutoff[key] = self.max_auto_rounds
            logger.info(f"[BotFriend] 群{group_id}与{target_qq}的随机截断轮数: {self.random_cutoff[key]}")
        return self.random_cutoff[key]

    def _mark_force_prefix(self, event: AstrMessageEvent, wake_prefix: str):
        """记录本次回复必须带上的唤醒词前缀。"""
        if wake_prefix:
            setattr(event, "_bot_friend_force_prefix", wake_prefix)

    def _mark_no_segmented_reply(self, event: AstrMessageEvent):
        """标记本次回复不走 AstrBot 的 LLM 正则分段。"""
        setattr(event, "_bot_friend_no_segmented_reply", True)

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
        except Exception as e:
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
        except Exception as e:
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

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """forw 走硬拼接直发，不经过大模型。"""
        self._reload_config()
        parsed = self._parse_forw_chain(event)
        if not parsed:
            return
        name, content_chain = parsed

        bot_info = self._find_by_name(name)
        if not bot_info:
            return

        wake_prefix = bot_info.get("wake_prefix", "")
        target_qq = str(bot_info["qq"])
        logger.info(f"[BotFriend] forw硬转发: {name}, 对方唤醒词:{wake_prefix}")

        group_id = event.message_obj.group_id
        if group_id:
            self.auto_reply_counter[f"{group_id}_{target_qq}"] = 0

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
        # 尝试多种方式获取消息内容
        msg = ""
        try:
            # 优先尝试 message_chain
            if hasattr(event, 'message_chain'):
                for comp in event.message_chain:
                    if hasattr(comp, 'text'):
                        msg += comp.text
            # 其次尝试 message_str
            elif hasattr(event, 'message_str'):
                msg = event.message_str
            # 最后尝试 message_obj
            elif hasattr(event, 'message_obj') and hasattr(event.message_obj, 'message'):
                for comp in event.message_obj.message:
                    if hasattr(comp, 'text'):
                        msg += comp.text
        except Exception as e:
            logger.warning(f"[BotFriend] 获取消息内容失败: {e}")

        msg = self._strip_my_wake_prefix(msg)

        logger.info(f"[BotFriend] 收到消息: {msg}, repr: {repr(msg)}")

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
                        if self.reply_format_prompt:
                            addition += f"\n\n{self.reply_format_prompt}"
                        if friend_prompt:
                            addition += f"\n\n【同类关系提示】{friend_prompt}"
                        addition += (
                            f"\n\n【当前任务】你要对朋友「{name}」说话。"
                            f"你的回复必须以「{wake_prefix}」开头，这是对方的唤醒词。"
                            f"重要：直接说出你想说的话，不要添加任何转述、不要说\"用户说\"、\"告诉你\"之类的。"
                            f"比如用户给\"你是大笨蛋\"，你应该直接说\"{wake_prefix}你是大笨蛋\"或\"{wake_prefix}你真笨\"，"
                            f"而不是\"用户说你是大笨蛋\"。"
                        )
                        req.system_prompt += addition
                        self._mark_force_prefix(event, wake_prefix)
                        self._mark_no_segmented_reply(event)
                        # 重置自动回复计数
                        group_id = event.message_obj.group_id
                        if group_id:
                            self.auto_reply_counter[f"{group_id}_{target_qq}"] = 0
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
            addition = (
                f"\n\n【同类关系提示】当前和你说话的是你的同类「{name_str}」。"
            )
            if friend_prompt:
                addition += friend_prompt
            if self.reply_format_prompt:
                addition += f"\n\n{self.reply_format_prompt}"
            if sender_wake:
                addition += (
                    f"\n重要：你的回复必须以「{sender_wake}」开头，这是对方的唤醒词，用来继续对话。"
                )
                self._mark_force_prefix(event, sender_wake)
                self._mark_no_segmented_reply(event)
            req.system_prompt += addition
            logger.info(f"[Bot同类] 检测到同类「{name_str}」的消息，回复将添加唤醒词前缀「{sender_wake}」")

    # ============================================================
    #  功能3: 处理自动回复的对话轮数限制和随机截断
    # ============================================================

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """
        检查是否需要截断对话，并处理轮数限制
        """
        sender_id = str(event.get_sender_id())
        msg = event.message_str.strip()

        result = event.get_result()
        if not result or not result.chain:
            return

        self._disable_segmented_reply_for_result(event)

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
            self._ensure_result_prefix(event, wake_prefix)
            self._strip_trailing_wake_prefix(event, wake_prefix)
            return  # 非群聊不处理

        target_qq = str(bot_info["qq"])
        counter_key = f"{group_id}_{target_qq}"

        # 初始化随机截断值
        cutoff_rounds = self._init_random_cutoff(group_id, target_qq)

        # 增加计数
        self.auto_reply_counter[counter_key] += 1
        current_round = self.auto_reply_counter[counter_key]

        logger.info(
            f"[BotFriend] 与{bot_info['names'][0]}自动回复第"
            f"{current_round}/{cutoff_rounds}轮"
        )

        # 检查是否超过截断轮数
        if current_round >= cutoff_rounds:
            logger.info(
                f"[BotFriend] 群{group_id}与{target_qq}达到截断轮数({cutoff_rounds}轮)，停止自动回复"
            )
            # 清空消息链，阻止回复
            result.chain = []
            # 清除计数，下次对话重新开始
            self.auto_reply_counter[counter_key] = 0
            if counter_key in self.random_cutoff:
                del self.random_cutoff[counter_key]
            return

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
        self.auto_reply_counter.clear()
        self.random_cutoff.clear()
        yield event.plain_result("已重置所有群的bot对话轮数～")

    async def terminate(self):
        logger.info("[Bot同类] 插件已卸载")
