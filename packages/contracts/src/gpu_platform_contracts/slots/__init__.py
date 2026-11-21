from .auth import Auth, Identity
from .build import Build, BuildResult, BuildSpec
from .entry import Entry
from .gpu import GPU
from .metrics import Metrics
from .notify import Notify
from .registry import Registry
from .runtime import Runtime
from .storage import Storage
from .transport import Transport

SLOT_CONTRACTS = {
    "auth": Auth,
    "transport": Transport,
    "entry": Entry,
    "runtime": Runtime,
    "storage": Storage,
    "registry": Registry,
    "build": Build,
    "gpu": GPU,
    "metrics": Metrics,
    "notify": Notify,
}

__all__ = [
    "Auth",
    "Identity",
    "Build",
    "BuildResult",
    "BuildSpec",
    "Entry",
    "GPU",
    "Metrics",
    "Notify",
    "Registry",
    "Runtime",
    "Storage",
    "Transport",
    "SLOT_CONTRACTS",
]
