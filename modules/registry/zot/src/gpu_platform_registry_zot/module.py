"""Owner-scoped OCI registry access and streamed OCI archive transfers."""

import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin, urlsplit

import requests

from gpu_platform_contracts.handoffs import Image, Snapshot
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.resources import ResourceDefinition, ResourceUsage
from gpu_platform_contracts.slots.registry import Registry
from gpu_platform_contracts.texts import LocalizedText
from .options import Options

from .accounts import Accounts

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"


def image_blobs(manifest):
    # A scratch build can encode its empty layer collection as JSON null.
    layers = manifest["layers"]
    if layers is None:
        layers = []
    if not isinstance(layers, list):
        raise ValueError("registry.invalid_manifest")
    return [manifest["config"], *layers]


class Module(Registry):
    options_model = Options
    descriptor = ModuleDescriptor(
        parameters=Options.model_json_schema(),
        name="zot",
        slot="registry",
        version="0.4.0",
        side="node",
        provides={"image": {"oci-registry"}},
        accepts={"snapshot": {"oci-archive"}, "build_result": {"oci-archive"}},
        resources=[
            ResourceDefinition(
                key="image_storage", unit="GiB", requestable=False, accounting="image",
                label=LocalizedText(en="Image storage", zh_hans="镜像空间")
            )
        ],
    )

    def __init__(self, context, session=None, reload=None):
        super().__init__(context)
        self.root = context.state_directory / "registry"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.records = self.root / "images"
        self.records.mkdir(mode=0o700, exist_ok=True)
        self.session = session or requests.Session()
        self.session.trust_env = False
        from .install import registry_url

        self.base = registry_url(context.options).rstrip("/")
        self.verify = context.options.get("ca", str(self.root / "tls.crt"))
        self.accounts = Accounts(self.root, context.options, reload or self.reload)

    def reload(self):
        subprocess.run(
            ["docker", "restart", self.context.options.get("container_name", "gpu-platform-registry")],
            check=True,
            timeout=60,
            capture_output=True,
        )

    @classmethod
    def install(cls, context):
        from .install import install

        install(context)

    @classmethod
    def compose(cls, context):
        from .install import compose

        return compose(context)

    def path(self, image_id):
        return self.records / (hashlib.sha256(image_id.encode()).hexdigest() + ".json")

    def credentials(self, owner):
        return self.accounts.identity(owner)["writer"]

    def request(self, method, path, owner, **kwargs):
        identity = self.credentials(owner)
        url = urljoin(self.base + "/", path)
        if urlsplit(url)[:2] != urlsplit(self.base)[:2]:
            raise ValueError("registry.invalid_upload_location")
        response = self.session.request(
            method,
            url,
            auth=(identity["username"], identity["password"]),
            verify=self.verify,
            timeout=(5, 120),
            allow_redirects=False,
            **kwargs,
        )
        response.raise_for_status()
        if 300 <= response.status_code < 400:
            raise ValueError("registry.redirect_rejected")
        return response

    def repo(self, owner, image_id=None):
        suffix = hashlib.sha256(image_id.encode()).hexdigest() if image_id else "images"
        return "user-" + hashlib.sha256(owner.encode()).hexdigest()[:32] + "/" + suffix

    def image_repo(self, image):
        return image.metadata.get("repository", self.repo(image.owner))

    def get(self, image_id, owner):
        image = Image.model_validate_json(self.path(image_id).read_text())
        if image.owner != owner:
            raise PermissionError("registry.owner_mismatch")
        identity = self.accounts.reader("image:" + image_id, [self.image_repo(image)])
        ca = Path(self.verify) if isinstance(self.verify, str) else None
        return image.model_copy(
            update={
                "metadata": {**image.metadata, **({"ca_certificate": ca.read_text()} if ca and ca.is_file() else {})},
                "credentials": {
                    "server": urlsplit(self.base).netloc,
                    "username": identity["username"],
                    "password": identity["password"],
                }
            }
        )

    def build_images(self, owner, references, build_id):
        from .install import build_mirrors

        images = []
        for reference in references:
            image = self.get(reference.id, reference.owner)
            if image.node_id != self.context.node_id or reference.node_id != self.context.node_id:
                raise PermissionError("registry.node_mismatch")
            if reference.digest and image.digest != reference.digest:
                raise ValueError("registry.image_changed")
            images.append(image)
        if not images:
            return []
        # Store the recipient only for revocation; credentials stay in the registry's private directory.
        grants = self.root / "build-access"
        grants.mkdir(mode=0o700, exist_ok=True)
        key = hashlib.sha256(build_id.encode()).hexdigest()
        path = grants / key
        if path.exists() and path.read_text() != owner:
            raise PermissionError("registry.build_owner_mismatch")
        path.write_text(owner)
        path.chmod(0o600)
        identity = self.accounts.reader("build:" + owner + ":" + build_id,
                                        [self.image_repo(image) for image in images])
        credentials = {"server": urlsplit(self.base).netloc,
                       "username": identity["username"], "password": identity["password"]}
        return [image.model_copy(update={
            "credentials": credentials,
            "metadata": {**image.metadata, "registry_mirrors": build_mirrors(self.context.options)},
        }) for image in images]

    def release_build_access(self, build_id):
        path = self.root / "build-access" / hashlib.sha256(build_id.encode()).hexdigest()
        if path.exists():
            self.accounts.reader("build:" + path.read_text() + ":" + build_id, [], remove=True)
            path.unlink()

    def save(self, image):
        path = self.path(image.id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(image.model_copy(update={"credentials": {}}).model_dump_json())
        temporary.chmod(0o600)
        temporary.replace(path)

    def store(self, source, owner, image_id):
        if source.owner != owner or source.node_id != self.context.node_id:
            raise PermissionError("registry.source_owner_mismatch")
        if source.form != "oci-archive":
            raise ValueError("registry.unsupported_source_form")
        location = source.location if isinstance(source, Snapshot) else source.reference
        architecture = source.metadata.get("architecture", self.context.options.get("architecture", "amd64"))

        def chunks():
            with open(location, "rb") as stream:
                while data := stream.read(1024 * 1024):
                    yield data

        return self.import_image(image_id, owner, architecture, chunks())

    def delete(self, image_id, owner):
        if not self.path(image_id).exists():
            return
        image = self.get(image_id, owner)
        tag = image.metadata["tag"]
        try:
            self.request("DELETE", f"/v2/{self.image_repo(image)}/manifests/{quote(tag, safe='')}", owner)
        except requests.HTTPError as error:
            if error.response.status_code != 404:
                raise
        self.accounts.reader("image:" + image_id, [], remove=True)
        self.path(image_id).unlink()

    def resources(self):
        images = [Image.model_validate_json(path.read_text()) for path in self.records.glob("*.json")]
        return ResourceUsage(
            total={"image_storage": self.context.options.get("budget_gib", 300)},
            used={"image_storage": Decimal(sum(image.size_bytes for image in images)) / 2**30},
        )

    def export_image(self, image_id, owner):
        image = self.get(image_id, owner)
        repo = self.image_repo(image)
        with tempfile.TemporaryDirectory(dir=self.root, prefix="export-") as directory:
            root = Path(directory)
            blobs = root / "blobs" / "sha256"
            blobs.mkdir(parents=True)
            response = self.request(
                "GET", f"/v2/{repo}/manifests/{image.digest}", owner, headers={"Accept": OCI_MANIFEST}
            )
            manifest = response.content
            if "sha256:" + hashlib.sha256(manifest).hexdigest() != image.digest:
                raise ValueError("registry.digest_mismatch")
            (blobs / image.digest.split(":")[1]).write_bytes(manifest)
            content = json.loads(manifest)
            for descriptor in image_blobs(content):
                digest = self.digest(descriptor["digest"])
                with self.request("GET", f"/v2/{repo}/blobs/sha256:{digest}", owner, stream=True) as response:
                    with (blobs / digest).open("wb") as target:
                        checksum = hashlib.sha256()
                        for chunk in response.iter_content(1024 * 1024):
                            checksum.update(chunk)
                            target.write(chunk)
                if checksum.hexdigest() != digest:
                    raise ValueError("registry.digest_mismatch")
            (root / "oci-layout").write_text(json.dumps({"imageLayoutVersion": "1.0.0"}))
            (root / "index.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "mediaType": OCI_INDEX,
                        "manifests": [
                            {
                                "mediaType": OCI_MANIFEST,
                                "digest": image.digest,
                                "size": len(manifest),
                                "platform": {"architecture": image.architecture, "os": "linux"},
                            }
                        ],
                    }
                )
            )
            archive_path = root / "image.tar"
            with tarfile.open(archive_path, "w") as archive:
                for path in [root / "oci-layout", root / "index.json", root / "blobs"]:
                    archive.add(path, arcname=path.name)
            with archive_path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    yield chunk

    @staticmethod
    def digest(value):
        import re

        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("registry.invalid_digest")
        return value.split(":")[1]

    def import_image(self, image_id, owner, architecture, chunks):
        if architecture not in {"amd64", "arm64"}:
            raise ValueError("registry.invalid_architecture")
        if architecture != self.context.options.get("architecture", architecture):
            raise ValueError("registry.architecture_mismatch")
        if self.path(image_id).exists():
            image = self.get(image_id, owner)
            for _ in chunks:
                pass
            return image
        self.credentials(owner)
        with tempfile.TemporaryDirectory(dir=self.root, prefix="import-") as directory:
            root = Path(directory)
            archive_path = root / "image.tar"
            with archive_path.open("wb") as stream:
                for chunk in chunks:
                    stream.write(chunk)
            with tarfile.open(archive_path) as archive:
                members = {}
                for member in archive:
                    path = Path(member.name)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or member.issym()
                        or member.islnk()
                        or member.isdev()
                    ):
                        raise ValueError("registry.invalid_archive_entry")
                    if member.isdir():
                        continue
                    if not member.isfile() or member.name in members:
                        raise ValueError("registry.invalid_archive_entry")
                    members[member.name] = member

                rewritten_blobs: dict[str, bytes] = {}

                def read_blob(digest):
                    if digest in rewritten_blobs:
                        return io.BytesIO(rewritten_blobs[digest])
                    key = "blobs/sha256/" + self.digest(digest)
                    member = members[key]
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError("registry.invalid_archive_entry")
                    return stream

                index_stream = archive.extractfile(members["index.json"])
                if index_stream is None:
                    raise ValueError("registry.invalid_archive_entry")
                with index_stream:
                    index_data = index_stream.read(16 * 1024 * 1024 + 1)
                    if len(index_data) > 16 * 1024 * 1024:
                        raise ValueError("registry.metadata_too_large")
                    index = json.loads(index_data)

                def select_manifest(index, depth=0):
                    if depth > 8:
                        raise ValueError("registry.index_depth_exceeded")
                    for descriptor in index["manifests"]:
                        platform = descriptor.get("platform", {})
                        if platform.get("architecture", architecture) != architecture or platform.get("os", "linux") != "linux":
                            continue
                        with read_blob(descriptor["digest"]) as stream:
                            manifest = stream.read(16 * 1024 * 1024 + 1)
                        if len(manifest) > 16 * 1024 * 1024:
                            raise ValueError("registry.metadata_too_large")
                        if len(manifest) != descriptor["size"] or hashlib.sha256(manifest).hexdigest() != self.digest(descriptor["digest"]):
                            raise ValueError("registry.digest_mismatch")
                        content = json.loads(manifest)
                        if "manifests" in content:
                            nested = select_manifest(content, depth + 1)
                            if nested:
                                return nested
                        elif "layers" in content:
                            return descriptor, manifest, content
                    return None

                selected = select_manifest(index)
                if selected is None:
                    raise ValueError("registry.architecture_missing")
                descriptor, manifest, content = selected
                normalized = False
                if content["layers"] is None:
                    content["layers"] = []
                    normalized = True
                with read_blob(content["config"]["digest"]) as stream:
                    configuration_data = stream.read(16 * 1024 * 1024 + 1)
                    if len(configuration_data) > 16 * 1024 * 1024:
                        raise ValueError("registry.metadata_too_large")
                    if (len(configuration_data) != content["config"]["size"]
                            or "sha256:" + hashlib.sha256(configuration_data).hexdigest() != content["config"]["digest"]):
                        raise ValueError("registry.digest_mismatch")
                    configuration = json.loads(configuration_data)
                if configuration.get("architecture") != architecture:
                    raise ValueError("registry.architecture_mismatch")
                rootfs = configuration.get("rootfs", {})
                if not content["layers"] and "diff_ids" in rootfs and rootfs["diff_ids"] is None:
                    rootfs["diff_ids"] = []
                    configuration_data = json.dumps(configuration, separators=(",", ":")).encode()
                    configuration_digest = "sha256:" + hashlib.sha256(configuration_data).hexdigest()
                    rewritten_blobs[configuration_digest] = configuration_data
                    content["config"] = {**content["config"], "digest": configuration_digest,
                                         "size": len(configuration_data)}
                    normalized = True
                if normalized:
                    manifest = json.dumps(content, separators=(",", ":")).encode()
                    descriptor = {**descriptor, "digest": "sha256:" + hashlib.sha256(manifest).hexdigest(),
                                  "size": len(manifest)}
                repo = self.repo(owner, image_id)
                size = 0
                for blob in image_blobs(content):
                    digest = self.digest(blob["digest"])
                    with read_blob(blob["digest"]) as stream:
                        checksum, actual_size = hashlib.sha256(), 0
                        while chunk := stream.read(1024 * 1024):
                            checksum.update(chunk)
                            actual_size += len(chunk)
                        if checksum.hexdigest() != digest or actual_size != blob["size"]:
                            raise ValueError("registry.digest_mismatch")
                    size += actual_size
                    response = self.request("POST", f"/v2/{repo}/blobs/uploads/", owner)
                    location = response.headers["Location"]
                    location += ("&" if "?" in location else "?") + urlencode({"digest": blob["digest"]})
                    with read_blob(blob["digest"]) as stream:
                        self.request(
                            "PUT",
                            location,
                            owner,
                            data=stream,
                            headers={
                                "Content-Type": "application/octet-stream",
                                "Content-Length": str(actual_size),
                            },
                        )
                tag = hashlib.sha256(image_id.encode()).hexdigest()
                self.request(
                    "PUT",
                    f"/v2/{repo}/manifests/{tag}",
                    owner,
                    data=manifest,
                    headers={"Content-Type": content.get("mediaType", OCI_MANIFEST)},
                )
                image = Image(
                    id=image_id,
                    owner=owner,
                    node_id=self.context.node_id,
                    form="oci-registry",
                    reference=urlsplit(self.base).netloc + "/" + repo + ":" + tag,
                    architecture=architecture,
                    size_bytes=size,
                    digest=descriptor["digest"],
                    metadata={"tag": tag, "repository": repo},
                )
                self.save(image)
        return self.get(image_id, owner)
