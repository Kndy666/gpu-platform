import os
from functools import lru_cache
from pathlib import Path
import yaml

@lru_cache(maxsize=1)
def config():
    path = os.environ.get('PLATFORM_CONFIG', str(Path(__file__).resolve().parents[2] / 'config.example.yaml'))
    with open(path) as f:
        return yaml.safe_load(f)
