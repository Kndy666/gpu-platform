"""Confined public-directory access and resumable file synchronization."""

from __future__ import annotations
import os
import hashlib
import json
import math
import shutil
from pathlib import Path, PurePosixPath
from typing import List
import threading

from gpu_platform_contracts.handoffs import ReadOnlyDirectory
from .state import read_json, write_json


TRANSACTION_DIRECTORY = ".gpu-platform-dataset-sync"


def relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value
            or path.parts[:1] == (TRANSACTION_DIRECTORY,)):
        raise ValueError("invalid dataset path")
    return path


def selected_paths(paths: List[str]) -> List[PurePosixPath]:
    selected = sorted({relative_path(value) for value in (paths or [""])}, key=lambda value: len(value.parts))
    result: List[PurePosixPath] = []
    for value in selected:
        if not any(
            parent == PurePosixPath(".") or parent == value or parent in value.parents for parent in result
        ):
            result.append(value)
    return result


class Datasets:
    def __init__(self, root: Path, node_id: str):
        self.root = root.resolve()
        self.node_id = node_id
        self.transactions = self.root / TRANSACTION_DIRECTORY
        self.transaction_prefix = ".dataset-" + hashlib.sha256(str(self.root).encode()).hexdigest()[:16] + "-"
        self.journal = self.transactions / "commit.json"
        self.lock = threading.RLock()
        self.recover()

    def remove_empty_transactions(self):
        if self.transactions.is_dir() and not any(self.transactions.iterdir()):
            self.transactions.rmdir()

    def commit(self, staged, selections, receipt):
        self.root.mkdir(parents=True, exist_ok=True)
        if selections == [PurePosixPath(".")]:
            names = sorted(
                {path.name for path in self.root.iterdir() if path != self.transactions}
                | {path.name for path in staged.iterdir()}
            )
        else:
            names = [str(path) for path in selections]
        record = {
            "staging": str(staged.parent),
            "receipt": receipt,
            "entries": [
                {
                    "path": name,
                    "replacement": staged.joinpath(*relative_path(name).parts).exists(),
                    "old_moved": False,
                    "installed": False,
                }
                for name in names
            ],
        }
        write_json(self.journal, record)
        self.recover()

    def recover(self):
        if self.transactions.is_symlink():
            raise ValueError("invalid dataset transaction directory")
        if not self.journal.exists():
            return
        record = read_json(self.journal, {})
        stage = Path(record["staging"])
        if (
            stage.parent != self.transactions
            or not stage.name.startswith(self.transaction_prefix)
            or stage.is_symlink()
        ):
            raise ValueError("invalid dataset transaction")
        for index, entry in enumerate(record["entries"]):
            relative = relative_path(entry["path"])
            if relative == PurePosixPath("."):
                raise ValueError("invalid dataset commit path")
            destination = self.target_path(str(relative))
            backup = stage / "backups" / str(index)
            replacement = stage / "payload" / relative
            if not entry["old_moved"]:
                if not backup.exists() and (destination.exists() or destination.is_symlink()):
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(destination, backup)
                entry["old_moved"] = True
                write_json(self.journal, record)
            if not entry["installed"]:
                if entry["replacement"]:
                    if replacement.exists():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(replacement, destination)
                    elif not destination.exists():
                        raise ValueError("dataset transaction lost its replacement")
                entry["installed"] = True
                write_json(self.journal, record)
        receipt = record["receipt"]
        self.receipt_path(receipt["id"]).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_json(self.receipt_path(receipt["id"]), receipt)
        if stage.exists():
            shutil.rmtree(stage)
        self.journal.unlink()
        self.remove_empty_transactions()

    def resolve(self, value: str) -> Path:
        relative_path(value)
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root) or path.is_relative_to(self.transactions):
            raise ValueError("dataset path escapes its root")
        return path

    def list(self, value: str = "") -> list[dict]:
        directory = self.resolve(value)
        result = []
        for child in sorted(directory.iterdir()):
            if child.is_symlink() or child == self.transactions:
                continue
            details = child.stat()
            result.append(
                {
                    "path": child.relative_to(self.root).as_posix(),
                    "name": child.name,
                    "directory": child.is_dir(),
                    "kind": "directory" if child.is_dir() else "file",
                    "size_bytes": details.st_size,
                    "modified": details.st_mtime,
                }
            )
        return result

    def handoff(self, value: str) -> ReadOnlyDirectory:
        path = self.resolve(value)
        if not path.is_dir():
            raise ValueError("dataset is not a directory")
        return ReadOnlyDirectory(
            form="local-directory",
            node_id=self.node_id,
            path=str(path),
            mount_path=f"/datasets/{value}".rstrip("/"),
        )

    @staticmethod
    def checksum(path):
        result = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                result.update(chunk)
        return result.hexdigest()

    def manifest(self, paths):
        self.recover()
        selections = selected_paths(paths)
        entries = {}
        for selected in selections:
            original = self.root.joinpath(*selected.parts)
            if original.is_symlink():
                raise ValueError("dataset symlinks cannot be synchronized")
            source = self.resolve(str(selected))
            if not source.exists():
                continue
            candidates = [source]
            if source.is_dir():
                for directory, children, files in os.walk(source, followlinks=False):
                    parent = Path(directory)
                    children[:] = [name for name in children if not (parent / name).is_symlink()
                                   and parent / name != self.transactions]
                    candidates.extend(parent / name for name in [*children, *files])
            for candidate in candidates:
                if candidate.is_symlink() or not (candidate.is_dir() or candidate.is_file()):
                    continue
                name = candidate.relative_to(self.root).as_posix()
                details = candidate.stat()
                entry = {"kind": "directory" if candidate.is_dir() else "file", "modified": details.st_mtime}
                if candidate.is_file():
                    entry.update(size_bytes=details.st_size, sha256=self.checksum(candidate))
                entries[name] = entry
                if len(entries) > 200000:
                    raise ValueError("dataset manifest contains too many entries")
        result = {"version": 1, "paths": [str(path) for path in selections], "entries": entries}
        self.encode_manifest(result)
        return result

    @staticmethod
    def encode_manifest(manifest):
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > 64 * 1024 * 1024:
            raise ValueError("dataset manifest exceeds metadata limit")
        return encoded

    @staticmethod
    def read_manifest(source, paths):
        encoded = source.read(64 * 1024 * 1024 + 1)
        if len(encoded) > 64 * 1024 * 1024:
            raise ValueError("dataset manifest exceeds metadata limit")
        document = json.loads(encoded)
        selections = selected_paths(paths)
        if not isinstance(document, dict) or document.get("paths") != [str(path) for path in selections]:
            raise ValueError("dataset manifest selection mismatch")
        entries = document.get("entries")
        if not isinstance(entries, dict) or len(entries) > 200000:
            raise ValueError("invalid dataset manifest entries")
        for name, entry in entries.items():
            relative = relative_path(name)
            if name != str(relative) or not any(
                relative == item or item == PurePosixPath(".") or item in relative.parents
                for item in selections
            ):
                raise ValueError("manifest entry outside selected paths")
            if not isinstance(entry, dict) or entry.get("kind") not in {"file", "directory"}:
                raise ValueError("invalid dataset manifest entry")
            modified = entry.get("modified", 0)
            if not isinstance(modified, (int, float)) or not math.isfinite(modified) or modified < 0:
                raise ValueError("invalid dataset timestamp")
            if entry["kind"] == "file":
                size, checksum = entry.get("size_bytes"), entry.get("sha256", "")
                if (
                    type(size) is not int
                    or size < 0
                    or len(checksum) != 64
                    or any(char not in "0123456789abcdef" for char in checksum)
                ):
                    raise ValueError("invalid dataset file descriptor")
        return document

    def session_path(self, identifier):
        return self.transactions / (self.transaction_prefix + hashlib.sha256(identifier.encode()).hexdigest())

    def receipt_path(self, identifier):
        return self.transactions / "receipts" / (hashlib.sha256(identifier.encode()).hexdigest() + ".json")

    def target_path(self, name):
        parts = relative_path(name).parts
        parent = self.root
        for part in parts[:-1]:
            parent = parent / part
            if parent.is_symlink():
                raise ValueError("dataset destination contains a symlink")
        return self.root.joinpath(*parts)

    def begin(self, identifier, source, paths):
        with self.lock:
            manifest = self.read_manifest(source, paths)
            digest = hashlib.sha256(self.encode_manifest(manifest)).hexdigest()
            receipt = read_json(self.receipt_path(identifier), {})
            if receipt:
                if receipt["manifest"] != digest:
                    raise ValueError("dataset synchronization identity conflict")
                return {"completed": True}
            for selected in selected_paths(paths):
                if self.target_path(str(selected)).is_symlink():
                    raise ValueError("dataset destination contains a symlink")
            directory = self.session_path(identifier)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            (directory / "payload").mkdir(exist_ok=True)
            plan = {"manifest": manifest, "digest": digest}
            stored = read_json(directory / "plan.json", {})
            if stored and stored != plan:
                raise ValueError("dataset synchronization identity conflict")
            write_json(directory / "plan.json", plan)
            return {"completed": False}

    @staticmethod
    def fingerprint(path):
        stat = path.stat()
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]

    def file_record(self, identifier, name):
        relative = relative_path(name)
        directory = self.session_path(identifier)
        plan = read_json(directory / "plan.json", {})
        entry = plan.get("manifest", {}).get("entries", {}).get(str(relative))
        if not entry or entry["kind"] != "file":
            raise ValueError("file is outside synchronization manifest")
        return directory, entry, directory / "payload" / relative

    def prepare_file(self, identifier, name):
        with self.lock:
            directory, entry, partial = self.file_record(identifier, name)
            verified = read_json(directory / "verified.json", {})
            if partial.is_file() and verified.get(name) == self.fingerprint(partial):
                return {"offset": entry["size_bytes"], "complete": True}
            if not partial.exists():
                candidate = self.target_path(name)
                if (candidate.is_file() and not candidate.is_symlink() and candidate.resolve() == candidate
                        and candidate.stat().st_size == entry["size_bytes"]):
                    before = self.fingerprint(candidate)
                    if self.checksum(candidate) == entry["sha256"] and self.fingerprint(candidate) == before:
                        partial.parent.mkdir(parents=True, exist_ok=True)
                        os.link(candidate, partial)
                        verified[name] = self.fingerprint(partial)
                        write_json(directory / "verified.json", verified)
                        return {"offset": entry["size_bytes"], "complete": True}
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > entry["size_bytes"]:
                raise ValueError("partial file exceeds declared size")
            return {"offset": offset, "complete": False}

    def receive_file(self, identifier, name, offset, chunks):
        with self.lock:
            _, entry, partial = self.file_record(identifier, name)
            actual = partial.stat().st_size if partial.exists() else 0
            if type(offset) is not int or actual != offset or not 0 <= offset <= entry["size_bytes"]:
                raise ValueError("partial file offset changed")
            partial.parent.mkdir(parents=True, exist_ok=True)
            count = offset
            with partial.open("ab") as output:
                try:
                    for chunk in chunks:
                        count += len(chunk)
                        if count > entry["size_bytes"]:
                            raise ValueError("file exceeds declared size")
                        output.write(chunk)
                finally:
                    output.flush()
                    os.fsync(output.fileno())
            if count != entry["size_bytes"]:
                raise ConnectionError("file transfer is incomplete")

    def verify_file(self, identifier, name):
        with self.lock:
            directory, entry, path = self.file_record(identifier, name)
            before = self.fingerprint(path) if path.exists() else None
            if (before is None or before[2] != entry["size_bytes"] or self.checksum(path) != entry["sha256"]
                    or self.fingerprint(path) != before):
                path.unlink(missing_ok=True)
                return False
            path.chmod(0o644)
            os.utime(path, (entry["modified"], entry["modified"]))
            verified = read_json(directory / "verified.json", {})
            verified[name] = self.fingerprint(path)
            write_json(directory / "verified.json", verified)
            return True

    def finish(self, identifier):
        with self.lock:
            if self.receipt_path(identifier).exists():
                return
            directory = self.session_path(identifier)
            plan = read_json(directory / "plan.json", {})
            manifest = plan["manifest"]
            verified = read_json(directory / "verified.json", {})
            for name, entry in manifest["entries"].items():
                path = directory / "payload" / relative_path(name)
                if entry["kind"] == "directory":
                    path.mkdir(parents=True, exist_ok=True)
                elif not path.is_file() or verified.get(name) != self.fingerprint(path):
                    raise ValueError("synchronized file has not been verified")
            self.commit(directory / "payload", selected_paths(manifest["paths"]),
                        {"id": identifier, "manifest": plan["digest"]})

    def cleanup(self, identifier):
        with self.lock:
            self.recover()
            directory = self.session_path(identifier)
            if directory.exists():
                shutil.rmtree(directory)
            self.receipt_path(identifier).unlink(missing_ok=True)
            receipts = self.transactions / "receipts"
            if receipts.is_dir() and not any(receipts.iterdir()):
                receipts.rmdir()
            self.remove_empty_transactions()
