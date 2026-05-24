"""Model a single discovered save (SP or hosted-MP) and its metadata."""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from . import paths


SAVE_KIND_SP = "SP"
SAVE_KIND_MP = "MP"


@dataclass
class Character:
    """One playable character (alive or dead) inside a save."""
    row_id: int
    name: str
    is_network: bool          # True → networkPlayers row, False → localPlayers
    wx: int | None
    wy: int | None
    x: float
    y: float
    z: float
    worldversion: int
    is_dead: bool
    data_blob: bytes
    # MP-only:
    world: str | None = None
    username: str | None = None
    steamid: str | None = None
    player_index: int | None = None

    @property
    def short_coords(self) -> str:
        return f"({int(self.x)}, {int(self.y)})"


@dataclass
class Save:
    kind: str                  # "SP" or "MP"
    mode: str                  # Apocalypse / Builder / Sandbox / Survivor / Multiplayer
    name: str                  # folder name
    world_dir: Path
    player_dir: Path | None = None    # MP-host: <name>_player
    server_ini: Path | None = None    # MP-host: Server\<name>.ini
    mods: list[str] = field(default_factory=list)
    map_value: str | None = None      # "Map" setting (e.g. "Muldraugh, KY")
    sandbox_vars_path: Path | None = None
    worldversion: int | None = None
    last_played: datetime | None = None
    characters: list[Character] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.mode})"

    @property
    def alive_characters(self) -> list[Character]:
        return [c for c in self.characters if not c.is_dead]

    @property
    def dead_characters(self) -> list[Character]:
        return [c for c in self.characters if c.is_dead]

    @property
    def is_mp_client_cache(self) -> bool:
        """True when this MP save has no Server\\<name>.ini — i.e. it's a
        client-side cache from a remote server, not a hosted MP world."""
        return self.kind == SAVE_KIND_MP and self.server_ini is None


# ---------- discovery ----------

def discover_all() -> list[Save]:
    saves: list[Save] = []
    for mode, world_dir in paths.iter_sp_save_dirs():
        try:
            saves.append(load_sp_save(mode, world_dir))
        except Exception as e:
            saves.append(_broken_save(SAVE_KIND_SP, mode, world_dir, repr(e)))
    for world_dir, player_dir in paths.iter_mp_host_save_dirs():
        try:
            saves.append(load_mp_save(world_dir, player_dir))
        except Exception as e:
            saves.append(_broken_save(SAVE_KIND_MP, paths.MP_MODE, world_dir, repr(e)))
    saves.sort(key=lambda s: (s.last_played or datetime.min), reverse=True)
    return saves


def _broken_save(kind: str, mode: str, world_dir: Path, err: str) -> Save:
    s = Save(kind=kind, mode=mode, name=world_dir.name, world_dir=world_dir)
    s.mods = [f"<error: {err}>"]
    return s


def load_sp_save(mode: str, world_dir: Path) -> Save:
    s = Save(kind=SAVE_KIND_SP, mode=mode, name=world_dir.name, world_dir=world_dir)
    s.mods = _read_sp_mods(world_dir / "mods.txt")
    sb = _find_sandbox_vars(world_dir)
    s.sandbox_vars_path = sb
    s.map_value = _read_map_from_sandbox(sb) if sb else None
    s.characters = _load_characters(world_dir / "players.db")
    if s.characters:
        s.worldversion = s.characters[0].worldversion
    s.last_played = _folder_mtime(world_dir)
    return s


def load_mp_save(world_dir: Path, player_dir: Path | None) -> Save:
    name = world_dir.name
    server_ini = paths.server_config_dir() / f"{name}.ini"
    s = Save(
        kind=SAVE_KIND_MP,
        mode=paths.MP_MODE,
        name=name,
        world_dir=world_dir,
        player_dir=player_dir,
        server_ini=server_ini if server_ini.is_file() else None,
    )
    if s.server_ini:
        s.mods = _read_mp_mods(s.server_ini)
        s.map_value = _read_ini_value(s.server_ini, "Map")
    sb = paths.server_config_dir() / f"{name}_SandboxVars.lua"
    if sb.is_file():
        s.sandbox_vars_path = sb
    s.characters = _load_characters(world_dir / "players.db")
    if s.characters:
        s.worldversion = s.characters[0].worldversion
    s.last_played = _folder_mtime(world_dir)
    return s


