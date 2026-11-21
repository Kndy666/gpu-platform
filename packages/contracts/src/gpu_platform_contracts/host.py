"""Portable ownership journal for reversible module installation hooks."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path


class HostOwnership:
    """Remove created artifacts; restore only configuration entries still owned by this module.

    Package databases, directories and user data are deliberately outside this helper.
    Journals survive interrupted hooks and are consumed after each successful undo.
    """

    def __init__(self, context, name):
        self.context = context
        self.path = context.state_directory / "host-ownership" / (name + ".json")
        self.records = json.loads(self.path.read_text()) if self.path.exists() else []

    def save(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.records))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    @staticmethod
    def fingerprint(path):
        if path.is_symlink():
            return "link:" + str(path.readlink())
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        return None

    @contextmanager
    def files(self, paths, *, before_remove=None):
        tracked = []
        for value in paths:
            path = Path(value)
            record = next((r for r in self.records if r["key"] == "file:" + str(path)), None)
            if record is None and not path.exists() and not path.is_symlink():
                record = {"key": "file:" + str(path), "kind": "file", "path": str(path),
                          "fingerprint": None, "commands": (before_remove or {}).get(str(path), [])}
                self.records.append(record)
            if record is not None:
                tracked.append(record)
        self.save()
        try:
            yield
        finally:
            for record in tracked:
                record["fingerprint"] = self.fingerprint(Path(record["path"]))
            self.save()

    def action(self, key, commands):
        """Record an undo before a command creates a previously absent external object."""
        if not any(record["key"] == key for record in self.records):
            self.records.append({"key": key, "kind": "action", "commands": commands})
            self.save()

    @staticmethod
    def changes(before, after, prefix=()):
        for key in before.keys() | after.keys():
            old, new = before.get(key), after.get(key)
            if isinstance(new, dict) and (isinstance(old, dict) or key not in before):
                yield from HostOwnership.changes(old or {}, new, (*prefix, key))
            elif key not in before or key not in after or old != new:
                yield {"keys": [*prefix, key], "before": old, "before_exists": key in before,
                       "after": new, "after_exists": key in after}

    @contextmanager
    def configuration(self, path, *, load=json.loads, dump=json.dumps, format="json"):
        path = Path(path)
        existed = path.exists()
        before = (load(path.read_text()) or {}) if existed else {}
        if not isinstance(before, dict):
            raise ValueError("Host configuration must be a mapping")
        # Save the baseline before an external configurator runs, including crash recovery.
        key = "configuration:" + str(path)
        record = next((r for r in self.records if r["key"] == key), None)
        if record is None:
            record = {"key": key, "kind": "configuration", "path": str(path),
                      "format": format, "existed": existed, "changes": []}
            self.records.append(record)
        record["pending_before"] = before
        self.save()
        try:
            yield
        finally:
            after = (load(path.read_text()) or {}) if path.exists() else {}
            for change in self.changes(before, after):
                prior = next((c for c in record["changes"] if c["keys"] == change["keys"]), None)
                if prior:
                    prior.update(after=change["after"], after_exists=change["after_exists"])
                else:
                    record["changes"].append(change)
            record.pop("pending_before", None)
            self.save()

    def cleanup(self, *, formats=None):
        formats = {"json": (json.loads, json.dumps), **(formats or {})}
        changed = False
        for record in list(reversed(self.records)):
            if record["kind"] == "action":
                for command in record["commands"]:
                    self.context.runner(command)
                changed = True
            elif record["kind"] == "file":
                path = Path(record["path"])
                actual = self.fingerprint(path)
                if actual is not None:
                    # A changed file belongs to the operator now; preserve it.
                    if record["fingerprint"] is None or actual != record["fingerprint"]:
                        continue
                    for command in record["commands"]:
                        self.context.runner(command)
                    path.unlink()
                    changed = True
            else:
                path = Path(record["path"])
                load, dump = formats[record["format"]]
                current = (load(path.read_text()) or {}) if path.exists() else {}
                if "pending_before" in record:
                    record["changes"].extend(self.changes(record["pending_before"], current))
                for change in reversed(record["changes"]):
                    parent = current
                    ancestors = []
                    for key in change["keys"][:-1]:
                        if not isinstance(parent.get(key), dict):
                            parent = None
                            break
                        ancestors.append((parent, key))
                        parent = parent[key]
                    if parent is None:
                        continue
                    key = change["keys"][-1]
                    if (key in parent) != change["after_exists"] or parent.get(key) != change["after"]:
                        continue
                    if change["before_exists"]:
                        parent[key] = change["before"]
                    else:
                        parent.pop(key, None)
                    for ancestor, name in reversed(ancestors):
                        if not ancestor[name]:
                            ancestor.pop(name)
                if current or record["existed"]:
                    path.write_text(dump(current))
                else:
                    path.unlink(missing_ok=True)
                changed = True
            self.records.remove(record)
            self.save()
        return changed
