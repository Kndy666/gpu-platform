"""Durable retry identities for bulk work that must not block task expiry."""

import hashlib
import json
import threading
import time
from pathlib import Path

from gpu_platform_contracts.messages import Reply, SyncProgress
from gpu_platform_contracts.texts import Message

from .state import read_json, write_json


class Operations:
    def __init__(self, directory: Path, stream_exists):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.stream_exists = stream_exists
        self.lock = threading.Lock()
        self.running: set[str] = set()

    def clean_expired(self, maximum_age=86400):
        with self.lock:
            for path in self.directory.glob("*.json"):
                if path.stem not in self.running and path.stat().st_mtime < time.time() - maximum_age:
                    path.unlink(missing_ok=True)

    def reports(self):
        with self.lock:
            paths = sorted(
                self.directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
            )[:100]
            return [
                SyncProgress.model_validate(record["sync"])
                for path in paths
                if (record := read_json(path, {})).get("sync")
            ]

    def request(self, command, execute, *, cache_result=True):
        identity = json.dumps(
            [command.operation.value, command.payload], sort_keys=True, separators=(",", ":")
        )
        key = hashlib.sha256(identity.encode()).hexdigest()
        path = self.directory / (key + ".json")
        with self.lock:
            record = read_json(path, {})
            if record.get("reply"):
                reply = Reply.model_validate(record["reply"])
                stream_id = reply.data.get("stream_id")
                if not stream_id or not cache_result or self.stream_exists(stream_id):
                    if not cache_result:
                        path.unlink(missing_ok=True)
                    if not reply.ok or reply.data.get("state") in {"queued", "running"}:
                        path.unlink(missing_ok=True)
                    return reply.model_copy(update={"id": command.id})
                path.unlink(missing_ok=True)
            if key not in self.running and not self.running:
                # Only one bulk worker mutates archive/registry state at a time.
                # Pending records resume lazily when a controller retries after restart.
                record = {"operation": command.operation.value, "state": "pending"}
                if command.operation.value in {
                    "image.export",
                    "image.import",
                    "datasets.export",
                    "datasets.import",
                    "datasets.manifest",
                }:
                    record["sync"] = SyncProgress(
                        id=str(command.payload.get("transfer_id") or command.payload.get("stream_id") or key),
                        state="running",
                    ).model_dump(mode="json")
                write_json(path, record)
                self.running.add(key)

                def worker():
                    try:
                        reply = execute(command)
                        record["reply"] = reply.model_dump(mode="json")
                        if record.get("sync"):
                            record["sync"].update(
                                state="completed" if reply.ok else "failed",
                                error=reply.error.model_dump(mode="json") if reply.error else None,
                            )
                            if "size_bytes" in reply.data:
                                record["sync"].update(
                                    transferred_bytes=reply.data["size_bytes"],
                                    total_bytes=reply.data["size_bytes"],
                                )
                        write_json(path, record)
                    finally:
                        with self.lock:
                            self.running.discard(key)

                threading.Thread(target=worker, daemon=True, name="node-bulk").start()
        return Reply(id=command.id, ok=False, error=Message(code="operation.pending"))
