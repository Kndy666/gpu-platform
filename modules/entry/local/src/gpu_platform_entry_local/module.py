from pydantic import Field
from gpu_platform_contracts.base import Contract
from gpu_platform_contracts.handoffs import LoginAddress
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.slots.entry import Entry


class Options(Contract):
    public_host: str | None = Field(default=None, min_length=1,
        description="Public hostname returned to users; defaults to the supplied port connection host.")


class Module(Entry):
    options_model = Options
    descriptor = ModuleDescriptor(
        name="local", slot="entry", version="0.4.0", side="both", accepts={"port": {"tcp"}},
        parameters=Options.model_json_schema(),
    )

    def __init__(self, context):
        super().__init__(context)
        self.addresses = {}

    def open(self, task_id, username, connection, public_port):
        if public_port is not None and public_port != connection.port:
            raise ValueError("local_port_mismatch")
        address = LoginAddress(
            host=self.context.options.get("public_host") or connection.host,
            port=connection.port,
            username=username,
            task_id=task_id,
        )
        self.addresses[task_id] = address
        return address

    def close(self, task_id):
        self.addresses.pop(task_id, None)

    def address(self, task_id):
        return self.addresses.get(task_id)
