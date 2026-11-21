"""Persist registry identities and publish repository-specific access rules."""

import fcntl
import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

import bcrypt


def atomic_write(path, content):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(content)
    temporary.chmod(0o600)
    temporary.replace(path)


class Accounts:
    def __init__(self, root, options, reload):
        self.root, self.options, self.reload = Path(root), options, reload
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def identity(self, owner):
        with (self.root / "accounts.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root / "accounts.json"
            accounts = json.loads(path.read_text()) if path.exists() else {}
            account = accounts.get(owner)
            pending = self.root / "reload-pending"
            if account is None:
                prefix = "user-" + hashlib.sha256(owner.encode()).hexdigest()[:32]
                password = secrets.token_urlsafe(32)
                account = {"writer": {
                        "username": prefix,
                        "password": password,
                        "hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                    }}
                accounts[owner] = account
                pending.touch(mode=0o600)
                atomic_write(path, json.dumps(accounts))
            if pending.exists():
                self.publish(accounts)
                self.reload()
                pending.unlink()
            return account

    def reader(self, scope, repositories, *, remove=False):
        """Issue or revoke one read identity, with a durable reload journal."""
        with (self.root / "accounts.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root / "readers.json"
            readers = json.loads(path.read_text()) if path.exists() else {}
            pending = self.root / "reload-pending"
            account = readers.get(scope)
            if remove and account is not None:
                del readers[scope]
                pending.touch(mode=0o600)
                atomic_write(path, json.dumps(readers))
            elif not remove:
                repositories = sorted(set(repositories))
                if account and account["repositories"] != repositories:
                    raise ValueError("registry.reader_scope_conflict")
                if account is None:
                    password = secrets.token_urlsafe(32)
                    account = {"username": "read-" + hashlib.sha256(scope.encode()).hexdigest()[:32],
                               "password": password,
                               "hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                               "repositories": repositories}
                    readers[scope] = account
                    pending.touch(mode=0o600)
                    atomic_write(path, json.dumps(readers))
            if pending.exists():
                owners = self.root / "accounts.json"
                self.publish(json.loads(owners.read_text()) if owners.exists() else {})
                self.reload()
                pending.unlink()
            return account

    def publish(self, accounts):
        from .options import Options
        from .pools import storage_config

        options = Options.model_validate(self.options)
        repositories: dict[str, Any] = {"**": {"defaultPolicy": []}}
        lines = []
        for owner, account in accounts.items():
            prefix = "user-" + hashlib.sha256(owner.encode()).hexdigest()[:32]
            writer = {
                "policies": [
                    {
                        "users": [account["writer"]["username"]],
                        "actions": ["read", "create", "update", "delete"],
                    },
                ]
            }
            repositories[prefix + "/**"] = writer
            for pool in options.pools:
                repositories[f"pool-{pool.name}/{prefix}/**"] = writer
            for identity in account.values():
                lines.append(identity["username"] + ":" + identity["hash"])
        readers_path = self.root / "readers.json"
        readers = json.loads(readers_path.read_text()) if readers_path.exists() else {}
        for account in readers.values():
            lines.append(account["username"] + ":" + account["hash"])
            for repository in account["repositories"]:
                owner_namespace = repository.rsplit("/", 1)[0]
                # An exact match must retain the writer policy from its namespace.
                inherited = repositories.get(owner_namespace + "/**", {}).get("policies", [])
                policies = repositories.setdefault(repository, {"policies": list(inherited)})["policies"]
                policies.append({"users": [account["username"]], "actions": ["read"]})
        config = {
            "distSpecVersion": "1.1.1",
            "storage": storage_config(options),
            "http": {
                "address": "0.0.0.0",
                "port": str(self.options.get("port", 5000)),
                "tls": {"cert": "/etc/zot/tls.crt", "key": "/etc/zot/tls.key"},
                "auth": {"htpasswd": {"path": "/etc/zot/htpasswd"}},
                "accessControl": {"repositories": repositories},
            },
            "log": {"level": "info"},
        }
        atomic_write(self.root / "htpasswd", "\n".join(lines) + "\n")
        atomic_write(self.root / "config.json", json.dumps(config))
