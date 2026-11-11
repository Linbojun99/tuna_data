from __future__ import annotations

import os
from pathlib import Path
import tomllib


def config_path() -> Path:
    return Path(os.environ.get("TUNA_DATA_CONFIG", Path.home() / ".config/tuna-data/config.toml")).expanduser()


def settings() -> dict:
    path = config_path()
    if not path.is_file():
        return {}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def data_home(root=None) -> Path:
    cfg = settings()
    selected = root or os.environ.get("TUNA_DATA_HOME") or cfg.get("storage", {}).get("root")
    return Path(selected or Path.home() / ".local/share/tuna-data").expanduser().absolute()
