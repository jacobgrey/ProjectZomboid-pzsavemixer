"""Locate the Zomboid user-data root and enumerate save folders."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

SP_MODES = ("Apocalypse", "Builder", "Sandbox", "Survivor")
MP_MODE = "Multiplayer"
ALL_MODES = SP_MODES + (MP_MODE,)


def zomboid_root() -> Path:
    """Return the user's ~/Zomboid directory."""
    env = os.environ.get("PZ_HOME")
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    return Path.home() / "Zomboid"


def saves_root() -> Path:
    return zomboid_root() / "Saves"


def server_config_dir() -> Path:
    return zomboid_root() / "Server"


def dedicated_db_dir() -> Path:
    return zomboid_root() / "db"


def characters_dir() -> Path:
    """User-facing collection of exported .pzchar files. Created on demand."""
    d = zomboid_root() / "Characters"
    d.mkdir(parents=True, exist_ok=True)
    return d


def iter_sp_save_dirs() -> Iterable[tuple[str, Path]]:
    """Yield (mode, save_dir) for each SP save folder."""
    root = saves_root()
    for mode in SP_MODES:
        mode_dir = root / mode
        if not mode_dir.is_dir():
            continue
        for entry in sorted(mode_dir.iterdir()):
            if entry.is_dir():
                yield mode, entry


def iter_mp_host_save_dirs() -> Iterable[tuple[Path, Path | None]]:
    """Yield (world_dir, player_dir_or_None) for each hosted MP save.

    The "_player" folder is the host's client-side cache; it may be missing
    if the host hasn't actually played the world yet.
    """
    mp = saves_root() / MP_MODE
    if not mp.is_dir():
        return
    seen: set[str] = set()
    for entry in sorted(mp.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name.endswith("_player"):
            continue
        if name in seen:
            continue
        seen.add(name)
        player = mp / f"{name}_player"
        yield entry, (player if player.is_dir() else None)


