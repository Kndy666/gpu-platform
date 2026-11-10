import os
from functools import lru_cache
from pathlib import Path
import yaml

@lru_cache(maxsize=1)
def base_config():
    path = os.environ.get('PLATFORM_CONFIG', str(Path(__file__).resolve().parents[2] / 'config.example.yaml'))
    with open(path) as f:
        return yaml.safe_load(f)


def config():
    from .node_context import active_node, node_config
    node = active_node.get()
    return node_config(node, base_config()) if node else base_config()


config.cache_clear = base_config.cache_clear
