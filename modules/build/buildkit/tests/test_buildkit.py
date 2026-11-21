import io
import json
import subprocess
import tarfile
from decimal import Decimal

import pytest

from gpu_platform_build_buildkit.module import Module
from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.slots.build import BuildSpec
from gpu_platform_contracts.testing import BuildContract


class Docker:
    def __init__(self):
        self.commands = []
        self.generation = 1

    def __call__(self, args, **kwargs):
        self.commands.append(args)
        if args[1] == "restart":
            self.generation += 1
        return subprocess.CompletedProcess(args, 0, str(self.generation), "")


@pytest.fixture
def builder(tmp_path):
    docker = Docker()
    return Module(
        ModuleContext(
            node_id="test-node", state_directory=tmp_path, options={"max_cpu": 8, "max_memory_gib": 16}
        ),
        run=docker,
        background=False,
    )


def spec(identifier="one", cpu=2, memory=4):
    return BuildSpec(
        id=identifier,
        owner="member",
        description="FROM scratch",
        architecture="amd64",
        resources={"cpu": cpu, "memory": memory},
    )


def finish(builder, identifier):
    directory = builder.directory(identifier)
    with tarfile.open(directory / "image.tar", "w") as archive:
        content = json.dumps({"manifests": [{"digest": "sha256:" + "a" * 64}]}).encode()
        member = tarfile.TarInfo("index.json")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    (directory / "exit").write_bytes(b"0")


def test_build_queue_enforces_per_job_resource_limits(builder):
    assert builder.build(spec()).state == "running"
    assert builder.build(spec("two", 3, 5)).state == "queued"
    updates = [command for command in builder.run.commands if command[1] == "update"]
    assert len(updates) == 1
    assert updates[0][updates[0].index("--cpus") + 1] == "2"
    assert updates[0][updates[0].index("--memory") + 1] == str(4 * 2**30)
    finish(builder, "one")
    result = builder.status("one")
    assert result.state == "completed"
    assert result.image.owner == "member" and result.image.node_id == "test-node"
    assert builder.status("two").state == "running"
    updates = [command for command in builder.run.commands if command[1] == "update"]
    assert len(updates) == 2
    assert updates[1][updates[1].index("--cpus") + 1] == "3"


def test_queued_build_survives_restart_and_cancel(builder):
    builder.build(spec())
    builder.build(spec("two"))
    restarted = Module(builder.context, run=builder.run, background=False)
    assert restarted.status("two").state == "queued"
    restarted.cancel("one")
    assert restarted.status("one").state == "failed"
    assert restarted.status("two").state == "running"


def test_conflict_capacity_and_context_confinement(builder):
    builder.build(spec())
    changed = spec()
    changed.resources["cpu"] = Decimal(3)
    with pytest.raises(ValueError):
        builder.build(changed)
    with pytest.raises(ValueError):
        builder.build(spec("huge", 9))
    escaped = spec("escaped")
    escaped.context = "../../outside"
    with pytest.raises(ValueError):
        builder.build(escaped)


def test_builder_restart_marks_inflight_build_failed(builder):
    builder.build(spec())
    builder.run.generation += 1
    assert builder.status("one").state == "failed"


def test_trusted_build_context_is_explicit_and_confined(builder):
    import hashlib

    assets = builder.context.state_directory / "bootstrap" / "custom"
    assets.mkdir(parents=True)
    (assets / "asset").write_bytes(bytes(range(32)))
    builder.context.options["trusted_contexts"] = {"custom": "bootstrap/custom"}
    request = spec("with-assets")
    request.context = "trusted:custom"
    assert builder.build(request).state == "running"
    copied = builder.directory(request.id) / "context" / "asset"
    assert (
        hashlib.sha256(copied.read_bytes()).digest()
        == hashlib.sha256((assets / "asset").read_bytes()).digest()
    )
    unknown = spec("unknown-assets")
    unknown.context = "trusted:unknown"
    with pytest.raises(ValueError):
        builder.build(unknown)


@pytest.mark.parametrize("context", [
    "https://github.com/example/project.git", "https://gitee.com/example/project.git",
    "https://git.campus.example:8443/team/project.git", "https://127.0.0.1/project.git",
])
def test_git_context_accepts_https_repositories_on_any_host(builder, context):
    request = spec("git-context")
    request.context = context
    assert builder.build(request).state == "running"
    assert builder.read(request.id)["git_context"] == request.context


@pytest.mark.parametrize("context", [
    "http://git.example/project.git", "https://user:secret@git.example/project.git",
    "https:///project.git", "https://git.example:99999/project.git",
])
def test_git_context_rejects_invalid_urls_and_embedded_credentials(builder, context):
    request = spec("invalid-origin")
    request.context = context
    with pytest.raises(ValueError):
        builder.build(request)


def test_build_deadline_reclaims_serial_executor(builder):
    builder.context.options["max_build_seconds"] = 0
    builder.build(spec())
    assert builder.status("one").state == "failed"
    assert any(command[1] == "restart" for command in builder.run.commands)


def test_private_image_credentials_are_scoped_to_build_lifetime(builder):
    import base64
    from gpu_platform_contracts.handoffs import Image

    request = spec("private-source")
    request.images = [
        Image(
            id="source",
            owner="publisher",
            node_id="test-node",
            form="oci-registry",
            reference="registry.example:5000/source:tag",
            architecture="amd64",
            credentials={
                "server": "registry.example:5000",
                "username": "temporary",
                "password": "read-secret",
            },
            metadata={"ca_certificate": "fixture-ca"},
        )
    ]
    assert builder.build(request).state == "running"
    directory = builder.directory(request.id)
    auth = directory / "auth" / "config.json"
    config = json.loads(auth.read_text())
    encoded = config["auths"]["registry.example:5000"]["auth"]
    assert base64.b64decode(encoded) == b"temporary:read-secret"
    assert auth.stat().st_mode & 0o777 == 0o600
    assert "credentials" not in builder.read(request.id)["spec"]["images"][0]
    assert len(list((directory / "auth").glob("*.crt"))) == 1
    finish(builder, request.id)
    assert builder.status(request.id).state == "completed"
    assert not (directory / "auth").exists()


