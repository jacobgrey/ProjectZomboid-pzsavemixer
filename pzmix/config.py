"""Tiny persistent config for things that have to survive between sessions.

Currently only stores a non-default Zomboid root chosen by the user.

Portability rule: the config file is NEVER created unless the user
explicitly sets a custom path. If the tool happily finds ~/Zomboid (or
$PZ_HOME) it writes nothing to disk anywhere outside the project — so
the tool can live in a Dropbox-synced folder without leaking
machine-specific state.

The file is OS-standard per-user when it does get written:
  Windows: %LOCALAPPDATA%\\PZSaveMixer\\config.json
  POSIX:   $XDG_CONFIG_HOME/pzsavemixer/config.json
            (falls back to ~/.config/pzsavemixer/)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


APP_NAME = "PZSaveMixer"


def config_dir() -> Path:
    """Return the OS-appropriate per-user config directory for this tool."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") \
            or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME.lower()


def config_path() -> Path:
    return config_dir() / "config.json"


def config_exists() -> bool:
    return config_path().is_file()


def load_config() -> dict[str, Any]:
    p = config_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(cfg: dict[str, Any]) -> None:
    """Write config to disk. Only called from set_zomboid_root /
    clear_zomboid_root — never from the read path. If we end up with an
    empty config we delete the file (and the dir if empty) rather than
    leave an empty file on disk."""
    p = config_path()
    if not cfg:
        if p.is_file():
            try: p.unlink()
            except OSError: pass
        d = p.parent
        if d.is_dir():
            try: d.rmdir()    # only succeeds if empty
            except OSError: pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ----- Zomboid root specifically -----

def get_zomboid_root() -> Path | None:
    """Return the configured Zomboid root if one was saved AND still exists.
    Returning None means "fall back to default discovery"."""
    raw = load_config().get("zomboid_root")
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def set_zomboid_root(p: Path) -> None:
    """Persist a user-chosen Zomboid root. This is the FIRST disk write."""
    cfg = load_config()
    cfg["zomboid_root"] = str(p)
    _save_config(cfg)


def clear_zomboid_root() -> None:
    """Forget any saved override; tool goes back to default discovery."""
    cfg = load_config()
    cfg.pop("zomboid_root", None)
    _save_config(cfg)
