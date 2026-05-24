r"""Build a new save folder from selected (world, character) parts.

Golden Rule:
  - source paths are opened read-only and never mutated
  - the destination directory MUST NOT already exist
  - all writes go to fresh paths under Saves\<mode>\<name> (and, for MP, the
    paired _player folder + Server\<name>.* config files)
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .saves import Save, Character, SAVE_KIND_SP, SAVE_KIND_MP


# Files in an SP save that are part of the host's view-of-the-world and need
# to be carried in the _player folder when going to MP-host.
_HOST_CACHE_FILES = (
    "InGameMap.ini", "map_basements.bin", "map_symbols.bin",
    "recorded_media.bin", "thumb.png",
)
_HOST_CACHE_DIRS = ("apop", "chunkdata", "isoregiondata", "map", "metagrid", "zpop")


@dataclass
class CharacterSpec:
    """One character to be inserted into the target save.

    `character` carries blob/coords/name/worldversion and (for MP-sourced
    characters and .pzchar files) the original username/steamid. `source_save`
    is the Save the character came from, for compatibility diffing; it can
    be None for characters loaded from .pzchar files.
    """
    character: Character
    source_save: Save | None = None
    source_label: str = ""

    @property
    def has_mp_creds(self) -> bool:
        return bool(self.character.steamid)


@dataclass
class ExportPlan:
    target_kind: str            # SP / MP
    target_mode: str            # Apocalypse / Builder / Sandbox / Survivor / Multiplayer
    target_name: str            # folder/world name for the new save
    source_world: Save
    characters: list[CharacterSpec] = field(default_factory=list)
    # Fallback credentials applied to any character that lacks its own
    # (e.g. an SP-sourced character being inserted into an MP target with
    # no .pzchar-bundled credentials).
    default_steamid: str | None = None
    default_username: str | None = None
    target_map: str | None = None         # MP only — value for Server\<name>.ini Map=


def plan_destinations(plan: ExportPlan) -> dict[str, Path]:
    """Return absolute paths the export will create. Does not touch disk."""
    out: dict[str, Path] = {}
    if plan.target_kind == SAVE_KIND_SP:
        out["world"] = paths.saves_root() / plan.target_mode / plan.target_name
    else:
        mp = paths.saves_root() / paths.MP_MODE
        out["world"] = mp / plan.target_name
        out["player"] = mp / f"{plan.target_name}_player"
        out["server_ini"] = paths.server_config_dir() / f"{plan.target_name}.ini"
        out["server_sandbox"] = paths.server_config_dir() / f"{plan.target_name}_SandboxVars.lua"
        out["server_spawnpts"] = paths.server_config_dir() / f"{plan.target_name}_spawnpoints.lua"
        out["server_spawnreg"] = paths.server_config_dir() / f"{plan.target_name}_spawnregions.lua"
    return out


_WIN_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def validate_save_name(name: str) -> list[str]:
    """Return a list of human-readable problems with a proposed save name."""
    errs: list[str] = []
    if not name:
        errs.append("name must not be empty")
        return errs
    if name != name.strip():
        errs.append("name must not start or end with whitespace")
    if name.endswith(".") or name.endswith(" "):
        errs.append("name must not end with '.' or ' ' (Windows trims them)")
    bad = set('<>:"/\\|?*\0')
    found = sorted({ch for ch in name if ch in bad or ord(ch) < 32})
    if found:
        errs.append(f"name contains invalid characters: {''.join(found)!r}")
    stem = name.split(".", 1)[0].lower()
    if stem in _WIN_RESERVED:
        errs.append(f"name is a Windows reserved word: {stem!r}")
    if len(name) > 120:
        errs.append("name too long (>120 chars)")
    return errs


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def precheck(plan: ExportPlan) -> list[str]:
    """Return a list of blocking errors (empty list = good to go).

    Compatibility warnings are *not* errors — they're surfaced separately
    and the user always gets to proceed.
    """
    errors: list[str] = []
    errors.extend(validate_save_name(plan.target_name))

    dests = plan_destinations(plan)
    for label, p in dests.items():
        if p.exists():
            errors.append(f"target {label!s} already exists: {p}")

    # Defence-in-depth: ensure no destination dir overlaps a source save dir,
    # and no destination file equals a source file. (The Server\ folder is
    # shared by every MP save's config — only the specific source files are
    # off-limits, not the whole folder.)
    src_dirs: list[Path] = [plan.source_world.world_dir]
    if plan.source_world.player_dir:
        src_dirs.append(plan.source_world.player_dir)
    src_files: list[Path] = []
    src_name = plan.source_world.name
    if plan.source_world.server_ini:
        srv = plan.source_world.server_ini.parent
        src_files.extend([
            srv / f"{src_name}.ini",
            srv / f"{src_name}_SandboxVars.lua",
            srv / f"{src_name}_spawnpoints.lua",
            srv / f"{src_name}_spawnregions.lua",
        ])
    for label, p in dests.items():
        for s in src_dirs:
            if _is_inside(p, s) or p.resolve() == s.resolve():
                errors.append(
                    f"target {label!r} would be inside source save dir "
                    f"({p} ⊂ {s}) — refusing to write")
        for f in src_files:
            try:
                if f.exists() and p.resolve() == f.resolve():
                    errors.append(
                        f"target {label!r} equals a source file "
                        f"({p}) — refusing to overwrite")
            except OSError:
                pass

    # MP target: every character must end up with a steamid + username,
    # either its own or via the plan-level defaults.
    if plan.target_kind == SAVE_KIND_MP:
        for i, spec in enumerate(plan.characters, 1):
            c = spec.character
            sid = c.steamid or plan.default_steamid
            uname = c.username or plan.default_username
            if not sid:
                errors.append(
                    f"character #{i} ({c.name}): missing steamid for MP target")
            if not uname:
                errors.append(
                    f"character #{i} ({c.name}): missing username for MP target")
        # Detect duplicate steamids in this build — PZ keys players by
        # steamid+username, so two characters with the same Steam ID would
        # conflict when both players try to join.
        seen_sids: dict[str, str] = {}
        for i, spec in enumerate(plan.characters, 1):
            sid = str(spec.character.steamid or plan.default_steamid or "").strip()
            if not sid:
                continue
            if sid in seen_sids:
                errors.append(
                    f"two characters share steamid {sid}: "
                    f"'{seen_sids[sid]}' and '{spec.character.name}' — "
                    f"only one can be claimed by a given Steam account")
            else:
                seen_sids[sid] = spec.character.name

    # SP target: only one character makes sense (PZ SP is single-player).
    if plan.target_kind == SAVE_KIND_SP and len(plan.characters) > 1:
        errors.append(
            f"SP target accepts at most 1 character "
            f"(received {len(plan.characters)})")
    return errors


def count_source_files(plan: ExportPlan) -> int:
    """Estimate how many files copytree will write. Used for the progress bar."""
    src = plan.source_world
    total = sum(1 for _ in src.world_dir.rglob("*") if _.is_file())
    if src.player_dir and src.player_dir.is_dir():
        total += sum(1 for _ in src.player_dir.rglob("*") if _.is_file())
    return total


ProgressFn = "callable | None"   # progress(stage: str, copied: int, total: int)


def execute(plan: ExportPlan, *, progress: ProgressFn = None) -> dict[str, Path]:
    """Materialise the new save. Returns the dict of created paths.

    progress(stage, copied, total) is invoked many times during the run.
    stage in {"counting", "copying", "rewriting", "config"}.
    """
    errs = precheck(plan)
    if errs:
        raise RuntimeError("export precheck failed: " + "; ".join(errs))
    dests = plan_destinations(plan)

    dests["world"].parent.mkdir(parents=True, exist_ok=True)
    if plan.target_kind == SAVE_KIND_MP:
        dests["player"].parent.mkdir(parents=True, exist_ok=True)
        dests["server_ini"].parent.mkdir(parents=True, exist_ok=True)

    if progress:
        progress("counting", 0, 0)
    total = count_source_files(plan)
    state = {"copied": 0, "total": total}

    def file_cb(_src: str, _dst: str) -> str:
        shutil.copy2(_src, _dst)
        state["copied"] += 1
        if progress:
            progress("copying", state["copied"], state["total"])
        return _dst

    if plan.target_kind == SAVE_KIND_SP:
        _build_sp(plan, dests, file_cb=file_cb)
    else:
        _build_mp(plan, dests, file_cb=file_cb)

    if progress:
        progress("rewriting", state["copied"], state["total"])
    return dests


# ---------- SP build ----------

def _build_sp(plan: ExportPlan, dests: dict[str, Path], *, file_cb=None) -> None:
    src = plan.source_world
    dest_world = dests["world"]

    if src.kind == SAVE_KIND_SP:
        _copytree(src.world_dir, dest_world, file_cb=file_cb)
    else:
        # MP-host → SP: merge world dir + _player dir, _player wins on conflicts.
        _copytree(src.world_dir, dest_world, file_cb=file_cb)
        if src.player_dir and src.player_dir.is_dir():
            _copytree(src.player_dir, dest_world, overwrite=True, file_cb=file_cb)
        if src.mods:
            _write_sp_mods_txt(dest_world / "mods.txt", src.mods)

    _rewrite_players_db(dest_world / "players.db",
                        specs=plan.characters,
                        as_network=False, plan=plan)


# ---------- MP build ----------

def _build_mp(plan: ExportPlan, dests: dict[str, Path], *, file_cb=None) -> None:
    src = plan.source_world
    dest_world = dests["world"]
    dest_player = dests["player"]

    if src.kind == SAVE_KIND_MP:
        _copytree(src.world_dir, dest_world, file_cb=file_cb)
        if src.player_dir and src.player_dir.is_dir():
            _copytree(src.player_dir, dest_player, file_cb=file_cb)
        else:
            _seed_empty_player_dir(dest_player, dest_world, file_cb=file_cb)
        _copy_or_generate_server_config(src, plan, dests)
    else:
        _split_sp_to_mp(src, dest_world, dest_player, file_cb=file_cb)
        _copy_or_generate_server_config(src, plan, dests)

    # Strip stale serverid.dat — binds the client cache to a previous server.
    stale = dest_player / "serverid.dat"
    if stale.is_file():
        stale.unlink()

    _rewrite_players_db(dest_world / "players.db",
                        specs=plan.characters,
                        as_network=True, plan=plan)


def _split_sp_to_mp(src: Save, dest_world: Path, dest_player: Path,
                    *, file_cb=None) -> None:
    """Copy SP folder into a server-side <world>/ folder, then mirror the
    client-side bits into <world>_player/. Many files belong in both."""
    _copytree(src.world_dir, dest_world, file_cb=file_cb)
    dest_player.mkdir(parents=True, exist_ok=False)
    for fname in _HOST_CACHE_FILES:
        s = src.world_dir / fname
        if s.is_file():
            if file_cb:
                file_cb(str(s), str(dest_player / fname))
            else:
                shutil.copy2(s, dest_player / fname)
    for d in _HOST_CACHE_DIRS:
        s = src.world_dir / d
        if s.is_dir():
            _copytree(s, dest_player / d, file_cb=file_cb)
    # Drop SP-only artifacts from the server-side dir (they confuse the server).
    for stray in ("mods.txt", "map.bin", "map_sand.bin", "map_ver.bin",
                  "map_visited.bin", "InGameMap.ini", "map_symbols.bin",
                  "metadata.bin", "statistics.bin", "fishingData.bin",
                  "global_mod_data.bin"):
        p = dest_world / stray
        if p.is_file():
            p.unlink()


def _seed_empty_player_dir(dest_player: Path, dest_world: Path,
                           *, file_cb=None) -> None:
    """When source MP-host has no _player folder, seed it with copies of the
    minimum chunk/map dirs the host will need."""
    dest_player.mkdir(parents=True, exist_ok=False)
    for d in _HOST_CACHE_DIRS:
        s = dest_world / d
        if s.is_dir():
            _copytree(s, dest_player / d, file_cb=file_cb)


# ---------- server config ----------

def _copy_or_generate_server_config(src: Save, plan: ExportPlan,
                                    dests: dict[str, Path]) -> None:
    r"""Copy the source's Server\<name>.ini (and friends) to the new name,
    or synthesise a minimal one if there's no source ini."""
    if src.kind == SAVE_KIND_MP and src.server_ini and src.server_ini.is_file():
        _copy_renamed_ini(src.server_ini, dests["server_ini"], src.name,
                          plan.target_name)
        for src_path, dest_key in (
            (paths.server_config_dir() / f"{src.name}_SandboxVars.lua", "server_sandbox"),
            (paths.server_config_dir() / f"{src.name}_spawnpoints.lua", "server_spawnpts"),
            (paths.server_config_dir() / f"{src.name}_spawnregions.lua", "server_spawnreg"),
        ):
            if src_path.is_file() and dest_key in dests:
                shutil.copy2(src_path, dests[dest_key])
        return

    # SP → MP: synthesise a minimal server INI from the SP mod list.
    # PZ won't actually LOAD mods at MP launch unless their Steam workshop
    # IDs are in WorkshopItems=, even if they're in Mods=. The user's saves
    # don't store workshop IDs, but Steam keeps subscribed mods at
    # <SteamLibrary>/steamapps/workshop/content/108600/<workshop_id>/mods/<mod_id>/ —
    # so we resolve the mapping by scanning every Steam library on disk.
    from . import steam as steam_mod
    ws_map = steam_mod.find_workshop_mods()
    resolved_ids: list[str] = []
    for mod in src.mods:
        wid = ws_map.get(mod)
        if wid and wid not in resolved_ids:
            resolved_ids.append(wid)

    mod_csv = ";".join(src.mods)
    workshop_csv = ";".join(resolved_ids)
    map_value = plan.target_map or "Muldraugh, KY"
    ini_text = (
        "# Generated by PZSaveMixer\n"
        f"PublicName={plan.target_name}\n"
        f"Mods={mod_csv}\n"
        f"WorkshopItems={workshop_csv}\n"
        f"Map={map_value}\n"
    )
    dests["server_ini"].write_text(ini_text, encoding="utf-8")


