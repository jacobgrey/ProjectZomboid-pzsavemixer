"""Best-effort discovery of the current Windows user's Steam credentials.

Strategy:
  1. Locate the Steam install directory via HKCU/HKLM registry.
  2. Read <SteamPath>/config/loginusers.vdf.
  3. Find the user block with MostRecent=1 (most-recently logged in account).
     The block key is the 17-digit SteamID64.
  4. Fall back to highest Timestamp if MostRecent isn't set anywhere.

Returns (steamid64, persona_name, account_name) or None. The persona_name is
what shows up in-game; account_name is the Steam login name. Either makes a
reasonable default for PZ's networkPlayers.username column.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SteamUser:
    steamid64: str
    persona_name: Optional[str] = None
    account_name: Optional[str] = None


def find_current_user() -> Optional[SteamUser]:
    vdf = _locate_loginusers_vdf()
    if not vdf or not vdf.is_file():
        return None
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _parse_most_recent(text)


# ---------- locating Steam ----------

# Project Zomboid's Steam App ID — used for both the install path and the
# workshop content folder (steamapps/workshop/content/108600/).
PZ_APP_ID = "108600"


def _steam_main_install() -> Optional[Path]:
    """Return the main Steam install directory (where libraryfolders.vdf lives)."""
    if os.name == "nt":
        for hive_path in (
            ("HKCU", r"Software\Valve\Steam", ("SteamPath",)),
            ("HKLM", r"SOFTWARE\WOW6432Node\Valve\Steam", ("InstallPath",)),
            ("HKLM", r"SOFTWARE\Valve\Steam", ("InstallPath",)),
        ):
            v = _registry_read(*hive_path)
            if v:
                p = Path(v)
                if p.is_dir():
                    return p
    candidates = [
        Path("C:/Program Files (x86)/Steam"),
        Path("C:/Program Files/Steam"),
        Path.home() / ".steam" / "steam",
        Path.home() / ".local" / "share" / "Steam",
        Path.home() / "Library" / "Application Support" / "Steam",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _locate_loginusers_vdf() -> Optional[Path]:
    main = _steam_main_install()
    if main:
        p = main / "config" / "loginusers.vdf"
        if p.is_file():
            return p
    return None


def steam_libraries() -> list[Path]:
    """Return every Steam library on this machine — the main install plus
    any extra SteamLibrary folders listed in libraryfolders.vdf."""
    main = _steam_main_install()
    if not main:
        return []
    libs: list[Path] = [main]
    vdf = main / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return libs
        # Entries look like:  "path"   "C:\\SteamLibrary"
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            raw = m.group(1).replace("\\\\", "\\")
            p = Path(raw)
            if p.is_dir() and not any(p.resolve() == lib.resolve() for lib in libs):
                libs.append(p)
    return libs


def find_workshop_mods(app_id: str = PZ_APP_ID) -> dict[str, str]:
    """Scan every Steam library's workshop content folder for app_id and
    return a {mod_id: workshop_id} mapping.

    A single Steam workshop item can package multiple PZ mods (e.g. Tomb's
    Player Body workshop = TombBody + TombBodyCustom + TombBodyTex + …).
    Each sub-mod's *internal mod ID* lives in its mod.info file as `id=XXX`,
    NOT in the folder name (folder names are human-readable display names
    that often differ from the mod ID). So we must read mod.info, not just
    walk directories.

    PZ build-specific subdirs (`42.0/`, `42.12/`, `42.13/`, `common/`) may
    also contain their own mod.info — we walk those too and dedupe by id.

    Returns {} if no workshop content is found.
    When a mod_id appears in multiple workshop items, the first one wins."""
    result: dict[str, str] = {}
    for lib in steam_libraries():
        ws = lib / "steamapps" / "workshop" / "content" / app_id
        if not ws.is_dir():
            continue
        for entry in ws.iterdir():
            if not entry.is_dir() or not entry.name.isdigit():
                continue
            workshop_id = entry.name
            mods_dir = entry / "mods"
            if not mods_dir.is_dir():
                continue
            for mod_dir in mods_dir.iterdir():
                if not mod_dir.is_dir():
                    continue
                for mod_id in _extract_mod_ids(mod_dir):
                    result.setdefault(mod_id, workshop_id)
    return result


def find_local_mods(zomboid_root: Optional[Path] = None) -> set[str]:
    """Return the set of mod IDs installed under ~/Zomboid/mods/.

    Reads mod.info from each subdirectory the same way workshop mods do —
    so we use the canonical internal ID, not the folder name."""
    if zomboid_root is None:
        zomboid_root = Path.home() / "Zomboid"
    mods_dir = zomboid_root / "mods"
    if not mods_dir.is_dir():
        return set()
    out: set[str] = set()
    for mod_dir in mods_dir.iterdir():
        if not mod_dir.is_dir():
            continue
        for mod_id in _extract_mod_ids(mod_dir):
            out.add(mod_id)
    return out


def _extract_mod_ids(mod_dir: Path) -> list[str]:
    """Find every distinct `id=…` declared in any mod.info under mod_dir.

    Walks the top level plus any build-specific subdirs. A single mod can
    declare its id in multiple mod.info files (B41 + B42 variants); dedupe.
    If no mod.info is found at all, fall back to the folder name as a
    last-resort guess so the caller at least sees a candidate."""
    seen: list[str] = []
    for info in mod_dir.rglob("mod.info"):
        try:
            text = info.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"^\s*id\s*=\s*(.+?)\s*$", text, re.MULTILINE)
        if m:
            mid = m.group(1).strip()
            if mid and mid not in seen:
                seen.append(mid)
    if not seen:
        seen.append(mod_dir.name)
    return seen


def _registry_read(hive: str, subkey: str, value_names: tuple) -> Optional[str]:
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    root = {"HKCU": winreg.HKEY_CURRENT_USER,
            "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]
    try:
        with winreg.OpenKey(root, subkey) as k:
            for name in value_names:
                try:
                    v, _ = winreg.QueryValueEx(k, name)
                    if isinstance(v, str) and v:
                        return v
                except OSError:
                    continue
    except OSError:
        return None
    return None


# ---------- VDF parsing (minimal — only what we need) ----------

# A user block looks like:
#   "76561197983586286"
#   {
#       "AccountName"   "goodwinfam03"
#       "PersonaName"   "Aedius"
#       "MostRecent"    "1"
#       "Timestamp"     "1778686262"
#       ...
#   }
# Regex captures the 17-digit key and the body up to the matching brace.
# This deliberately keeps it simple — we only ever scan one section ("users")
# of one specific file Valve writes, so the structure is well-defined.

_BLOCK_RE = re.compile(
    r'"(\d{17})"\s*\{([^{}]*)\}',
    re.DOTALL,
)


def _parse_most_recent(text: str) -> Optional[SteamUser]:
    blocks = list(_BLOCK_RE.finditer(text))
    if not blocks:
        return None

    def field(body: str, key: str) -> Optional[str]:
        m = re.search(rf'"{key}"\s+"([^"]*)"', body)
        return m.group(1) if m else None

    # First pass: pick any with MostRecent=1.
    for m in blocks:
        body = m.group(2)
        if field(body, "MostRecent") == "1":
            return SteamUser(
                steamid64=m.group(1),
                persona_name=field(body, "PersonaName"),
                account_name=field(body, "AccountName"),
            )
    # Fallback: highest Timestamp wins.
    scored = []
    for m in blocks:
        body = m.group(2)
        ts = field(body, "Timestamp") or "0"
        try:
            scored.append((int(ts), m.group(1), body))
        except ValueError:
            continue
    if not scored:
        return None
    scored.sort(reverse=True)
    _ts, sid, body = scored[0]
    return SteamUser(
        steamid64=sid,
        persona_name=field(body, "PersonaName"),
        account_name=field(body, "AccountName"),
    )
