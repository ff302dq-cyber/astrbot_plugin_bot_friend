import sys
import types


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class _Filter:
    EventMessageType = types.SimpleNamespace(ALL="all")
    PermissionType = types.SimpleNamespace(ADMIN="admin")

    @staticmethod
    def _decorator(*_args, **_kwargs):
        return lambda function: function

    event_message_type = _decorator
    on_llm_request = _decorator
    on_decorating_result = _decorator
    command = _decorator
    permission_type = _decorator


class _Star:
    def __init__(self, context):
        self.context = context


class _Plain:
    def __init__(self, text):
        self.text = text


class _Reply:
    def __init__(self, id):
        self.id = id


def _register(*_args, **_kwargs):
    return lambda cls: cls


astrbot = types.ModuleType("astrbot")
api = types.ModuleType("astrbot.api")
event = types.ModuleType("astrbot.api.event")
provider = types.ModuleType("astrbot.api.provider")
star = types.ModuleType("astrbot.api.star")
components = types.ModuleType("astrbot.api.message_components")

api.AstrBotConfig = dict
api.logger = _Logger()
event.filter = _Filter()
event.AstrMessageEvent = object
provider.ProviderRequest = object
star.Context = object
star.Star = _Star
star.register = _register
components.Plain = _Plain
components.Reply = _Reply

astrbot.api = api
sys.modules.setdefault("astrbot", astrbot)
sys.modules.setdefault("astrbot.api", api)
sys.modules.setdefault("astrbot.api.event", event)
sys.modules.setdefault("astrbot.api.provider", provider)
sys.modules.setdefault("astrbot.api.star", star)
sys.modules.setdefault("astrbot.api.message_components", components)