def _copy_renamed_ini(src_ini: Path, dst_ini: Path, old_name: str, new_name: str) -> None:
    text = src_ini.read_text(encoding="utf-8", errors="replace")
    # Replace standalone PublicName= line if it matches old name.
    out = []
    for line in text.splitlines():
        if line.lower().startswith("publicname="):
            out.append(f"PublicName={new_name}")
        else:
            out.append(line)
    dst_ini.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------- players.db rewrite ----------

def _rewrite_players_db(db_path: Path, *, specs: list[CharacterSpec],
                        as_network: bool, plan: ExportPlan) -> None:
    """Wipe & rebuild players.db with the chosen character(s).

    db_path points to the freshly-copied players.db inside the *new* save
    folder. We DROP+CREATE both tables to guarantee the current-build schema
    even when the source came from an older PZ version.

    Safety: db_path must not equal the source's players.db (the precheck
    in execute() will already have refused if a destination collided with
    a source, but we double-check here).
    """
    src_world = plan.source_world.world_dir
    src_db = src_world / "players.db"
    if src_db.is_file() and db_path.resolve() == src_db.resolve():
        raise RuntimeError(
            f"refusing to rewrite source players.db (would violate Golden Rule): {src_db}")

    if not db_path.is_file():
        _create_empty_players_db(db_path)

    con = sqlite3.connect(db_path)
    try:
        # Drop & recreate so old-schema saves (Build 41) get current columns.
        con.execute("DROP TABLE IF EXISTS localPlayers")
        con.execute("DROP TABLE IF EXISTS networkPlayers")
        _ensure_player_tables(con)
        for idx, spec in enumerate(specs):
            c = spec.character
            if as_network:
                _insert_network(con, c, plan, fallback_player_index=idx)
            else:
                _insert_local(con, c, plan)
        con.commit()
    finally:
        con.close()


