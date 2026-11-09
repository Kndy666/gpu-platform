#!/usr/bin/env python3
"""Verify local-reference cleanup using two disposable tags of an existing image."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import uuid


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo')
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('cleanup_agent', root / 'deploy/node-agent.py')
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    command = ['k3s', 'ctr', '--namespace', 'k8s.io', 'images']
    source = 'docker.io/library/gpu-platform:0.2.0'
    operation = uuid.uuid4()
    references = ['docker.io/library/platform-save:' + operation.hex,
                  'localhost:5000/platform-cleanup-check/environment:' + operation.hex]
    before = set(agent.run([*command, 'list', '-q']).splitlines())
    assert source in before and not (set(references) & before)
    try:
        for reference in references:
            agent.run([*command, 'tag', source, reference])
        with tempfile.TemporaryDirectory(prefix='platform-image-cleanup-check-') as directory:
            agent.IMAGE_CLEANUP_DIR = Path(directory)
            record = Path(directory) / (operation.hex + '.json')
            record.write_text(json.dumps(references))
            agent.retry_image_cleanup()
            assert not record.exists(), 'Cleanup did not finish'
            # A retried operation must also succeed after its tags are gone.
            record.write_text(json.dumps(references))
            agent.retry_image_cleanup()
            assert not record.exists(), 'Missing references must be idempotent'
        after = set(agent.run([*command, 'list', '-q']).splitlines())
        assert not (set(references) & after)
        assert before <= after, 'An existing image reference was removed'
        print(json.dumps({'local_references_removed': 2, 'missing_reference_retry': 'pass', 'existing_references_preserved': True}))
    finally:
        for reference in references:
            agent.run([*command, 'rm', reference])


if __name__ == '__main__':
    main()