def test_conflicting_host_credentials_are_rejected(builder):
    from gpu_platform_contracts.handoffs import Image

    request = spec("conflicting")
    request.images = [
        Image(
            id=str(index),
            owner=str(index),
            node_id="test-node",
            form="oci-registry",
            reference=f"registry.example/{index}:tag",
            architecture="amd64",
            credentials={"server": "registry.example", "username": str(index), "password": "secret"},
        )
        for index in range(2)
    ]
    with pytest.raises(ValueError):
        builder.build(request)


def test_ambiguous_launch_retains_reservation_until_executor_is_observed(tmp_path):
    class UncertainDocker(Docker):
        uncertain = False

        def __call__(self, args, **kwargs):
            if args[1] == "exec" and "--detach" in args:
                self.commands.append(args)
                self.uncertain = True
                raise subprocess.CalledProcessError(1, args)
            if self.uncertain and args[1] == "restart":
                raise subprocess.CalledProcessError(1, args)
            return super().__call__(args, **kwargs)

    docker = UncertainDocker()
    builder = Module(
        ModuleContext(node_id="test-node", state_directory=tmp_path, options={"max_cpu": 8}),
        run=docker,
        background=False,
    )
    assert builder.build(spec()).state == "running"
    assert (builder.directory("one") / "auth").exists()
    finish(builder, "one")
    docker.uncertain = False
    assert builder.status("one").state == "completed"
    assert sum("--detach" in command for command in docker.commands) == 1


def test_private_registry_mirror_preserves_from_and_scopes_tls_credentials(builder):
    import base64
    import tomllib
    from gpu_platform_contracts.handoffs import Image

    request = spec("mirrored-source")
    request.description = "FROM 127.0.0.1:5000/source:tag"
    request.images = [
        Image(
            id="source",
            owner="publisher",
            node_id="test-node",
            form="oci-registry",
            reference="127.0.0.1:5000/source:tag",
            architecture="amd64",
            credentials={"server": "127.0.0.1:5000", "username": "scoped", "password": "read-secret"},
            metadata={"ca_certificate": "fixture-ca", "registry_mirrors": ["registry:5000"]},
        )
    ]
    assert builder.build(request).state == "running"
    directory = builder.directory(request.id)
    config = tomllib.loads((directory / "registry.toml").read_text())["registry"]
    assert config["127.0.0.1:5000"]["mirrors"] == ["registry:5000"]
    for endpoint in ("127.0.0.1:5000", "registry:5000"):
        assert config[endpoint]["http"] is False and config[endpoint]["insecure"] is False
        certificate = config[endpoint]["ca"][0].removeprefix("/buildkit-config/")
        assert (builder.root / "config" / certificate).read_text() == "fixture-ca"
    auths = json.loads((directory / "auth/config.json").read_text())["auths"]
    assert set(auths) == {"127.0.0.1:5000", "registry:5000"}
    assert {base64.b64decode(value["auth"]) for value in auths.values()} == {b"scoped:read-secret"}
    assert (directory / "context/Dockerfile").read_text() == request.description
    finish(builder, request.id)
    assert builder.status(request.id).state == "completed"
    assert not (directory / "auth").exists()


@pytest.mark.parametrize(
    "mirrors", ["registry:5000", ["http://registry:5000"], ["registry/path"], ["registry:0"]]
)
def test_registry_mirror_authorities_are_validated_before_secret_files(builder, mirrors):
    from gpu_platform_contracts.handoffs import Image

    request = spec("invalid-mirror")
    request.images = [
        Image(
            id="source",
            owner="publisher",
            node_id="test-node",
            form="oci-registry",
            reference="registry/source:tag",
            architecture="amd64",
            credentials={"server": "registry", "username": "scoped", "password": "secret"},
            metadata={"registry_mirrors": mirrors},
        )
    ]
    with pytest.raises(ValueError):
        builder.build(request)
    assert not (builder.directory(request.id) / "auth").exists()


def test_preflight_allows_docker_to_be_provisioned_by_installer(tmp_path):
    context = ModuleContext(node_id="new-host", state_directory=tmp_path)
    assert not any(not result.ok for result in Module.check_host(context))


def test_published_build_releases_artifacts_without_reexecuting(builder):
    request = spec("released")
    builder.build(request)
    finish(builder, request.id)
    assert builder.status(request.id).image is not None
    directory = builder.directory(request.id)
    (directory / "build.log").write_bytes(b"x" * (2 * 1024 * 1024))
    builder.release_result(request.id)
    builder.release_result(request.id)
    assert not (directory / "image.tar").exists() and not (directory / "context").exists()
    assert (directory / "build.log").stat().st_size == 1024 * 1024
    calls = len(builder.run.commands)
    assert builder.build(request).state == "completed"
    assert len(builder.run.commands) == calls


@pytest.fixture
def build_spec():
    return spec("contract")


@pytest.fixture
def await_build():
    def wait(builder, identifier):
        finish(builder, identifier)
        return builder.status(identifier)

    return wait


class TestBuildKitContract(BuildContract):
    pass
