"""Bounded control messages and independently streamed payloads."""

import json
import socket
from contextlib import contextmanager
from typing import BinaryIO

from gpu_platform_contracts.messages import MAX_MESSAGE_BYTES


def encode_message(value: dict) -> bytes:
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("message_too_large")
    return data


def read_message(stream: BinaryIO) -> dict:
    line = stream.readline(MAX_MESSAGE_BYTES + 1)
    if not line.endswith(b"\n") or len(line) > MAX_MESSAGE_BYTES:
        raise ValueError("invalid_message_frame")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("invalid_message_type")
    return value


@contextmanager
def connect(path: str, timeout: float = 50):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(path)
        yield connection
    finally:
        connection.close()


def exchange(path: str, message: dict, timeout: float = 50) -> dict:
    with connect(path, timeout) as connection:
        connection.sendall(encode_message(message))
        with connection.makefile("rb") as stream:
            return read_message(stream)
