from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.slots.notify import IN_APP_CHANNEL, InboxDelivery, Notify


class Module(Notify):
    descriptor = ModuleDescriptor(name=IN_APP_CHANNEL, slot="notify", version="0.4.0", side="control")

    def recipient_fields(self):
        return {}

    def send(self, recipient, event, language):
        return InboxDelivery(title=event.parameters["title"], content=event.parameters["content"])