# ---------- helpers ----------

def _folder_mtime(p: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime)
    except OSError:
        return None


def _read_sp_mods(mods_txt: Path) -> list[str]:
    if not mods_txt.is_file():
        return []
    out: list[str] = []
    text = mods_txt.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        m = re.match(r"\s*mod\s*=\s*([^,\s]+)", line)
        if m:
            out.append(m.group(1).strip())
    return out


def _read_mp_mods(server_ini: Path) -> list[str]:
    line = _read_ini_value(server_ini, "Mods")
    if not line:
        return []
    return [m.strip() for m in line.split(";") if m.strip()]


def _read_ini_value(ini: Path, key: str) -> str | None:
    if not ini.is_file():
        return None
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*)$", re.IGNORECASE)
    for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat.match(line)
        if m:
            return m.group(1).strip()
    return None


def _find_sandbox_vars(world_dir: Path) -> Path | None:
    # SP: world_dir/sandbox_vars or similar — actually SP sandbox lives elsewhere.
    # PZ encodes SP sandbox in map_sand.bin (binary). For now leave None; we read map from server ini in MP only.
    return None


def _read_map_from_sandbox(sb: Path) -> str | None:
    if not sb.is_file():
        return None
    try:
        txt = sb.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"Map\s*=\s*\"([^\"]+)\"", txt)
    return m.group(1) if m else None


# ---------- characters ----------

def _load_characters(players_db: Path) -> list[Character]:
    if not players_db.is_file():
        return []
    # Always operate on a copy to avoid touching the original (Golden Rule).
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        shutil.copyfile(players_db, tmp)
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        try:
            return _read_chars(con)
        finally:
            con.close()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _table_cols(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


def _read_chars(con: sqlite3.Connection) -> list[Character]:
    """Select only columns that exist — old saves (Build 41) lack steamid/world."""
    chars: list[Character] = []
    tbls = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "localPlayers" in tbls:
        cols = set(_table_cols(con, "localPlayers"))
        sel = [c for c in ("id", "name", "wx", "wy", "x", "y", "z",
                           "worldversion", "data", "isDead") if c in cols]
        for row in con.execute(f"SELECT {','.join(sel)} FROM localPlayers"):
            d = dict(zip(sel, row))
            chars.append(Character(
                row_id=d.get("id", 0),
                name=_strip_quotes(d.get("name", "")),
                is_network=False,
                wx=d.get("wx"), wy=d.get("wy"),
                x=d.get("x", 0.0), y=d.get("y", 0.0), z=d.get("z", 0.0),
                worldversion=d.get("worldversion") or 0,
                data_blob=d.get("data") or b"",
                is_dead=bool(d.get("isDead")),
            ))

    if "networkPlayers" in tbls:
        cols = set(_table_cols(con, "networkPlayers"))
        sel = [c for c in ("id", "world", "username", "playerIndex", "name",
                           "steamid", "x", "y", "z", "worldversion", "data",
                           "isDead") if c in cols]
        for row in con.execute(f"SELECT {','.join(sel)} FROM networkPlayers"):
            d = dict(zip(sel, row))
            chars.append(Character(
                row_id=d.get("id", 0),
                name=_strip_quotes(d.get("name", "")),
                is_network=True,
                wx=None, wy=None,
                x=d.get("x", 0.0), y=d.get("y", 0.0), z=d.get("z", 0.0),
                worldversion=d.get("worldversion") or 0,
                data_blob=d.get("data") or b"",
                is_dead=bool(d.get("isDead")),
                world=_strip_quotes(d.get("world")) if d.get("world") else None,
                username=_strip_quotes(d.get("username")) if d.get("username") else None,
                steamid=_strip_quotes(d.get("steamid")) if d.get("steamid") else None,
                player_index=d.get("playerIndex"),
            ))
    return chars


def _strip_quotes(v):
    """PZ stores some strings with surrounding single quotes literally in the column."""
    if isinstance(v, str) and len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1]
    return v
