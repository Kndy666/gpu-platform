from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.slots.metrics import Metrics

from .sampler import Sampler


from .options import Options


class Module(Metrics):
    options_model = Options
    descriptor = ModuleDescriptor(name="host", slot="metrics", version="0.4.0", side="node", parameters=Options.model_json_schema())

    def __init__(self, context, sampler=None):
        super().__init__(context)
        self.sampler = sampler or Sampler({"storage": {"pools": context.options.get("pools", [])}})

    def sample(self):
        data = self.sampler.sample()
        result: dict[str, float | str | None] = {}

        def flatten(prefix, value):
            if isinstance(value, dict):
                for key, child in value.items():
                    flatten(f"{prefix}.{key}" if prefix else key, child)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    flatten(f"{prefix}.{index}", child)
            elif value is None or isinstance(value, str):
                result[prefix] = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                result[prefix] = float(value)

        for category in ("cpu", "memory", "gpus", "disks", "network"):
            flatten(category, data.get(category, {}))
        return result
