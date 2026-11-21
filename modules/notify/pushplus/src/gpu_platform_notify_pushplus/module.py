import requests

from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.slots.notify import Notify
from gpu_platform_contracts.texts import LocalizedText


from .options import Options


class Module(Notify):
    options_model = Options
    descriptor = ModuleDescriptor(
        parameters=Options.model_json_schema(),
        name="pushplus",
        slot="notify",
        version="0.4.0",
        side="control",
        texts={"token": LocalizedText(en="PushPlus token", zh_hans="PushPlus 令牌")},
    )

    def __init__(self, context, session=None):
        super().__init__(context)
        self.session = session or requests.Session()

    def recipient_fields(self):
        return {"token": {"type": "string", "secret": True, "required": True, "pattern": r"^[0-9a-fA-F]{32}$"}}

    def send(self, recipient, event, language):
        token = recipient.get("token")
        if not token:
            raise ValueError("notify.recipient_missing")
        # The event service supplies text rendered in the recipient's language.
        title = event.parameters.get("title", event.code)
        content = event.parameters.get("content", "")
        response = self.session.post(
            "https://www.pushplus.plus/send",
            json={
                "token": token,
                "title": title,
                "content": content,
                "template": "txt",
                "channel": "wechat",
            },
            timeout=(3, 8),
            allow_redirects=False,
        )
        response.raise_for_status()
        if response.json().get("code") != 200:
            raise RuntimeError("notify.delivery_rejected")
