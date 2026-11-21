import os
import re
import tempfile
import time
import threading
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from gpu_platform_contracts.messages import SyncProgress
from gpu_platform_contracts.texts import Message

from .state import read_json, write_json


class LimitedOutput:
    def __init__(self, output, maximum_bytes):
        self.output, self.maximum_bytes, self.size = output, maximum_bytes, 0

    def write(self, data):
        if self.size + len(data) > self.maximum_bytes:
            raise ValueError("stream exceeds data limit")
        written = self.output.write(data)
        self.size += written
        return written

    def tell(self):
        return self.size


class Streams:
    def __init__(self, directory: Path, maximum_bytes: int = 1 << 40, receiver=None):
        self.directory = directory
        self.maximum_bytes = maximum_bytes
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.progress_directory = directory.parent / (directory.name + "-progress")
        self.progress_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.progress_lock = threading.Lock()
        self.archive_lock = threading.RLock()
        self.in_use: Counter[str] = Counter()
        self.source_directory = directory.parent / (directory.name + "-files")
        self.receiver_directory = directory.parent / (directory.name + "-receivers")
        self.receiver = receiver

    def file_source(self, stream_id, path, offset=0):
        self.path(stream_id)
        details = path.stat()
        if not 0 <= offset <= details.st_size or details.st_size - offset > self.maximum_bytes:
            raise ValueError("invalid file offset")
        self.source_directory.mkdir(mode=0o700, exist_ok=True)
        record = {"path": str(path), "offset": offset, "size_bytes": details.st_size - offset,
                  "mtime_ns": details.st_mtime_ns, "inode": details.st_ino}
        write_json(self.source_directory / (stream_id + ".json"), record)
        return record["size_bytes"]

    def file_receiver(self, stream_id, payload):
        self.path(stream_id)
        if self.receiver is None:
            raise ValueError("file receiver is unavailable")
        self.receiver_directory.mkdir(mode=0o700, exist_ok=True)
        write_json(self.receiver_directory / (stream_id + ".json"), payload)

    def exists(self, stream_id):
        return self.path(stream_id).is_file() or any(
            (directory / (stream_id + ".json")).is_file()
            for directory in [self.source_directory, self.receiver_directory]
        )

    @contextmanager
    def download(self, stream_id):
        with self.hold(stream_id), self.open_stream(stream_id) as stream:
            yield stream

    @contextmanager
    def hold(self, name):
        with self.archive_lock:
            self.in_use[name] += 1
        try:
            yield
        finally:
            with self.archive_lock:
                self.in_use[name] -= 1
                if not self.in_use[name]:
                    del self.in_use[name]

    @contextmanager
    def open_stream(self, stream_id):
        self.path(stream_id)
        record_path = self.source_directory / (stream_id + ".json")
        if record_path.exists():
            record = read_json(record_path, {})
            path = Path(record["path"])
            if path.is_symlink():
                raise ValueError("file source changed")
            with path.open("rb") as source:
                details = os.fstat(source.fileno())
                if (details.st_ino != record["inode"] or details.st_mtime_ns != record["mtime_ns"]
                        or details.st_size != record["offset"] + record["size_bytes"]):
                    raise ValueError("file source changed")
                source.seek(record["offset"])
                yield record["size_bytes"], self.read(stream_id, source)
        else:
            with self.path(stream_id).open("rb") as source:
                yield os.fstat(source.fileno()).st_size, self.read(stream_id, source)

    def progress(self, stream_id, state, size, total=None):
        self.path(stream_id)
        report = SyncProgress(
            id=stream_id,
            state=state,
            transferred_bytes=size,
            total_bytes=total,
            error=Message(code="stream.transfer_failed") if state == "failed" else None,
        )
        with self.progress_lock:
            write_json(self.progress_directory / (stream_id + ".json"), report.model_dump(mode="json"))

    def reports(self):
        with self.progress_lock:
            paths = sorted(
                self.progress_directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
            )[:100]
            return [SyncProgress.model_validate(read_json(path, {})) for path in paths]

    def track(self, stream_id, chunks, total=None):
        transferred, updated = 0, time.monotonic()
        self.progress(stream_id, "running", 0, total)
        try:
            for chunk in chunks:
                yield chunk
                transferred += len(chunk)
                if time.monotonic() - updated >= 0.25:
                    self.progress(stream_id, "running", transferred, total)
                    updated = time.monotonic()
        except BaseException:
            self.progress(stream_id, "failed", transferred, total)
            raise
        else:
            self.progress(stream_id, "completed", transferred, total if total is not None else transferred)

    def path(self, stream_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", stream_id):
            raise ValueError("invalid stream identifier")
        return self.directory / stream_id

    @contextmanager
    def writer(self, stream_id):
        destination = self.path(stream_id)
        with self.hold(stream_id):
            with self.archive_lock:
                fd, temporary = tempfile.mkstemp(prefix=f".incoming-{stream_id}.", dir=self.directory)
            try:
                with self.hold(Path(temporary).name), os.fdopen(fd, "wb") as output:
                    limited = LimitedOutput(output, self.maximum_bytes)
                    yield limited
                    output.flush()
                    os.fsync(output.fileno())
                    os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)

    def receive(self, stream_id: str, chunks: Iterable[bytes]) -> int:
        receiver = self.receiver_directory / (stream_id + ".json")
        if receiver.exists():
            payload = read_json(receiver, {})
            size = 0

            def limited():
                nonlocal size
                for chunk in chunks:
                    size += len(chunk)
                    if size > self.maximum_bytes:
                        raise ValueError("stream exceeds data limit")
                    yield chunk

            try:
                self.receiver(payload, self.track(stream_id, limited()))
            except BaseException:
                self.progress(stream_id, "failed", size)
                raise
            return size
        with self.writer(stream_id) as output:
            for chunk in self.track(stream_id, chunks):
                output.write(chunk)
            return output.size

    def send(self, stream_id: str) -> Iterable[bytes]:
        with self.download(stream_id) as (_, chunks):
            yield from chunks

    def read(self, stream_id, source):
        def chunks():
            while chunk := source.read(1024 * 1024):
                yield chunk

        yield from self.track(stream_id, chunks(), os.fstat(source.fileno()).st_size - source.tell())

    def remove(self, stream_id: str) -> None:
        with self.archive_lock:
            if stream_id in self.in_use:
                raise BlockingIOError("Stream is in use")
            self.path(stream_id).unlink(missing_ok=True)
            for partial in self.directory.glob(f".incoming-{stream_id}.*"):
                partial.unlink(missing_ok=True)
            for directory in [self.source_directory, self.receiver_directory, self.progress_directory]:
                (directory / (stream_id + ".json")).unlink(missing_ok=True)

    def clean_expired(self, maximum_age: int = 4 * 3600, *, retained=()) -> None:
        cutoff = time.time() - maximum_age
        with self.archive_lock:
            for item in self.directory.iterdir():
                if item.name not in retained and item.name not in self.in_use and item.is_file():
                    try:
                        if item.stat().st_mtime < cutoff:
                            item.unlink(missing_ok=True)
                    except FileNotFoundError:
                        pass
        with self.progress_lock:
            for item in self.progress_directory.glob("*.json"):
                if item.stat().st_mtime < cutoff:
                    item.unlink(missing_ok=True)


def relay(
    chunks: Iterable[bytes], bytes_per_second: int = 0, progress=None, clock=time.monotonic, sleep=time.sleep
):
    """Relay with a bounded working set and an optional transfer rate."""
    if bytes_per_second < 0:
        raise ValueError("bandwidth must be nonnegative")
    started = clock()
    transferred = 0
    for chunk in chunks:
        transferred += len(chunk)
        if bytes_per_second:
            delay = transferred / bytes_per_second - (clock() - started)
            if delay > 0:
                sleep(delay)
        yield chunk
        if progress is not None:
            progress(transferred)
