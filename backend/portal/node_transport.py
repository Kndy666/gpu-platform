"""Bounded JSON RPC over a local Unix socket; credentials stay on the host."""
import json
import os
import socket
import time
from contextlib import contextmanager
from contextvars import ContextVar

MAX_MESSAGE = 8 * 1024 * 1024
OPERATIONS = {'observe', 'kube', 'volume_stats', 'registry', 'dataset', 'forwards'}
deadline = ContextVar('node_request_deadline', default=None)


@contextmanager
def request_budget(seconds):
    token = deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        deadline.reset(token)


class NodeUnavailable(RuntimeError):
    pass


def read_message(stream):
    line = stream.readline(MAX_MESSAGE + 1)
    if not line or len(line) > MAX_MESSAGE or not line.endswith(b'\n'):
        raise ValueError('Invalid or oversized node message')
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError('Node message must be an object')
    return value


def encode_message(value):
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
    if len(data) > MAX_MESSAGE:
        raise ValueError('Node response exceeds message limit')
    return data


def socket_call(path, message, timeout=50):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(path)
            connection.sendall(encode_message(message))
            with connection.makefile('rb') as stream:
                result = read_message(stream)
    except (OSError, ValueError) as exc:
        raise NodeUnavailable('节点连接不可用：' + type(exc).__name__) from exc
    if not result.get('ok'):
        raise NodeUnavailable(result.get('error', '节点请求失败'))
    return result.get('result')


def node_call(node, operation, **parameters):
    if operation not in OPERATIONS:
        raise ValueError('Unsupported node operation')
    remaining = deadline.get()
    timeout = min(50, remaining - time.monotonic()) if remaining is not None else 50
    if timeout <= 0:
        raise NodeUnavailable('本轮节点同步已超时，下一轮继续')
    return socket_call(os.environ.get('PLATFORM_CONNECTOR_SOCKET', '/run/gpu-platform/controller.sock'),
                       {'connection': {'name': node.name, 'port': node.ssh_port,
                                       'user': node.ssh_user, 'identity': node.identity},
                        'operation': operation, 'parameters': parameters}, timeout=timeout)
