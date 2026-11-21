import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import gpu_platform_storage_rawfile

spec = importlib.util.spec_from_file_location(
    "rawfile_permissions", Path(gpu_platform_storage_rawfile.__file__).parent / "host/rawfile_permissions.py"
)
ownership = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ownership)


class StorageOwnershipTests(TestCase):
    def test_volume_images_and_snapshots_inherit_pool_owner_without_following_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            pool = Path(directory)
            volume = pool / "pvc-example"
            (volume / "snapshots/temp").mkdir(parents=True)
            image = volume / "disk.img"
            image.write_bytes(b"active volume content")
            (volume / "snapshots/temp/snapshot.img").write_bytes(b"snapshot content")
            outside = pool / "outside"
            outside.mkdir(mode=0o755)
            (outside / "file").write_bytes(b"external content")
            (volume / "linked-dir").symlink_to(outside, target_is_directory=True)
            (volume / "linked-file").symlink_to(outside / "file")
            manager = SimpleNamespace(get_volume_path=lambda pool_name, volume_id: volume)
            observed = []

            def chown(descriptor, uid, gid):
                observed.append((os.fstat(descriptor).st_ino, uid, gid))

            with (
                image.open("rb") as active,
                patch.dict(sys.modules, {"utils.volume_manager": SimpleNamespace(manager=manager)}),
                patch.object(os, "fchown", side_effect=chown),
            ):
                ownership.update_permissions("pvc-example", "pool")
                ownership.update_permissions("pvc-example", "pool")
                self.assertEqual(active.read(), b"active volume content")
            expected = {
                path.stat().st_ino
                for path in [
                    volume,
                    volume / "snapshots",
                    volume / "snapshots/temp",
                    image,
                    volume / "snapshots/temp/snapshot.img",
                ]
            }
            self.assertEqual({inode for inode, uid, gid in observed}, expected)
            self.assertTrue(
                all((uid, gid) == (pool.stat().st_uid, pool.stat().st_gid) for inode, uid, gid in observed)
            )
            self.assertEqual(image.stat().st_mode & 0o777, 0o600)
            self.assertEqual((volume / "snapshots/temp").stat().st_mode & 0o777, 0o700)
            self.assertEqual(outside.stat().st_mode & 0o777, 0o755)
            self.assertEqual((outside / "file").read_bytes(), b"external content")

    def test_missing_volume_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = SimpleNamespace(
                get_volume_path=lambda pool_name, volume_id: Path(directory) / volume_id
            )
            with patch.dict(sys.modules, {"utils.volume_manager": SimpleNamespace(manager=manager)}):
                ownership.update_permissions("pvc-absent", "pool")
