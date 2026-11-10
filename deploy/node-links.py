#!/usr/bin/env python3
"""Controller-host SSH multiplexer and public workspace port forwarding."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socketserver
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from portal.node_transport import read_message, encode_message, OPERATIONS


class Links:
    def __init__(self, directory, runtime, start, end):
        self.directory, self.runtime = Path(directory), Path(runtime)
        self.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.start, self.end = start, end
        self.forwards = {}
        self.limits = {}
        self.lock = threading.RLock()

    def command(self, connection):
        if not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', str(connection.get(key, ''))) for key in ('name', 'identity')):
            raise ValueError('Invalid node or identity name')
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', connection.get('user', '')):
            raise ValueError('Invalid SSH user')
        port = connection['port']
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('Invalid SSH port')
        identity = self.directory / connection['identity']
        if not identity.is_file() or identity.is_symlink():
            raise ValueError('Node private key is unavailable')
        digest = hashlib.sha256(json.dumps(connection, sort_keys=True).encode()).hexdigest()[:24]
        return ['ssh', '-T', '-p', str(port), '-i', str(identity), '-o', 'IdentitiesOnly=yes',
                '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'UserKnownHostsFile=' + str(self.directory / 'known_hosts'),
                '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
                '-o', 'ControlMaster=auto', '-o', 'ControlPersist=60', '-S', str(self.runtime / digest)], connection['user'] + '@127.0.0.1'

    def call(self, connection, operation, parameters):
        if operation not in OPERATIONS:
            raise ValueError('Unsupported node operation')
        command, host = self.command(connection)
        if operation == 'forwards':
            ports = parameters.get('ports', [])
            if len(ports) != len(set(ports)) or any(type(port) is not int or not self.start <= port <= self.end for port in ports):
                raise ValueError('Forwarded ports must belong to the controller port pool')
            with self.lock:
                alive = subprocess.run([*command, '-O', 'check', host], capture_output=True).returncode == 0
                if not alive:
                    subprocess.run([*command, '-MNf', host], check=True, capture_output=True, timeout=15)
                    self.forwards[connection['name']] = set()
                current = self.forwards.setdefault(connection['name'], set())
                for port in sorted(current - set(ports)):
                    subprocess.run([*command, '-O', 'cancel', '-L', f'0.0.0.0:{port}:127.0.0.1:{port}', host], check=True, capture_output=True, timeout=10)
                    current.remove(port)
                for port in sorted(set(ports) - current):
                    subprocess.run([*command, '-o', 'ExitOnForwardFailure=yes', '-O', 'forward', '-L', f'0.0.0.0:{port}:127.0.0.1:{port}', host], check=True, capture_output=True, timeout=10)
                    current.add(port)
            return {'ports': sorted(current)}
        with self.lock:
            limit = self.limits.setdefault(connection['name'], threading.BoundedSemaphore(4))
        if not limit.acquire(timeout=5):
            raise RuntimeError('Node connection is busy; retry the request')
        try:
            completed = subprocess.run([*command, host, '/usr/local/bin/gpu-platform-worker', 'call'],
                                       input=encode_message({'operation': operation, 'parameters': parameters}),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45, check=True)
        finally:
            limit.release()
        import io
        response = read_message(io.BytesIO(completed.stdout))
        if not response.get('ok'):
            raise RuntimeError(response.get('error', 'Worker request failed'))
        return response.get('result')


def serve(links, path):
    class Handler(socketserver.StreamRequestHandler):
        timeout = 50
        def handle(self):
            try:
                message = read_message(self.rfile)
                result = links.call(message['connection'], message['operation'], message.get('parameters', {}))
                response = {'ok': True, 'result': result}
            except Exception as exc:
                response = {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:500]}
            self.wfile.write(encode_message(response))
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    path = Path(path)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with Server(str(path), Handler) as server:
        os.chown(path, 0, 10001)
        path.chmod(0o660)
        server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-dir', required=True)
    parser.add_argument('--socket', default='/run/gpu-platform/controller.sock')
    parser.add_argument('--ports', default='30022:30121')
    args = parser.parse_args()
    first, last = map(int, args.ports.split(':'))
    if not 30000 <= first <= last <= 32767:
        parser.error('Invalid public port pool')
    serve(Links(args.key_dir, '/run/gpu-platform/ssh', first, last), args.socket)
