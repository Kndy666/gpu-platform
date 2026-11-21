import hashlib
import io
import json
import tarfile
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.handoffs import Snapshot
from gpu_platform_contracts.testing import RegistryContract
from gpu_platform_registry_zot.accounts import Accounts
from gpu_platform_registry_zot.module import Module, OCI_MANIFEST


@pytest.mark.parametrize("options", [{"port": 0}, {"url": "http://registry.example"}, {"url": "https://registry.example:bad"}, {"ca": False}, {"unknown": True}])
def test_invalid_registry_configuration_is_rejected(options):
    with pytest.raises(ValueError):
        Module.validate_options(options)


def test_dynamic_registry_defaults_follow_configured_port():
    from gpu_platform_registry_zot.install import registry_url

    options = Module.validate_options({"port": 5443})
    assert registry_url(options) == "https://127.0.0.1:5443"


def test_installation_publishes_trust_under_configured_state_directory(tmp_path, monkeypatch):
    from gpu_platform_contracts.handoffs import RegistryTrust
    from gpu_platform_registry_zot.install import install

    root = tmp_path / "custom-state"
    (root / "registry").mkdir(parents=True)
    (root / "registry" / "tls.crt").write_text("fixture-certificate")
    context = SimpleNamespace(node_id="node-a", state_directory=root,
                              options={"url": "https://registry.example:5443"}, installation_handoffs={})
    monkeypatch.setattr(Accounts, "identity", lambda *args: None)
    install(context)
    trust = RegistryTrust.model_validate(context.installation_handoffs["image"][0])
    assert trust.node_id == "node-a"
    assert trust.endpoint == "registry.example:5443"
    assert trust.ca_file.read_text() == "fixture-certificate"


def archive(architecture="amd64", corrupt=False, *, layerless=False, null_layers=False):
    blobs = {}

    def blob(data, media_type):
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        blobs[digest.split(":")[1]] = data
        return {"mediaType": media_type, "digest": digest, "size": len(data)}

    configuration = {"architecture": architecture, "os": "linux"}
    if layerless:
        configuration["rootfs"] = {"type": "layers", "diff_ids": None if null_layers else []}
    config = blob(json.dumps(configuration).encode(), "application/vnd.oci.image.config.v1+json")
    layer = blob(b"layer-payload", "application/vnd.oci.image.layer.v1.tar")
    layers = None if null_layers else [] if layerless else [layer]
    manifest = blob(json.dumps({"schemaVersion": 2, "mediaType": OCI_MANIFEST, "config": config, "layers": layers}).encode(), OCI_MANIFEST)
    if corrupt:
        blobs[layer["digest"].split(":")[1]] = b"changed-layer"
    entries = {"index.json": json.dumps({"schemaVersion": 2, "manifests": [{**manifest, "platform": {"architecture": architecture, "os": "linux"}}]}).encode(),
               "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
               **{"blobs/sha256/" + digest: data for digest, data in blobs.items()}}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        for name, data in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return output.getvalue(), manifest


@pytest.fixture
def registry(tmp_path, monkeypatch):
    module = Module(ModuleContext(node_id="target", state_directory=tmp_path, options={"architecture": "amd64"}), reload=lambda: None)
    monkeypatch.setattr(module, "credentials", lambda owner, **kw: {"username": owner, "password": "temporary"})
    published = []

    def request(method, path, owner, **kwargs):
        if method == "POST":
            return SimpleNamespace(headers={"Location": "/upload/session"})
        if "/manifests/" in path and method == "PUT":
            published.append(kwargs["data"])
        return SimpleNamespace(headers={})

    monkeypatch.setattr(module, "request", request)
    return module, published


def test_archive_import_records_independent_node_copy(registry):
    module, published = registry
    payload, manifest = archive()
    image = module.import_image("image-a", "owner-a", "amd64", [payload[:100], payload[100:]])
    assert image.digest == manifest["digest"]
    assert image.node_id == "target"
    assert image.owner == "owner-a"
    assert image.size_bytes > 0
    assert len(published) == 1
    assert module.import_image("image-a", "owner-a", "amd64", []).id == image.id
    assert len(published) == 1