def _create_empty_players_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        _ensure_player_tables(con)
        con.commit()
    finally:
        con.close()


def _ensure_player_tables(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS localPlayers (
            id INTEGER PRIMARY KEY NOT NULL,
            name STRING, wx INTEGER, wy INTEGER,
            x FLOAT, y FLOAT, z FLOAT,
            worldversion INTEGER, data BLOB, isDead BOOLEAN
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS networkPlayers (
            id INTEGER PRIMARY KEY NOT NULL,
            world TEXT, username TEXT, playerIndex INTEGER,
            name STRING, steamid STRING,
            x FLOAT, y FLOAT, z FLOAT,
            worldversion INTEGER, data BLOB, isDead BOOLEAN
        )""")


def _insert_local(con: sqlite3.Connection, c: Character, plan: ExportPlan) -> None:
    """SP convention: PZ stores localPlayers.name wrapped in single-quote
    literals (the apostrophes are part of the column value, not SQL syntax)."""
    wx = c.wx if c.wx is not None else int(c.x // 300)
    wy = c.wy if c.wy is not None else int(c.y // 300)
    name = c.name if (c.name.startswith("'") and c.name.endswith("'")) \
        else f"'{c.name}'"
    con.execute(
        "INSERT INTO localPlayers (name,wx,wy,x,y,z,worldversion,data,isDead) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (name, wx, wy, c.x, c.y, c.z, c.worldversion, c.data_blob,
         int(c.is_dead)),
    )


def _insert_network(con: sqlite3.Connection, c: Character, plan: ExportPlan,
                    *, fallback_player_index: int = 0) -> None:
    """MP convention: PZ stores networkPlayers string columns AS-IS — no
    surrounding apostrophes. The earlier code wrapped values in quotes, which
    broke steamid/username matching when joining a hosted MP world (PZ would
    not find the row and prompt for a new character).

    Username and steamid come from the character itself (MP source or
    .pzchar with creds bundled) when available, falling back to the
    plan-level defaults the user typed in the mix flow."""
    # Defensively strip any apostrophe wrap (e.g. if c was synthesised from an
    # SP source where _strip_quotes happened to miss it).
    name = c.name
    if name.startswith("'") and name.endswith("'") and len(name) >= 2:
        name = name[1:-1]
    username = c.username or plan.default_username
    steamid = c.steamid or plan.default_steamid
    player_index = c.player_index if c.player_index is not None \
        else fallback_player_index
    con.execute(
        "INSERT INTO networkPlayers "
        "(world,username,playerIndex,name,steamid,x,y,z,worldversion,data,isDead) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (plan.target_name, username, player_index, name, steamid,
         c.x, c.y, c.z, c.worldversion, c.data_blob, int(c.is_dead)),
    )


# ---------- helpers ----------

def _copytree(src: Path, dst: Path, *, overwrite: bool = False,
              file_cb=None) -> None:
    if file_cb is None:
        shutil.copytree(src, dst, dirs_exist_ok=overwrite)
    else:
        shutil.copytree(src, dst, dirs_exist_ok=overwrite,
                        copy_function=file_cb)


def _write_sp_mods_txt(dest: Path, mods: list[str]) -> None:
    lines = ["VERSION = 1,", "", "mods", "{"]
    for m in mods:
        lines.append(f"    mod = {m},")
    lines.append("}")
    lines.append("")
    dest.write_text("\n".join(lines), encoding="utf-8")
