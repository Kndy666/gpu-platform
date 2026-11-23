"""Unix-socket node process and live status client."""

import argparse
import json
import logging
import os
import signal
import socket
import socketserver
import threading
from pathlib import Path

import yaml

from gpu_platform_contracts.config import HostConfig, PlatformConfig
from gpu_platform_contracts.messages import Command, Operation, Reply, encode_message, read_message
from gpu_platform_contracts.texts import Message

from .commands import NodeEngine
from .datasets import Datasets
from .events import Events
from .slots import load_slots

logger = logging.getLogger(__name__)


def chunks(stream):
    while chunk := stream.read(1024 * 1024):
        yield chunk


class Handler(socketserver.StreamRequestHandler):
    server: "Server"

    def handle(self):
        self.request.settimeout(300)
        identity = "invalid"
        replied = False
        try:
            message = read_message(self.rfile)
            if message.get("channel") == "stream":
                identity = message["stream_id"]
                streams = self.server.engine.streams
                if message["direction"] == "upload":
                    size = streams.receive(identity, chunks(self.rfile))
                    self.wfile.write(encode_message(Reply(id=identity, ok=True, data={"size_bytes": size})))
                elif message["direction"] == "download":
                    # Validate and open before acknowledging the transfer.
                    with streams.download(identity) as (size, source):
                        self.wfile.write(
                            encode_message(
                                Reply(
                                    id=identity,
                                    ok=True,
                                    data={"size_bytes": size},
                                )
                            )
                        )
                        self.wfile.flush()
                        replied = True
                        for chunk in source:
                            self.wfile.write(chunk)
                else:
                    raise ValueError("invalid stream direction")
            else:
                command = Command.model_validate(message)
                identity = command.id
                self.wfile.write(encode_message(self.server.engine.handle(command)))
        except (OSError, ValueError, KeyError):
            if not replied:
                self.wfile.write(
                    encode_message(
                        Reply(id=identity, ok=False, error=Message(code="transport.invalid_request"))
                    )
                )


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path: Path, engine: NodeEngine, socket_group: int | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with socket.socket(socket.AF_UNIX) as probe:
                try:
                    probe.connect(str(path))
                except ConnectionRefusedError:
                    path.unlink()
                else:
                    raise RuntimeError("node socket is already active")
        self.path = path
        self.engine = engine
        super().__init__(str(path), Handler)
        path.chmod(0o660)
        if socket_group is not None:
            os.chown(path, -1, socket_group)

    def server_close(self):
        super().server_close()
        self.path.unlink(missing_ok=True)


def merge(base, override):
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def configured_engine(config_path: Path, host_name: str):
    data = yaml.safe_load(config_path.read_text())
    config = PlatformConfig.model_validate(data)
    host = HostConfig.model_validate(merge(data.get("defaults", {}), data["hosts"][host_name]))
    directory = Path(host.parameters.get("state_directory", config.cluster.state_directory))
    directory.mkdir(parents=True, exist_ok=True)
    slots = load_slots(host, host_name, directory, Events(directory / "events").publish)
    features = host.features.model_dump()
    if sum("node" in item.roles for item in config.hosts.values()) < 2:
        features["image_sync"] = features["dataset_sync"] = False
    datasets = (
        Datasets(Path(host.parameters.get("datasets", "/data/datasets")), host_name)
        if features["public_datasets"]
        else None
    )
    return NodeEngine(host_name, host.architecture, slots, directory, features, datasets), host


def status(path: str):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(15)
        connection.connect(path)
        command = Command(operation=Operation.STATUS)
        connection.sendall(encode_message(command))
        with connection.makefile("rb") as stream:
            reply = Reply.model_validate(read_message(stream))
        if not reply.ok or reply.id != command.id:
            raise RuntimeError("node status request failed")
        return reply.data


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("PLATFORM_CONFIG", "/etc/gpu-platform/platform.yaml")),
    )
    serve.add_argument("--host", required=True)
    serve.add_argument("--socket")
    inspect = commands.add_parser("status")
    inspect.add_argument("--socket", default="/var/lib/gpu-platform/node.sock")
    inspect.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.command == "status":
        print(json.dumps(status(args.socket)))
        return
    engine, host = configured_engine(args.config, args.host)
    path = Path(args.socket or host.parameters.get("socket_path", str(engine.directory / "node.sock")))
    stop = threading.Event()
    with Server(path, engine, host.parameters.get("socket_group", 10001)) as server:
        engine.configure_peer_ssh()

        def autonomy():
            while not stop.is_set():
                try:
                    engine.tick()
                except Exception:
                    logger.exception("Node autonomy cycle failed")
                stop.wait(5)

        worker = threading.Thread(target=autonomy, daemon=True)
        worker.start()

        def shutdown(*_):
            stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            stop.set()
            worker.join(timeout=10)
            if engine.peer:
                engine.peer.close()


if __name__ == "__main__":
    main()