@pytest.mark.parametrize("architecture,corrupt", [("arm64", False), ("amd64", True)])
def test_invalid_content_never_publishes_tag(registry, architecture, corrupt):
    module, published = registry
    payload, _ = archive(architecture, corrupt)
    with pytest.raises(ValueError):
        module.import_image("image-a", "owner-a", "amd64", [payload])
    assert published == []
    assert not module.path("image-a").exists()


def test_image_record_cannot_be_reused_by_another_owner(registry):
    module, published = registry
    payload, _ = archive()
    module.import_image("image-a", "owner-a", "amd64", [payload])
    with pytest.raises(PermissionError):
        module.get("image-a", "owner-b")
    with pytest.raises(PermissionError):
        module.import_image("image-a", "owner-b", "amd64", [payload])
    assert len(published) == 1


def test_archive_links_are_rejected_before_upload(registry):
    module, published = registry
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        member = tarfile.TarInfo("blobs/unsafe")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../outside"
        tar.addfile(member)
    with pytest.raises(ValueError):
        module.import_image("image-a", "owner-a", "amd64", [output.getvalue()])
    assert published == []


def test_account_reload_failure_is_retried_with_stable_credentials(tmp_path):
    attempts = []

    def reload():
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError()

    accounts = Accounts(tmp_path, {}, reload)
    with pytest.raises(ConnectionError):
        accounts.identity("owner-a")
    saved = json.loads((tmp_path / "accounts.json").read_text())["owner-a"]
    assert accounts.identity("owner-a") == saved
    assert accounts.identity("owner-a") == saved
    assert len(attempts) == 2
    assert saved["writer"]["password"]
    rules = json.loads((tmp_path / "config.json").read_text())["http"]["accessControl"]["repositories"]
    assert rules["**"]["defaultPolicy"] == []
    policies = next(value["policies"] for key, value in rules.items() if key != "**")
    assert policies == [{"users": [saved["writer"]["username"]], "actions": ["read", "create", "update", "delete"]}]


def test_read_grants_cover_only_selected_images_and_revoke_after_build(registry):
    module, _ = registry
    payload, _ = archive()
    first = module.import_image("selected", "owner-a", "amd64", [payload])
    other = module.import_image("private", "owner-a", "amd64", [payload])
    shared = module.import_image("shared", "owner-b", "amd64", [payload])
    sources = module.build_images("builder", [first, shared], "build-a")
    credentials = sources[0].credentials
    assert sources[1].credentials == credentials
    assert sources[0].metadata["registry_mirrors"] == ["registry:5000"]
    assert sources[0].reference == first.reference
    rules = json.loads((module.root / "config.json").read_text())["http"]["accessControl"]["repositories"]
    allowed = {repo for repo, value in rules.items() if any(
        credentials["username"] in policy["users"] and policy["actions"] == ["read"]
        for policy in value.get("policies", [])
    )}
    assert allowed == {module.image_repo(first), module.image_repo(shared)}
    assert module.image_repo(other) not in allowed
    assert first.credentials != other.credentials
    module.release_build_access("build-a")
    module.release_build_access("build-a")
    readers = json.loads((module.root / "readers.json").read_text())
    assert all(reader["username"] != credentials["username"] for reader in readers.values())


def test_reader_revocation_retries_after_reload_failure(tmp_path):
    failures = []
    accounts = Accounts(tmp_path, {}, lambda: None)
    identity = accounts.reader("build-a", ["owner/image"])

    def fail():
        failures.append(True)
        raise ConnectionError()

    accounts.reload = fail
    with pytest.raises(ConnectionError):
        accounts.reader("build-a", [], remove=True)
    accounts.reload = lambda: None
    accounts.reader("build-a", [], remove=True)
    assert identity["username"] not in (tmp_path / "htpasswd").read_text()
    assert not (tmp_path / "reload-pending").exists()


