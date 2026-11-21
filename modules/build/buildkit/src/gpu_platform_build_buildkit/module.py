"""Durable, resource-bounded build queue for a rootless BuildKit service."""

from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import shutil
import shlex
import subprocess
import tarfile
import threading
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from gpu_platform_contracts.handoffs import Image
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.slots.build import Build, BuildResult, BuildSpec
from gpu_platform_contracts.texts import Message

from .options import Options
from .host import PROFILE_NAME

def container_name(context):
    return context.options.get(
        "container_name", "gpu-platform-builder-" + hashlib.sha256(context.node_id.encode()).hexdigest()[:12]
    )


def writable(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o755)
    if os.geteuid() == 0:
        os.chown(path, 1000, 1000)



class Module(Build):
    options_model = Options
    descriptor = ModuleDescriptor(
        name="buildkit",
        slot="build",
        version="0.4.0",
        side="node",
        provides={"build_result": {"oci-archive"}},
        parameters=Options.model_json_schema(),
    )

    def __init__(self, context, run=subprocess.run, background=True):
        super().__init__(context)
        self.run = run
        self.root = context.state_directory / "buildkit"
        self.jobs = self.root / "jobs"
        writable(self.root)
        writable(self.jobs)
        writable(self.root / "config")
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.container = container_name(context)
        self.thread = None
        if background:
            self.thread = threading.Thread(target=self.worker, daemon=True)
            self.thread.start()

    def key(self, build_id):
        return hashlib.sha256(build_id.encode()).hexdigest()

    def directory(self, build_id):
        return self.jobs / self.key(build_id)

    def save(self, directory, record):
        target = directory / "record.json"
        temporary = directory / ".record.tmp"
        temporary.write_text(json.dumps(record))
        temporary.chmod(0o600)
        temporary.replace(target)

    def read(self, build_id):
        return json.loads((self.directory(build_id) / "record.json").read_text())

    def execute(self, args):
        return self.run(["docker", *args], check=True, capture_output=True, text=True, timeout=30)

    def boot(self):
        return self.execute(["inspect", "--format", "{{.State.StartedAt}}", self.container]).stdout.strip()

    def spec_record(self, spec):
        return spec.model_dump(mode="json", exclude={"images": {"__all__": {"credentials"}}})

    def prepare_access(self, spec, directory):
        auths: dict[str, dict[str, str]] = {}
        certificates: dict[str, str] = {}
        mirrors: dict[str, list[str]] = {}
        for image in spec.images:
            if image.node_id != self.context.node_id:
                raise ValueError("build.image_node_mismatch")
            credentials = image.credentials
            if not credentials:
                continue
            server = credentials["server"]
            image_mirrors = image.metadata.get("registry_mirrors", [])
            if not isinstance(image_mirrors, list) or not all(
                isinstance(value, str) for value in image_mirrors
            ):
                raise ValueError("build.invalid_registry_mirrors")
            for endpoint in [server, *image_mirrors]:
                if not re.fullmatch(r"[A-Za-z0-9.:[\]-]+", endpoint):
                    raise ValueError("build.invalid_registry")
                parsed = urlsplit("https://" + endpoint)
                if not parsed.hostname or parsed.path or parsed.port == 0:
                    raise ValueError("build.invalid_registry")
            if server in mirrors and mirrors[server] != image_mirrors:
                raise ValueError("build.conflicting_registry_mirrors")
            mirrors[server] = image_mirrors
            identity = {
                "auth": base64.b64encode(
                    f"{credentials['username']}:{credentials['password']}".encode()
                ).decode()
            }
            for endpoint in [server, *image_mirrors]:
                if endpoint in auths and auths[endpoint] != identity:
                    raise ValueError("build.conflicting_registry_credentials")
                auths[endpoint] = identity
                if image.metadata.get("ca_certificate"):
                    certificate = image.metadata["ca_certificate"]
                    if endpoint in certificates and certificates[endpoint] != certificate:
                        raise ValueError("build.conflicting_registry_trust")
                    certificates[endpoint] = certificate
        auth = directory / "auth"
        writable(auth)
        auth.chmod(0o700)
        config = auth / "config.json"
        config.write_text(json.dumps({"auths": auths}))
        config.chmod(0o600)
        if os.geteuid() == 0:
            os.chown(config, 1000, 1000)
        trust_paths = {}
        for server, certificate in certificates.items():
            name = hashlib.sha256((server + certificate).encode()).hexdigest() + ".crt"
            target = auth / name
            target.write_text(certificate)
            target.chmod(0o644)
            public = self.root / "config" / "certificates"
            public.mkdir(exist_ok=True)
            certificate_path = public / name
            certificate_path.write_text(certificate)
            certificate_path.chmod(0o644)
            trust_paths[server] = "/buildkit-config/certificates/" + name
        trust = []
        for server in sorted(auths):
            trust.extend([f"[registry.{json.dumps(server)}]", "http = false", "insecure = false"])
            if mirrors.get(server):
                trust.append("mirrors = " + json.dumps(mirrors[server]))
            if server in trust_paths:
                trust.append("ca = " + json.dumps([trust_paths[server]]))
        (directory / "registry.toml").write_text("\n".join(trust))

    def clean_access(self, directory):
        shutil.rmtree(directory / "auth", ignore_errors=True)

    def build(self, spec):
        with self.lock:
            directory = self.directory(spec.id)
            if (directory / "record.json").exists():
                if self.read(spec.id)["spec"] != self.spec_record(spec):
                    raise ValueError("build.conflict")
                return self.status(spec.id)
            cpu = Decimal(spec.resources.get("cpu", 1))
            memory = Decimal(spec.resources.get("memory", 2))
            if cpu <= 0 or memory <= 0:
                raise ValueError("build.invalid_resources")
            if cpu > Decimal(
                str(self.context.options.get("max_cpu", os.cpu_count() or 1))
            ) or memory > Decimal(str(self.context.options.get("max_memory_gib", 8))):
                raise ValueError("build.capacity_exceeded")
            writable(directory)
            context_directory = directory / "context"
            writable(context_directory)
            git_context = None
            if spec.context:
                if "://" in spec.context:
                    url = urlsplit(spec.context)
                    if (
                        url.scheme != "https"
                        or not url.hostname
                        or url.username
                        or url.password
                        or (url.port is not None and not 1 <= url.port <= 65535)
                    ):
                        raise ValueError("build.invalid_git_context")
                    git_context = spec.context
                else:
                    if spec.context.startswith("trusted:"):
                        key = spec.context.removeprefix("trusted:")
                        configured = self.context.options.get("trusted_contexts", {}).get(key)
                        if not configured:
                            raise ValueError("build.unknown_trusted_context")
                        allowed = (self.context.state_directory / configured).resolve()
                        source = allowed
                    else:
                        allowed = (self.root / "contexts").resolve()
                        source = (allowed / spec.context).resolve()
                    if not source.is_relative_to(allowed) or not source.is_dir():
                        raise ValueError("build.invalid_context")
                    if any(item.is_symlink() for item in source.rglob("*")):
                        raise ValueError("build.context_symlink")
                    shutil.copytree(source, context_directory, dirs_exist_ok=True)
            (context_directory / "Dockerfile").write_text(spec.description)
            for item in context_directory.rglob("*"):
                item.chmod(0o755 if item.is_dir() else 0o644)
            self.prepare_access(spec, directory)
            record = {
                "spec": self.spec_record(spec),
                "state": "queued",
                "created_at": time.time(),
                "cpu": str(cpu),
                "memory_bytes": int(memory * 2**30),
                "git_context": git_context,
            }
            self.save(directory, record)
            self.advance()
            return self.result(self.read(spec.id))

    def start(self, directory, record):
        config = self.root / "config" / "buildkitd.toml"
        config.write_text((directory / "registry.toml").read_text())
        config.chmod(0o644)
        self.execute(["restart", "--time", "5", self.container])
        self.execute(
            [
                "exec",
                self.container,
                "sh",
                "-c",
                "for attempt in $(seq 1 30); do buildctl --addr unix:///run/user/1000/buildkit/buildkitd.sock debug workers >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1",
            ]
        )
        self.execute(
            [
                "update",
                "--cpus",
                record["cpu"],
                "--memory",
                str(record["memory_bytes"]),
                "--memory-swap",
                str(record["memory_bytes"]),
                self.container,
            ]
        )
        root = "/builds/" + directory.name
        architecture = record["spec"]["architecture"]
        clone = ""
        context_path = root + "/context"
        if record.get("git_context"):
            context_path = root + "/source"
            clone = f"git -c http.followRedirects=false clone --depth 1 -- {shlex.quote(record['git_context'])} {context_path} && "
        # All shell substitutions below are generated identifiers and fixed arguments.
        command = (
            f"( {clone}DOCKER_CONFIG={root}/auth buildctl --addr unix:///run/user/1000/buildkit/buildkitd.sock build "
            f"--frontend dockerfile.v0 --local context={context_path} --local dockerfile={root}/context "
            f"--opt platform=linux/{architecture} --output type=oci,dest={root}/image.tar "
            f") >{root}/build.log 2>&1; result=$?; printf '%s' \"$result\" >{root}/exit.tmp; "
            f"mv {root}/exit.tmp {root}/exit"
        )
        record["boot"] = self.boot()
        # Persist before exec so a node restart can identify an interrupted launch.
        record["state"] = "running"
        record["launched_at"] = time.time()
        self.save(directory, record)
        self.execute(["exec", "--detach", self.container, "sh", "-c", command])

    def complete(self, directory, record):
        exit_path = directory / "exit"
        if exit_path.exists():
            exit_code = int(exit_path.read_text())
            if exit_code:
                record.update(state="failed", error="build.command_failed", exit_code=exit_code)
            else:
                try:
                    with tarfile.open(directory / "image.tar", "r:*") as archive:
                        member = archive.getmember("index.json")
                        if member.size > 4 * 1024 * 1024:
                            raise ValueError("build.invalid_index")
                        source = archive.extractfile(member)
                        if source is None:
                            raise ValueError("build.invalid_index")
                        index = json.load(source)
                        digest = index["manifests"][0]["digest"]
                    record.update(state="completed", digest=digest)
                except (OSError, ValueError, KeyError, IndexError, tarfile.TarError):
                    record.update(state="failed", error="build.invalid_output")
            self.save(directory, record)
            self.clean_access(directory)
        elif self.boot() != record["boot"]:
            record.update(state="failed", error="build.service_restarted")
            self.save(directory, record)
            self.clean_access(directory)
        elif time.time() - record["launched_at"] > self.context.options.get("max_build_seconds", 3600):
            self.execute(["restart", "--time", "5", self.container])
            record.update(state="failed", error="build.expired")
            self.save(directory, record)
            self.clean_access(directory)
        elif not (directory / "build.log").exists() and time.time() - record["launched_at"] > 60:
            # A launch interrupted before docker exec has no worker to complete it.
            record.update(state="failed", error="build.launch_interrupted")
            self.save(directory, record)
            self.clean_access(directory)

    def advance(self):
        with self.lock:
            records = [
                (path.parent, json.loads(path.read_text())) for path in self.jobs.glob("*/record.json")
            ]
            for directory, record in records:
                if record["state"] == "running":
                    self.complete(directory, record)
            if any(record["state"] == "running" for _, record in records):
                return
            queued = sorted(
                ((directory, record) for directory, record in records if record["state"] == "queued"),
                key=lambda item: item[1]["created_at"],
            )
            if queued:
                directory, record = queued[0]
                try:
                    self.start(directory, record)
                except (OSError, subprocess.SubprocessError):
                    if record["state"] == "running":
                        # Preserve an ambiguous launch until the executor is observed or stopped.
                        try:
                            self.execute(["restart", "--time", "5", self.container])
                        except (OSError, subprocess.SubprocessError):
                            return
                    record.update(state="failed", error="build.service_unavailable")
                    self.save(directory, record)
                    self.clean_access(directory)

    def result(self, record):
        spec = BuildSpec.model_validate(record["spec"])
        directory = self.directory(spec.id)
        result = BuildResult(id=spec.id, state=record["state"])
        log = directory / "build.log"
        if log.exists():
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 1024 * 1024))
                result.logs = stream.read().decode("utf-8", errors="replace")
        if result.state == "completed" and not record.get("released"):
            archive = directory / "image.tar"
            result.image = Image(
                id=spec.id,
                owner=spec.owner,
                node_id=self.context.node_id,
                form="oci-archive",
                reference=str(archive),
                architecture=spec.architecture,
                size_bytes=archive.stat().st_size,
                digest=record["digest"],
                metadata={"architecture": spec.architecture},
            )
        elif result.state == "failed":
            result.error = Message(code=record["error"], parameters={"exit_code": record.get("exit_code")})
        return result

    def status(self, build_id):
        with self.lock:
            self.advance()
            return self.result(self.read(build_id))

    def cancel(self, build_id):
        with self.lock:
            record = self.read(build_id)
            if record["state"] in {"completed", "failed"}:
                return
            if record["state"] == "running":
                self.execute(["restart", "--time", "5", self.container])
            record.update(state="failed", error="build.cancelled")
            self.save(self.directory(build_id), record)
            self.release_result(build_id)
            self.advance()

    def release_result(self, build_id):
        with self.lock:
            record = self.read(build_id)
            if record["state"] not in {"completed", "failed"}:
                raise ValueError("build.result_still_active")
            directory = self.directory(build_id)
            record["released"] = True
            self.save(directory, record)
            for path in directory.iterdir():
                if path.name in {"record.json", "build.log"}:
                    continue
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            log = directory / "build.log"
            if log.exists() and log.stat().st_size > 1024 * 1024:
                with log.open("rb") as source:
                    source.seek(-1024 * 1024, os.SEEK_END)
                    retained = source.read()
                log.write_bytes(retained)

    def worker(self):
        while not self.closed.wait(2):
            try:
                self.advance()
            except (OSError, ValueError, subprocess.SubprocessError):
                # Status stays durable while the container service reconnects.
                continue

    def close(self):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout=5)

    @classmethod
    def check_host(cls, context):
        # Docker is an installer prerequisite and may not exist during preflight.
        # BuildKit runs in its image; host-specific prerequisites belong to Docker.
        return []

    @classmethod
    def install_host(cls, context):
        from .host import install

        install(context)

    @classmethod
    def uninstall_host(cls, context):
        from .host import uninstall

        uninstall(context)

    @classmethod
    def install(cls, context):
        writable(context.state_directory / "buildkit")
        writable(context.state_directory / "buildkit" / "jobs")
        configuration = context.state_directory / "buildkit" / "config"
        writable(configuration)
        settings = configuration / "buildkitd.toml"
        settings.touch()
        settings.chmod(0o644)

    @classmethod
    def compose(cls, context):
        image = context.options.get("image", "moby/buildkit:v0.26.1-rootless")
        return {
            "services": {
                "builder": {
                    "image": image,
                    "container_name": container_name(context),
                    "restart": "unless-stopped",
                    "user": "1000:1000",
                    "command": ["--config", "/buildkit-config/buildkitd.toml"],
                    "security_opt": ["seccomp=unconfined", f"apparmor={PROFILE_NAME}", "systempaths=unconfined"],
                    "cpus": context.options.get("max_cpu", 1),
                    "mem_limit": f"{context.options.get('max_memory_gib', 8)}g",
                    "volumes": [
                        f"{context.state_directory}/buildkit/jobs:/builds",
                        f"{context.state_directory}/buildkit/config:/buildkit-config:ro",
                        "buildkit-cache:/home/user/.local/share/buildkit",
                    ],
                    "healthcheck": {
                        "test": [
                            "CMD",
                            "buildctl",
                            "--addr",
                            "unix:///run/user/1000/buildkit/buildkitd.sock",
                            "debug",
                            "workers",
                        ],
                        "interval": "30s",
                        "timeout": "5s",
                        "retries": 3,
                    },
                },
                "node": {
                    "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
                    "depends_on": {"builder": {"condition": "service_healthy"}},
                },
            },
            "volumes": {"buildkit-cache": {}},
        }
