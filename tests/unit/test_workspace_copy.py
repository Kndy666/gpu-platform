import os
from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase
from portal.workspace_copy import COPY_SCRIPT


class WorkspaceDataCopyTests(TestCase):
    def test_copy_preserves_data_links_permissions_and_sparse_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir();destination=root/'destination';destination.mkdir()
            source.chmod(0o750);destination.chmod(0o777)
            data=source/'program';data.write_bytes(b'program contents');data.chmod(0o640)
            os.link(data,source/'program-hardlink');(source/'program-symlink').symlink_to('program')
            with (source/'sparse').open('wb') as stream:stream.seek(8*1024*1024);stream.write(b'end')
            result=subprocess.run(['/bin/sh','-c',COPY_SCRIPT,'copy',str(source),str(destination)],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(destination.stat().st_mode&0o777,0o750)
            self.assertEqual((destination/'program').read_bytes(),data.read_bytes())
            self.assertEqual((destination/'program').stat().st_mode&0o777,0o640)
            self.assertEqual((destination/'program').stat().st_ino,(destination/'program-hardlink').stat().st_ino)
            self.assertEqual(os.readlink(destination/'program-symlink'),'program')
            self.assertEqual((destination/'sparse').read_bytes(),(source/'sparse').read_bytes())
            self.assertLess((destination/'sparse').stat().st_blocks*512,1024*1024)

    def test_insufficient_destination_space_leaves_source_and_destination_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir();destination=root/'destination';destination.mkdir();binary=root/'bin';binary.mkdir()
            data=source/'data';data.write_bytes(b'x'*8192)
            df=binary/'df';df.write_text('#!/bin/sh\nprintf "Avail\\n0\\n"\n');df.chmod(0o755)
            result=subprocess.run(['/bin/sh','-c',COPY_SCRIPT,'copy',str(source),str(destination)],
                env={**os.environ,'PATH':str(binary)+':'+os.environ['PATH']},text=True,capture_output=True)
            self.assertEqual(result.returncode,72)
            self.assertIn('原工作卷保留',result.stderr)
            self.assertEqual(data.read_bytes(),b'x'*8192);self.assertEqual(list(destination.iterdir()),[])
