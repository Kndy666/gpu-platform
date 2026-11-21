"""Validate source message coverage and compile deterministic gettext catalogs."""

import ast
import re
import struct
from pathlib import Path
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError


def read_catalog(path):
    entries = {}
    current: dict[str, str] = {}
    key = None
    for line in path.read_text().splitlines() + [""]:
        line = line.strip()
        if not line:
            if "msgid" in current:
                entries[current["msgid"]] = current.get("msgstr", "")
            current = {}
            key = None
        elif line.startswith("msgid ") or line.startswith("msgstr "):
            key, value = line.split(" ", 1)
            current[key] = ast.literal_eval(value)
        elif line.startswith('"') and key:
            current[key] += ast.literal_eval(line)
    return entries


def compile_catalog(entries, path):
    pairs = sorted((key.encode(), value.encode()) for key, value in entries.items())
    count = len(pairs)
    offset = 28 + 16 * count
    sources, targets = b"", b""
    source_table, target_table = [], []
    for source, target in pairs:
        source_table.append((len(source), offset + len(sources)))
        sources += source + b"\0"
    for source, target in pairs:
        target_table.append((len(target), offset + len(sources) + len(targets)))
        targets += target + b"\0"
    data = struct.pack("<7I", 0x950412DE, 0, count, 28, 28 + 8 * count, 0, 0)
    data += b"".join(struct.pack("<2I", *row) for row in source_table + target_table)
    path.write_bytes(data + sources + targets)


def source_messages(root):
    messages: dict[str, set[str]] = {"django": set(), "djangojs": set()}
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            if name in {"_", "tr", "gettext", "gettext_lazy", "ngettext"}:
                for arg in node.args[: 2 if name == "ngettext" else 1]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        messages["django"].add(arg.value)
    from django.utils.translation import templatize

    for path in root.rglob("*.html"):
        extracted = templatize(path.read_text(), origin=str(path))
        for match in re.finditer(r"gettext\((u?'(?:\\.|[^'\\])*'|u?\"(?:\\.|[^\"\\])*\")\)", extracted):
            messages["django"].add(ast.literal_eval(match[1]))
    for path in root.rglob("*.js"):
        for match in re.finditer(r'\bgettext\(\s*([\'"])(.*?)\1\s*\)', path.read_text()):
            messages["djangojs"].add(match[2])
    return messages


class Command(BaseCommand):
    help = "Check English source messages have Simplified Chinese translations"

    def add_arguments(self, parser):
        parser.add_argument("--compile", action="store_true")

    def handle(self, *args, **options):
        roots = [Path(app.path) for app in apps.get_app_configs() if app.name.startswith("gpu_platform_")]
        # Control's app contains the models; its catalogs live one level above.
        roots = [root.parent if root.name == "core" else root for root in roots]
        catalogs = {}
        all_messages: dict[str, set[str]] = {"django": set(), "djangojs": set()}
        for root in roots:
            for domain, messages in source_messages(root).items():
                all_messages[domain].update(messages)
            for path in (root / "locale" / "zh_Hans" / "LC_MESSAGES").glob("*.po"):
                catalogs[path] = read_catalog(path)
        failures: list[str] = []
        for domain, messages in all_messages.items():
            translated = {
                key
                for path, entries in catalogs.items()
                if path.stem == domain
                for key, value in entries.items()
                if value
            }
            failures.extend(f"{domain}: {message}" for message in sorted(messages - translated))
        if failures:
            raise CommandError("Missing translations:\n" + "\n".join(failures))
        if options["compile"]:
            for path, entries in catalogs.items():
                compile_catalog(entries, path.with_suffix(".mo"))
        self.stdout.write(f"Translation coverage complete: {sum(map(len, all_messages.values()))} messages.")