def test_registry_publish_endpoint_and_build_network_are_independent(tmp_path):
    from gpu_platform_registry_zot.install import compose

    context = ModuleContext(node_id="target", state_directory=tmp_path, options={
        "port": 5443, "url": "https://images.example:6500", "publish_address": "192.0.2.10",
    })
    service = compose(context)["services"]["registry"]
    assert "network_mode" not in service
    assert service["ports"] == [{"target": 5443, "published": "6500",
                                 "host_ip": "192.0.2.10", "protocol": "tcp"}]
    module = Module(context, reload=lambda: None)
    assert module.base == "https://images.example:6500"
    default = compose(ModuleContext(node_id="target", state_directory=tmp_path, options={}))
    assert default["services"]["registry"]["ports"][0]["host_ip"] == "127.0.0.1"


class TestZotContract(RegistryContract):
    @pytest.mark.parametrize("null_layers", [False, True])
    def test_scratch_image_roundtrip_preserves_digest(self, registry, null_layers):
        payload, manifest = archive(layerless=True, null_layers=null_layers)
        image = registry.import_image("scratch", "builder", "amd64", [payload])
        copied = registry.import_image("scratch-copy", "builder", "amd64", registry.export_image(image.id, image.owner))
        assert copied.digest == image.digest
        if null_layers:
            assert image.digest != manifest["digest"]
        else:
            assert image.digest == manifest["digest"]
        assert copied.size_bytes == image.size_bytes > 0
        exported = b"".join(registry.export_image(copied.id, copied.owner))
        with tarfile.open(fileobj=io.BytesIO(exported)) as result:
            content = result.extractfile("blobs/sha256/" + copied.digest.split(":")[1]).read()
            configuration = json.loads(content)["config"]
            config_blob = result.extractfile("blobs/sha256/" + configuration["digest"].split(":")[1]).read()
        assert json.loads(content)["layers"] == []
        assert "sha256:" + hashlib.sha256(content).hexdigest() == copied.digest
        assert json.loads(config_blob)["rootfs"]["diff_ids"] == []
        assert "sha256:" + hashlib.sha256(config_blob).hexdigest() == configuration["digest"]

    @pytest.fixture
    def registry(self, tmp_path):
        class Response:
            def __init__(self, data=b"", headers=None):
                self.content, self.headers = data, headers or {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def iter_content(self, size):
                for start in range(0, len(self.content), size):
                    yield self.content[start:start + size]

        blobs, manifests = {}, {}

        def request(method, target, owner, **kwargs):
            url = urlsplit(target)
            path = url.path
            if method == "POST":
                return Response(headers={"Location": path})
            if "/blobs/" in path:
                repository, suffix = path.split("/blobs/", 1)
                if method == "PUT":
                    digest = parse_qs(url.query)["digest"][0]
                    blobs[repository, digest] = kwargs["data"].read()
                    return Response()
                return Response(blobs[repository, suffix])
            repository, identifier = path.split("/manifests/", 1)
            if method == "PUT":
                data = kwargs["data"]
                manifests[repository, identifier] = data
                manifests[repository, "sha256:" + hashlib.sha256(data).hexdigest()] = data
                return Response()
            if method == "DELETE":
                manifests.pop((repository, identifier))
                return Response()
            return Response(manifests[repository, identifier])

        module = Module(ModuleContext(node_id="contract-node", state_directory=tmp_path), reload=lambda: None)
        module.request = request
        return module

    @pytest.fixture
    def owner(self):
        return "contract-owner"

    @pytest.fixture
    def image_id(self):
        return "original-image"

    @pytest.fixture
    def second_image_id(self):
        return "copied-image"

    @pytest.fixture
    def image_source(self, tmp_path, owner):
        data, _ = archive()
        path = tmp_path / "source.tar"
        path.write_bytes(data)
        return Snapshot(id="source", owner=owner, task_id="task", node_id="contract-node",
                        form="oci-archive", location=str(path), metadata={"architecture": "amd64"})
