"""Backup & restore of existing PZ saves.

Backup format:
  ~/Zomboid/PZSaveMixer_Backups/<save-name>__<timestamp>.zip
  alongside a sidecar:
  ~/Zomboid/PZSaveMixer_Backups/<save-name>__<timestamp>.meta.json

Each zip stores the save under a top-level "world/" folder, plus (for MP)
"player/" and "server/" trees, so the layout is unambiguous on restore.

Restoration defaults to creating a NEW save (the Golden-Rule path).
Overwriting an existing save is supported but requires typed confirmation
from the calling layer.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import paths


# Switched from ZIP_DEFLATED to ZIP_STORED: PZ chunk files are already
# opaque binary that doesn't compress meaningfully, and skipping deflate
# makes backup + restore several times faster on SSDs.
_ZIP_MODE = zipfile.ZIP_STORED


def _extract_workers() -> int:
    """Number of threads to use for parallel zip extraction. Caps at 8 since
    above that the file-system mutex dominates on Windows."""
    return min(8, max(2, (os.cpu_count() or 4)))
from .saves import Save, SAVE_KIND_SP, SAVE_KIND_MP


BACKUP_DIR_NAME = "PZSaveMixer_Backups"


def backups_dir() -> Path:
    d = paths.zomboid_root() / BACKUP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class BackupRecord:
    zip_path: Path
    meta_path: Path
    save_name: str
    kind: str
    mode: str
    created: datetime
    size_bytes: int
    has_player_dir: bool
    has_server_config: bool
    note: str | None = None

    @property
    def stamp(self) -> str:
        return self.created.strftime("%Y-%m-%d %H:%M:%S")


# -------- backup --------

def backup(save: Save, note: str | None = None,
           progress=None) -> BackupRecord:
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = backups_dir()
    base = f"{_sanitise(save.name)}__{ts}"
    zip_path = out_dir / f"{base}.zip"
    meta_path = out_dir / f"{base}.meta.json"
    if zip_path.exists() or meta_path.exists():
        raise RuntimeError(f"backup target already exists: {zip_path}")

    meta = {
        "tool": "PZSaveMixer",
        "format_version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "save_name": save.name,
        "kind": save.kind,
        "mode": save.mode,
        "world_dir": str(save.world_dir),
        "player_dir": str(save.player_dir) if save.player_dir else None,
        "server_ini": str(save.server_ini) if save.server_ini else None,
        "worldversion": save.worldversion,
        "mods": save.mods,
        "map_value": save.map_value,
        "note": note,
    }

    with zipfile.ZipFile(zip_path, "w", _ZIP_MODE, allowZip64=True) as zf:
        _zip_tree(zf, save.world_dir, "world", progress)
        if save.player_dir and save.player_dir.is_dir():
            _zip_tree(zf, save.player_dir, "player", progress)
        if save.kind == SAVE_KIND_MP:
            # Bundle the four Server\<name>.* files too.
            for ext, arc in (
                (".ini", "server/server.ini"),
                ("_SandboxVars.lua", "server/SandboxVars.lua"),
                ("_spawnpoints.lua", "server/spawnpoints.lua"),
                ("_spawnregions.lua", "server/spawnregions.lua"),
            ):
                src = paths.server_config_dir() / f"{save.name}{ext}"
                if src.is_file():
                    zf.write(src, arc)
                    if progress:
                        progress(src.name)

    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    size = zip_path.stat().st_size
    return BackupRecord(
        zip_path=zip_path,
        meta_path=meta_path,
        save_name=save.name,
        kind=save.kind,
        mode=save.mode,
        created=datetime.now(),
        size_bytes=size,
        has_player_dir=bool(save.player_dir and save.player_dir.is_dir()),
        has_server_config=(save.server_ini is not None),
        note=note,
    )


def _zip_tree(zf: zipfile.ZipFile, root: Path, arc_prefix: str, progress) -> None:
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            arc = f"{arc_prefix}/{rel}"
            zf.write(p, arc)
            if progress:
                progress(rel)


def _sanitise(name: str) -> str:
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in name)


# -------- list backups --------

def list_backups() -> list[BackupRecord]:
    out: list[BackupRecord] = []
    d = backups_dir()
    for zp in sorted(d.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        mp = zp.with_suffix("").with_suffix(".meta.json")
        if not mp.is_file():
            mp = zp.with_name(zp.stem + ".meta.json")
        meta = {}
        if mp.is_file():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        created = meta.get("created")
        try:
            created_dt = datetime.fromisoformat(created) if created \
                else datetime.fromtimestamp(zp.stat().st_mtime)
        except ValueError:
            created_dt = datetime.fromtimestamp(zp.stat().st_mtime)
        out.append(BackupRecord(
            zip_path=zp,
            meta_path=mp,
            save_name=meta.get("save_name", zp.stem),
            kind=meta.get("kind", "?"),
            mode=meta.get("mode", "?"),
            created=created_dt,
            size_bytes=zp.stat().st_size,
            has_player_dir=bool(meta.get("player_dir")),
            has_server_config=bool(meta.get("server_ini")),
            note=meta.get("note"),
        ))
    return out


# -------- restore --------

def restore(record: BackupRecord, *, target_name: str,
            overwrite: bool = False) -> dict[str, Path]:
    """Restore a backup. Default behaviour: create a NEW save with target_name.

    With overwrite=True, an existing save of the same name is replaced — but
    only after auto-snapshotting it first to ~/Zomboid/PZSaveMixer_Backups/
    so the user can never lose data through this code path.

    Fast path (overwrite=False): extract directly to the destination since
    we've verified it's empty. If extraction fails midway, the partial
    output is wiped so we don't leave a half-baked save.

    Safe path (overwrite=True): extract to a staging dir first, then swap
    into place only after the full extraction succeeds.
    """
    meta = json.loads(record.meta_path.read_text(encoding="utf-8")) \
        if record.meta_path.is_file() else {}
    kind = meta.get("kind", record.kind)
    mode = meta.get("mode", record.mode)
    orig_name = meta.get("save_name", record.save_name)

    dests = _plan_restore_dests(kind=kind, mode=mode, name=target_name)

    if not overwrite:
        for label, p in dests.items():
            if p.exists():
                raise RuntimeError(f"target {label!s} already exists: {p}")
        # Direct extraction — destination paths are verified empty.
        try:
            _extract_direct(record, dests, kind=kind, mode=mode,
                            target_name=target_name, orig_name=orig_name)
        except BaseException:
            # Failure mid-extract → clean up the partial new save so the
            # user isn't left with a half-baked directory tree.
            for p in dests.values():
                if not p.exists():
                    continue
                try:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink()
                except OSError:
                    pass
            raise
        return dests

    # Overwrite path: auto-snapshot first, extract to staging, then swap.
    _autosnap_before_overwrite(target_name, kind, mode, dests)
    staging = _extract_to_staging(record, target_name, kind, mode, orig_name)
    try:
        _swap_into_place(staging, dests, overwrite=True)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return dests


def _extract_direct(record: BackupRecord, dests: dict[str, Path], *,
                    kind: str, mode: str, target_name: str,
                    orig_name: str) -> None:
    """Extract every zip member straight to its final destination path.
    Same N-thread parallelism as _extract_to_staging but with no swap step."""
    for p in dests.values():
        if p.suffix == "":
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)

    work: list[tuple[str, Path]] = []
    with zipfile.ZipFile(record.zip_path, "r") as zf:
        for info in zf.infolist():
            arc = info.filename.replace("\\", "/")
            if arc.endswith("/"):
                continue
            target = _route_arc(arc, dests, kind=kind, mode=mode,
                                name=target_name, orig_name=orig_name)
            if target is None:
                continue
            work.append((arc, target))
    for _, target in work:
        target.parent.mkdir(parents=True, exist_ok=True)

    n_workers = _extract_workers()
    batches = [work[i::n_workers] for i in range(n_workers)]

    def extract_batch(batch: list[tuple[str, Path]]) -> None:
        if not batch:
            return
        with zipfile.ZipFile(record.zip_path, "r") as zf:
            for arc, target in batch:
                with zf.open(arc) as fp, open(target, "wb") as out:
                    shutil.copyfileobj(fp, out)

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        list(ex.map(extract_batch, batches))


def _extract_to_staging(record: BackupRecord, target_name: str, kind: str,
                        mode: str, orig_name: str) -> Path:
    """Extract the zip into a fresh sibling-of-destination staging directory.

    Parallelised across N worker threads — each worker opens its own
    ZipFile handle because sharing one across threads is not safe (the
    underlying file position is mutated by .open() calls).

    Returns the staging root.
    """
    staging = backups_dir() / f".staging_{target_name}_{time.strftime('%H%M%S')}"
    if staging.exists():
        shutil.rmtree(staging)
    staging_dests = _plan_restore_dests_under(staging, kind=kind, mode=mode,
                                              name=target_name)
    for p in staging_dests.values():
        if p.suffix == "":
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)

    # First pass: list the work, ensure target directories exist.
    work: list[tuple[str, Path]] = []
    with zipfile.ZipFile(record.zip_path, "r") as zf:
        for info in zf.infolist():
            arc = info.filename.replace("\\", "/")
            if arc.endswith("/"):
                continue
            target = _route_arc(arc, staging_dests, kind=kind, mode=mode,
                                name=target_name, orig_name=orig_name)
            if target is None:
                continue
            work.append((arc, target))
    # Create directories serially — cheap and avoids racing mkdir calls.
    for _, target in work:
        target.parent.mkdir(parents=True, exist_ok=True)

    # Second pass: strip the work into N near-equal batches and extract
    # in parallel. Striping (work[i::N]) keeps batches roughly balanced
    # even when zip members are sorted by directory.
    n_workers = _extract_workers()
    batches: list[list[tuple[str, Path]]] = [work[i::n_workers]
                                             for i in range(n_workers)]

    def extract_batch(batch: list[tuple[str, Path]]) -> None:
        if not batch:
            return
        # One ZipFile per worker — opened once, reused for all files in
        # the batch. Far cheaper than reopening per file.
        with zipfile.ZipFile(record.zip_path, "r") as zf:
            for arc, target in batch:
                with zf.open(arc) as fp, open(target, "wb") as out:
                    shutil.copyfileobj(fp, out)

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        # Materialise the iterator so exceptions in any worker propagate.
        list(ex.map(extract_batch, batches))

    return staging


def _plan_restore_dests_under(root: Path, *, kind: str, mode: str,
                              name: str) -> dict[str, Path]:
    """Same as _plan_restore_dests but rooted under an arbitrary directory
    (for the staging area)."""
    out: dict[str, Path] = {}
    if kind == SAVE_KIND_SP:
        out["world"] = root / "Saves" / mode / name
    else:
        mp = root / "Saves" / paths.MP_MODE
        out["world"] = mp / name
        out["player"] = mp / f"{name}_player"
        srv = root / "Server"
        out["server_ini"] = srv / f"{name}.ini"
        out["server_sandbox"] = srv / f"{name}_SandboxVars.lua"
        out["server_spawnpts"] = srv / f"{name}_spawnpoints.lua"
        out["server_spawnreg"] = srv / f"{name}_spawnregions.lua"
    return out


def _swap_into_place(staging: Path, dests: dict[str, Path], *,
                     overwrite: bool) -> None:
    """Move the freshly extracted files from staging into their final
    locations. By this point existing files have already been snapshotted
    away (overwrite mode) or the destinations are confirmed free."""
    staging_dests = {label: _resolve_in_staging(staging, p)
                     for label, p in dests.items()}
    for label, src in staging_dests.items():
        dst = dests[label]
        if not src.exists():
            continue
        if overwrite and dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


def _resolve_in_staging(staging: Path, final: Path) -> Path:
    """Translate a final-destination path into its equivalent inside staging."""
    # All final paths live under either paths.saves_root() or paths.server_config_dir().
    try:
        rel = final.relative_to(paths.zomboid_root())
        return staging / rel
    except ValueError:
        return staging / final.name


def _autosnap_before_overwrite(target_name: str, kind: str, mode: str,
                               dests: dict[str, Path]) -> None:
    """If we're about to overwrite an existing save, make a safety backup
    of whatever's there now. Same code path the user would get from the
    Backup menu — into ~/Zomboid/PZSaveMixer_Backups/."""
    from .saves import Save
    # Reconstruct a minimal Save object from the existing on-disk state.
    if not any(p.exists() for p in dests.values()):
        return
    ts = time.strftime("%Y%m%d_%H%M%S")
    snap_name = f"{_sanitise(target_name)}__autosnap_{ts}.zip"
    snap_path = backups_dir() / snap_name
    meta = {
        "tool": "PZSaveMixer",
        "format_version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "save_name": target_name,
        "kind": kind, "mode": mode,
        "note": "auto-snapshot before overwrite-restore",
    }
    with zipfile.ZipFile(snap_path, "w", _ZIP_MODE, allowZip64=True) as zf:
        world = dests.get("world")
        if world and world.is_dir():
            _zip_tree(zf, world, "world", None)
        player = dests.get("player")
        if player and player.is_dir():
            _zip_tree(zf, player, "player", None)
        for key, arc in (
            ("server_ini", "server/server.ini"),
            ("server_sandbox", "server/SandboxVars.lua"),
            ("server_spawnpts", "server/spawnpoints.lua"),
            ("server_spawnreg", "server/spawnregions.lua"),
        ):
            p = dests.get(key)
            if p and p.is_file():
                zf.write(p, arc)
    snap_meta = snap_path.with_name(snap_path.stem + ".meta.json")
    snap_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _plan_restore_dests(*, kind: str, mode: str, name: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if kind == SAVE_KIND_SP:
        out["world"] = paths.saves_root() / mode / name
    else:
        mp = paths.saves_root() / paths.MP_MODE
        out["world"] = mp / name
        out["player"] = mp / f"{name}_player"
        out["server_ini"] = paths.server_config_dir() / f"{name}.ini"
        out["server_sandbox"] = paths.server_config_dir() / f"{name}_SandboxVars.lua"
        out["server_spawnpts"] = paths.server_config_dir() / f"{name}_spawnpoints.lua"
        out["server_spawnreg"] = paths.server_config_dir() / f"{name}_spawnregions.lua"
    return out


def _route_arc(arc: str, dests: dict[str, Path], *, kind: str, mode: str,
               name: str, orig_name: str) -> Path | None:
    if arc.startswith("world/"):
        return dests["world"] / arc[len("world/"):]
    if arc.startswith("player/"):
        return dests.get("player", dests["world"].parent / f"{name}_player") / arc[len("player/"):]
    if arc.startswith("server/"):
        leaf = arc[len("server/"):]
        if leaf == "server.ini":
            return dests.get("server_ini")
        if leaf == "SandboxVars.lua":
            return dests.get("server_sandbox")
        if leaf == "spawnpoints.lua":
            return dests.get("server_spawnpts")
        if leaf == "spawnregions.lua":
            return dests.get("server_spawnreg")
    return None


def delete_backup(record: BackupRecord) -> None:
    if record.zip_path.is_file():
        record.zip_path.unlink()
    if record.meta_path.is_file():
        record.meta_path.unlink()


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
