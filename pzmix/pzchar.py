"""Portable character files (.pzchar).

A .pzchar is a small ZIP_STORED archive containing two members:
  - manifest.json   metadata describing the character
  - character.blob  the raw player data blob (bytes) lifted from players.db

These files are designed to be shared between machines — typically a
remote player exports their character, sends the .pzchar to a host, and
the host imports the file into a new mix-and-export build. The host
should not need to type anything: the manifest carries the steamid /
username / coords / source mods.

Storage convention: ~/Zomboid/Characters/.

manifest.json schema (format_version: 1)
----------------------------------------
{
  "format_version":  1,
  "tool":            "PZSaveMixer",
  "exported_at":     "2026-05-24T15:30:00",
  "name":            "Mackenzie Whitten",
  "worldversion":    245,
  "is_dead":         false,
  "blob_size":       23827,
  "source_kind":     "SP" | "MP",
  "source_save":     "2026-05-05_23-02-38",
  "coords":          {"x": 2006.04, "y": 6109.66, "z": 1.0, "wx": 250, "wy": 763},
  "source_mods":     ["isoContainers", ...],
  "source_map":      "Muldraugh, KY" | null,
  "mp":              {"username": "Aedius", "steamid": "76561...", "player_index": 0} | null,
  "note":            null | "free text"
}
"""
from __future__ import annotations

import json
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .saves import Character


PZCHAR_EXTENSION = ".pzchar"
FORMAT_VERSION = 1


@dataclass
class PZCharFile:
    """A .pzchar on disk, loaded from manifest only — blob fetched lazily."""
    path: Path
    name: str
    is_dead: bool
    worldversion: int
    blob_size: int
    source_kind: str
    source_save: str
    coords: dict
    source_mods: list[str]
    source_map: Optional[str]
    note: Optional[str]
    exported_at: str
    mp_username: Optional[str]
    mp_steamid: Optional[str]
    mp_player_index: Optional[int]

    @property
    def has_mp_creds(self) -> bool:
        return bool(self.mp_steamid)

    @property
    def display_label(self) -> str:
        creds = f" [{self.mp_username}]" if self.mp_username else ""
        dead = " (DEAD)" if self.is_dead else ""
        return f"{self.name}{creds}{dead}"

    def load_blob(self) -> bytes:
        with zipfile.ZipFile(self.path) as zf:
            with zf.open("character.blob") as fp:
                data = fp.read()
        if len(data) != self.blob_size:
            raise RuntimeError(
                f"blob size mismatch in {self.path.name}: "
                f"manifest said {self.blob_size}, got {len(data)}")
        return data

    def to_character(self) -> Character:
        """Materialise an in-memory Character. The is_network flag is set
        at insert time by compose.py based on target kind, not here."""
        coords = self.coords or {}
        return Character(
            row_id=0,
            name=self.name,
            is_network=bool(self.mp_steamid),
            wx=coords.get("wx"),
            wy=coords.get("wy"),
            x=coords.get("x", 0.0),
            y=coords.get("y", 0.0),
            z=coords.get("z", 0.0),
            worldversion=self.worldversion,
            is_dead=self.is_dead,
            data_blob=self.load_blob(),
            world=None,
            username=self.mp_username,
            steamid=self.mp_steamid,
            player_index=self.mp_player_index,
        )


# ---------- write ----------

def export_character(
    character: Character,
    *,
    source_save_name: str,
    source_kind: str,
    source_mods: list[str],
    source_map: Optional[str],
    output_path: Path,
    note: Optional[str] = None,
    local_steam_user: Optional[object] = None,   # steam.SteamUser-like
    overwrite: bool = False,
) -> Path:
    """Write a single character to a .pzchar file.

    If the character has no MP credentials (i.e. came from an SP save) and
    `local_steam_user` is provided, the steamid/username from the local
    Steam install are bundled — so a remote host importing the file
    doesn't have to ask the exporting player for anything.
    """
    mp_user = character.username
    mp_sid = character.steamid
    mp_idx = character.player_index

    if not mp_sid and local_steam_user is not None:
        # SteamUser dataclass: steamid64 + account_name
        mp_user = getattr(local_steam_user, "account_name", None) or mp_user
        mp_sid = getattr(local_steam_user, "steamid64", None)
        if mp_idx is None:
            mp_idx = 0

    manifest = {
        "format_version": FORMAT_VERSION,
        "tool": "PZSaveMixer",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "name": character.name,
        "worldversion": character.worldversion,
        "is_dead": bool(character.is_dead),
        "blob_size": len(character.data_blob),
        "source_kind": source_kind,
        "source_save": source_save_name,
        "coords": {
            "x": character.x, "y": character.y, "z": character.z,
            "wx": character.wx, "wy": character.wy,
        },
        "source_mods": list(source_mods),
        "source_map": source_map,
        "mp": ({
            "username": mp_user,
            "steamid": mp_sid,
            "player_index": mp_idx,
        } if mp_sid else None),
        "note": note,
    }

    if output_path.exists() and not overwrite:
        raise FileExistsError(f"target already exists: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # ZIP_STORED — blob is opaque binary that won't compress meaningfully,
    # and tiny .pzchar files are I/O-bound on tear-down rather than CPU.
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("character.blob", character.data_blob)
    return output_path


def suggested_filename(character: Character, source_save_name: str) -> str:
    """A friendly default filename for a .pzchar export."""
    bad = re.compile(r'[<>:"/\\|?*\0]')
    parts = []
    if character.username:
        parts.append(bad.sub("_", character.username))
    parts.append(bad.sub("_", character.name or "Character"))
    parts.append(bad.sub("_", source_save_name))
    parts.append(time.strftime("%Y%m%d"))
    return "__".join(parts) + PZCHAR_EXTENSION


# ---------- read ----------

def load_pzchar(path: Path) -> PZCharFile:
    """Read just the manifest. Blob loaded on demand via PZCharFile.load_blob()."""
    with zipfile.ZipFile(path) as zf:
        with zf.open("manifest.json") as fp:
            m = json.loads(fp.read().decode("utf-8"))
    fmt = m.get("format_version", 0)
    if fmt != FORMAT_VERSION:
        raise RuntimeError(
            f"{path.name}: unknown .pzchar format_version {fmt} "
            f"(this build supports {FORMAT_VERSION})")
    mp = m.get("mp") or {}
    return PZCharFile(
        path=path,
        name=m.get("name", "?"),
        is_dead=bool(m.get("is_dead", False)),
        worldversion=int(m.get("worldversion", 0) or 0),
        blob_size=int(m.get("blob_size", 0) or 0),
        source_kind=m.get("source_kind", "SP"),
        source_save=m.get("source_save", "?"),
        coords=m.get("coords") or {},
        source_mods=list(m.get("source_mods") or []),
        source_map=m.get("source_map"),
        note=m.get("note"),
        exported_at=m.get("exported_at", "?"),
        mp_username=mp.get("username") if isinstance(mp, dict) else None,
        mp_steamid=(str(mp.get("steamid")) if isinstance(mp, dict) and mp.get("steamid") else None),
        mp_player_index=mp.get("player_index") if isinstance(mp, dict) else None,
    )


def list_pzchar_files(directory: Path) -> list[PZCharFile]:
    """Return every loadable .pzchar in the given directory, newest first."""
    if not directory.is_dir():
        return []
    out: list[PZCharFile] = []
    for p in sorted(directory.glob(f"*{PZCHAR_EXTENSION}"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            out.append(load_pzchar(p))
        except Exception:
            # Bad/corrupt file — skip silently so one bad file doesn't break the menu.
            continue
    return out
