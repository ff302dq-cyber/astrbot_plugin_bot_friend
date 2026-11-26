"""撤回同步插件（astrbot_plugin_recall_sync）的登记口。没装那个插件时全部静默跳过。"""
import sys


def _api():
    return sys.modules.get("astrbot_recall_sync")


def is_recalled(event) -> bool:
    """触发这条事件的消息是不是已经被撤回了"""
    m = _api()
    f = getattr(m, "is_recalled", None) if m else None
    try:
        return bool(f(event)) if f else False
    except Exception:
        return False


async def register_reply(event, message_ids=None, t_start=None) -> None:
    """告诉撤回同步插件：这几条消息是回复 event 的，原消息撤回时一起撤"""
    m = _api()
    f = getattr(m, "register_reply", None) if m else None
    if not f:
        return
    try:
        await f(event, message_ids=message_ids, t_start=t_start)
    except Exception:
        pass
