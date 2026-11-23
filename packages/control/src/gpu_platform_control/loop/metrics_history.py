"""Send telemetry to the archive without blocking reconciliation."""

import logging
from queue import Empty, Full, Queue
from threading import Event, Thread
import time

from gpu_platform_control.core.metrics_history import (
    FIELDS, HOUR, MINUTE, MINUTE_DELAY, MetricsArchive, NoArchiveSpace, PendingMetrics, floor,
)

log = logging.getLogger(__name__)


class HistoryRecorder:
    def __init__(self, archive: MetricsArchive):
        self.archive = archive
        self.queue: Queue[tuple[int, dict]] = Queue(maxsize=256)
        self.stopping = Event()
        self.dropped = Event()
        self.worker = Thread(target=self._run, name="metrics-history")

    def __enter__(self):
        self.worker.start()
        return self

    def __exit__(self, *exc):
        self.stopping.set()
        self.worker.join()

    def submit(self, node: int, data: dict):
        sample = {key: data[key] for key in ("sampled_at", *FIELDS) if key in data}
        try:
            self.queue.put_nowait((node, sample))
        except Full:
            self.dropped.set()

    def _run(self):
        pending = PendingMetrics()
        last_cycle = last_compaction = 0
        last_write = None
        last_state = None
        while True:
            now = int(time.time())
            try:
                node, data = self.queue.get(timeout=1 if not self.stopping.is_set() else 0)
            except Empty:
                if self.stopping.is_set():
                    break
            else:
                try:
                    pending.add(node, data, now)
                except (KeyError, TypeError, ValueError, OverflowError):
                    self.dropped.set()
            cycle = floor(now - MINUTE_DELAY, MINUTE)
            if cycle == last_cycle:
                continue
            last_cycle = cycle
            blocks = {}
            state = "waiting" if last_write is None else "ready"
            try:
                self.archive.root.mkdir(mode=0o700, parents=True, exist_ok=True)
                (self.archive.root / ".metrics-write.tmp").unlink(missing_ok=True)
                blocks = {block.path: block for block in self.archive.blocks()}
                if last_write is None:
                    saved = self.archive.status()["last_write"]
                    last_write = int(saved) if saved is not None else None
                    state = "ready" if last_write is not None else "waiting"
                compaction = floor(now - MINUTE_DELAY, HOUR)
                try:
                    if pending.flush(self.archive, blocks, now):
                        last_write = now
                        state = "ready"
                finally:
                    if last_compaction != compaction:
                        self.archive.maintain(blocks, now)
                        last_compaction = compaction
                if self.dropped.is_set():
                    self.dropped.clear()
                    state = "dropped"
            except NoArchiveSpace:
                state = "space"
            except (OSError, ValueError, OverflowError):
                state = "error"
                if last_state != state:
                    log.exception("Metrics history write failed")
            if state != last_state and state in {"space", "dropped"}:
                log.warning("Metrics history status: %s", state)
            try:
                self.archive.write_status(state, last_write, blocks)
            except OSError:
                pass
            last_state = state
