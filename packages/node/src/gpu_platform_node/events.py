"""Persist module events until the controller commits and acknowledges them."""

import threading
from uuid import UUID

from gpu_platform_contracts.events import ModuleEvent

from .state import read_json, write_json


class Events:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.Lock()

    def publish(self, event):
        event = ModuleEvent.model_validate(event)
        if len(event.model_dump_json().encode()) > 65536:
            raise ValueError("Module event exceeds its message limit")
        path = self.directory / (str(event.id) + ".json")
        data = event.model_dump(mode="json")
        with self.lock:
            if path.exists() and read_json(path, {}) != data:
                raise ValueError("Module event identity conflict")
            write_json(path, data)

    def pending(self):
        result: list[ModuleEvent] = []
        size = 0
        with self.lock:
            for path in sorted(self.directory.glob("*.json")):
                data = path.read_bytes()
                if len(result) == 100 or size + len(data) > 256 * 1024:
                    break
                result.append(ModuleEvent.model_validate_json(data))
                size += len(data)
        return result

    def acknowledge(self, identifiers):
        paths = [self.directory / (str(UUID(str(identifier))) + ".json") for identifier in identifiers]
        with self.lock:
            for path in paths:
                path.unlink(missing_ok=True)
