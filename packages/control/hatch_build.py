"""Include the repository's canonical Agent skill in wheels and source archives."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        source = Path(self.root).parents[1] / "skills" / "gpu-platform"
        if source.is_dir():
            destination = "gpu_platform_control/agent-skill/gpu-platform"
            if self.target_name == "sdist":
                destination = "src/" + destination
            build_data["force_include"][str(source)] = destination
