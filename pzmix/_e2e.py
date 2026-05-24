"""End-to-end smoke for the compose pipeline.

Uses a temp directory as PZ_HOME, copies one real SP save and one real MP
save into it, then exercises:
  - SP→SP world+character mix
  - SP→MP world+character mix (with synthesised server ini)
  - MP→SP merge (host's _player merged into world)
  - MP→MP rename
  - players.db rewrite (correct table + row)
  - backup → restore-as-new round trip

Run with:  python -m pzmix._e2e
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --- arrange a sandboxed PZ_HOME ---

REAL_ROOT = Path.home() / "Zomboid"
SAMPLE_SP = REAL_ROOT / "Saves" / "Sandbox" / "2026-05-05_23-02-38"
SAMPLE_MP_WORLD = REAL_ROOT / "Saves" / "Multiplayer" / "madland"
SAMPLE_MP_PLAYER = REAL_ROOT / "Saves" / "Multiplayer" / "madland_player"
SAMPLE_SERVER_INI = REAL_ROOT / "Server" / "madland.ini"

assert SAMPLE_SP.is_dir(), f"missing fixture: {SAMPLE_SP}"
assert SAMPLE_MP_WORLD.is_dir(), f"missing fixture: {SAMPLE_MP_WORLD}"

tmp_root = Path(tempfile.mkdtemp(prefix="pzmix_e2e_"))
print(f"tmp PZ_HOME = {tmp_root}")
os.environ["PZ_HOME"] = str(tmp_root)

# Lay out a copy of the fixtures.
(tmp_root / "Saves" / "Sandbox").mkdir(parents=True)
(tmp_root / "Saves" / "Multiplayer").mkdir(parents=True)
(tmp_root / "Server").mkdir(parents=True)

shutil.copytree(SAMPLE_SP, tmp_root / "Saves" / "Sandbox" / SAMPLE_SP.name)
shutil.copytree(SAMPLE_MP_WORLD, tmp_root / "Saves" / "Multiplayer" / "madland")
if SAMPLE_MP_PLAYER.is_dir():
    shutil.copytree(SAMPLE_MP_PLAYER,
                    tmp_root / "Saves" / "Multiplayer" / "madland_player")
if SAMPLE_SERVER_INI.is_file():
    shutil.copy2(SAMPLE_SERVER_INI, tmp_root / "Server" / "madland.ini")
    # carry the auxiliary lua files too
    for ext in ("_SandboxVars.lua", "_spawnpoints.lua", "_spawnregions.lua"):
        s = REAL_ROOT / "Server" / f"madland{ext}"
        if s.is_file():
            shutil.copy2(s, tmp_root / "Server" / f"madland{ext}")

# --- now import the tool ---
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pzmix import paths, saves, compose, backup as backup_mod
from pzmix.saves import SAVE_KIND_SP, SAVE_KIND_MP, Character


def find(name: str, all_saves):
    for s in all_saves:
        if s.name == name:
            return s
    raise KeyError(name)


def assert_(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def db_rows(db: Path, table: str):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return list(con.execute(f"SELECT * FROM {table}"))
    finally:
        con.close()


# ====== test 1: SP→SP ======
print("\n[1] SP world + SP char → new SP save")
all_saves = saves.discover_all()
print(f"    discovered: {[s.name for s in all_saves]}")
sp = find(SAMPLE_SP.name, all_saves)
mp = find("madland", all_saves)
sp_char = sp.characters[0]
mp_char_alive = [c for c in mp.characters if not c.is_dead][0]

plan = compose.ExportPlan(
    target_kind=SAVE_KIND_SP, target_mode="Sandbox",
    target_name="t1_sp2sp",
    source_world=sp,
    characters=[compose.CharacterSpec(character=sp_char, source_save=sp,
                                      source_label="sp_char")],
)
out = compose.execute(plan)
assert_(out["world"].is_dir(), "target world dir created")
assert_((out["world"] / "players.db").is_file(), "players.db present")
assert_((out["world"] / "mods.txt").is_file(), "mods.txt present (SP)")
rows = db_rows(out["world"] / "players.db", "localPlayers")
assert_(len(rows) == 1, f"exactly one localPlayers row (got {len(rows)})")
assert_(rows[0][1].strip("'") == sp_char.name, "char name preserved")
assert_(rows[0][8] == sp_char.data_blob, "blob bytes preserved exactly")

# ====== test 2: SP→MP ======
print("\n[2] SP world + SP char → new MP-host save")
plan = compose.ExportPlan(
    target_kind=SAVE_KIND_MP, target_mode="Multiplayer",
    target_name="t2_sp2mp",
    source_world=sp,
    characters=[compose.CharacterSpec(character=sp_char, source_save=sp,
                                      source_label="sp_char")],
    default_username="Tester", default_steamid="76561111111111111",
)
out = compose.execute(plan)
assert_(out["world"].is_dir(), "MP world dir created")
assert_(out["player"].is_dir(), "MP _player dir created")
assert_(out["server_ini"].is_file(), "Server\\<name>.ini synthesised")
assert_(not (out["world"] / "mods.txt").exists(), "SP mods.txt stripped from MP world")
rows = db_rows(out["world"] / "players.db", "networkPlayers")
assert_(len(rows) == 1, "exactly one networkPlayers row")
nrow = rows[0]
# columns: id, world, username, playerIndex, name, steamid, x, y, z, wv, data, isDead
assert_(nrow[1] == "t2_sp2mp",
        f"world stored PLAIN in MP, got {nrow[1]!r}")
assert_(nrow[2] == "Tester",
        f"username stored PLAIN in MP, got {nrow[2]!r}")
assert_(nrow[4] == "Mackenzie Whitten",
        f"name stored PLAIN in MP (no apostrophe wrap), got {nrow[4]!r}")
# SQLite "STRING" column has NUMERIC affinity → an all-digit value is
# coerced to INTEGER. That matches what PZ actually stores in working MP
# saves (madland.players.db: steamid is an int), so we compare as strings.
assert_(str(nrow[5]) == "76561111111111111",
        f"steamid stored PLAIN in MP, got {nrow[5]!r}")
assert_(nrow[10] == sp_char.data_blob, "MP blob preserved")
local_rows = db_rows(out["world"] / "players.db", "localPlayers")
assert_(len(local_rows) == 0, "localPlayers empty in MP build")

# Workshop ID resolution: scan the local Steam workshop and verify the
# synthesised INI carries WorkshopItems= when any source mod was found.
from pzmix import steam as _steam
ws_map = _steam.find_workshop_mods()
ini_text = out["server_ini"].read_text(encoding="utf-8", errors="replace")
import re as _re
ws_line = _re.search(r"^WorkshopItems=(.*)$", ini_text, _re.MULTILINE)
assert_(ws_line is not None, "WorkshopItems= line present in synthesised INI")
written_ids = [s for s in (ws_line.group(1) if ws_line else "").split(";") if s]
expected_ids = []
for m in sp.mods:
    if m in ws_map and ws_map[m] not in expected_ids:
        expected_ids.append(ws_map[m])
assert_(written_ids == expected_ids,
        f"WorkshopItems= matches resolved workshop IDs "
        f"({len(written_ids)} of {len(sp.mods)} source mods resolved)")

# ====== test 3: MP→SP merge ======
print("\n[3] MP world + MP char → new SP save")
plan = compose.ExportPlan(
    target_kind=SAVE_KIND_SP, target_mode="Sandbox",
    target_name="t3_mp2sp",
    source_world=mp,
    characters=[compose.CharacterSpec(character=mp_char_alive, source_save=mp,
                                      source_label="mp_char_alive")],
)
out = compose.execute(plan)
assert_(out["world"].is_dir(), "merged world dir created")
assert_((out["world"] / "mods.txt").is_file(), "mods.txt synthesised from MP mod list")
rows = db_rows(out["world"] / "players.db", "localPlayers")
assert_(len(rows) == 1, "MP char appears in localPlayers in SP build")
assert_(rows[0][8] == mp_char_alive.data_blob, "MP blob carried into SP build")

# ====== test 4: MP→MP rename ======
print("\n[4] MP world + MP char → new MP save (rename)")
plan = compose.ExportPlan(
    target_kind=SAVE_KIND_MP, target_mode="Multiplayer",
    target_name="t4_mp2mp",
    source_world=mp,
    characters=[compose.CharacterSpec(character=mp_char_alive, source_save=mp,
                                      source_label="mp_char_alive")],
    default_username=mp_char_alive.username or "Renamed",
    default_steamid=mp_char_alive.steamid or "0",
)
out = compose.execute(plan)
assert_(out["world"].is_dir(), "renamed MP world dir created")
assert_(out["player"].is_dir(), "renamed MP _player dir created")
assert_(out["server_ini"].is_file(), "Server\\<name>.ini copied/renamed")
ini_text = out["server_ini"].read_text(encoding="utf-8", errors="replace")
assert_("PublicName=t4_mp2mp" in ini_text, "PublicName updated in copied ini")
rows = db_rows(out["world"] / "players.db", "networkPlayers")
assert_(len(rows) == 1, "char carried through")
assert_(rows[0][1] == "t4_mp2mp",
        f"world column updated PLAIN, got {rows[0][1]!r}")

# ====== test 5: precheck refuses overwrite ======
print("\n[5] precheck refuses to clobber an existing target")
plan = compose.ExportPlan(
    target_kind=SAVE_KIND_SP, target_mode="Sandbox",
    target_name="t1_sp2sp",  # already created in [1]
    source_world=sp,
    characters=[compose.CharacterSpec(character=sp_char, source_save=sp)],
)
errs = compose.precheck(plan)
assert_(any("already exists" in e for e in errs),
        "precheck flags existing destination")
try:
    compose.execute(plan)
    raise AssertionError("execute() should have refused")
except RuntimeError as e:
    assert_("precheck failed" in str(e), "execute() raises on collision")

# ====== test 6: backup → restore-as-new ======
print("\n[6] backup + restore-as-new round trip")
rec = backup_mod.backup(sp, note="e2e")
assert_(rec.zip_path.is_file(), "backup zip written")
assert_(rec.meta_path.is_file(), "backup meta sidecar written")
listed = backup_mod.list_backups()
assert_(any(r.zip_path == rec.zip_path for r in listed),
        "backup appears in list")
out = backup_mod.restore(rec, target_name="t6_restored", overwrite=False)
assert_(out["world"].is_dir(), "restored world dir present")
assert_((out["world"] / "players.db").is_file(), "restored players.db present")

# Round-trip: restored players.db has same row count as original.
orig_rows = db_rows(SAMPLE_SP / "players.db", "localPlayers")
restored_rows = db_rows(out["world"] / "players.db", "localPlayers")
assert_(len(orig_rows) == len(restored_rows),
        "restored player count matches source")

# ====== test 7: target name validation ======
print("\n[7] target name validation")
for bad in (" leading", "trailing ", "trailing.", "with*star", "CON", "nul", "x"*200):
    plan = compose.ExportPlan(
        target_kind=SAVE_KIND_SP, target_mode="Sandbox", target_name=bad,
        source_world=sp,
        characters=[compose.CharacterSpec(character=sp_char, source_save=sp)],
    )
    errs = compose.precheck(plan)
    assert_(bool(errs), f"name {bad!r} rejected by precheck")

# ====== test 8: serverid.dat is stripped from new _player ======
print("\n[8] serverid.dat is stripped from MP _player on export")
# Plant a fake serverid.dat into our copy of the source MP _player.
src_player = tmp_root / "Saves" / "Multiplayer" / "madland_player"
(src_player / "serverid.dat").write_bytes(b"\x00\x01\x02")
saves_now = saves.discover_all()
mp_now = find("madland", saves_now)
plan = compose.ExportPlan(
    target_kind=SAVE_KIND_MP, target_mode="Multiplayer",
    target_name="t8_strip_sid",
    source_world=mp_now,
    characters=[compose.CharacterSpec(character=mp_char_alive,
                                      source_save=mp_now)],
    default_username="X", default_steamid="0",
)
out = compose.execute(plan)
assert_(not (out["player"] / "serverid.dat").exists(),
        "serverid.dat removed from new _player")

# ====== test 9: overwrite restore auto-snapshots existing save ======
print("\n[9] overwrite restore auto-snapshots the existing save first")
# Take a backup of the SP save first.
rec2 = backup_mod.backup(sp, note="for overwrite test")
# Restore it as 'overwrite_target' (creates the save fresh).
backup_mod.restore(rec2, target_name="overwrite_target", overwrite=False)
target_world = paths.saves_root() / "Sandbox" / "overwrite_target"
# Plant a marker file inside the existing save we're about to overwrite.
marker = target_world / "DO_NOT_LOSE.txt"
marker.write_text("important data the user did not expect to lose")
backups_before = {p.name for p in backup_mod.backups_dir().glob("*.zip")}
backup_mod.restore(rec2, target_name="overwrite_target", overwrite=True)
backups_after = {p.name for p in backup_mod.backups_dir().glob("*.zip")}
new_snaps = backups_after - backups_before
assert_(any("autosnap" in n for n in new_snaps),
        "auto-snapshot zip created before overwrite")
# Confirm the marker can be recovered from the autosnap.
snap = next(backup_mod.backups_dir().glob("*autosnap*.zip"))
import zipfile as _zf
with _zf.ZipFile(snap) as zfh:
    names = zfh.namelist()
assert_(any(n.endswith("DO_NOT_LOSE.txt") for n in names),
        "user's pre-overwrite data preserved in auto-snapshot")

# ====== test 10: precheck refuses to write a destination equal to a source path ======
print("\n[10] precheck refuses if destination would equal a source file/dir")
# Try to export with a name that collides with the source world name in same mode.
plan = compose.ExportPlan(
    target_kind=SAVE_KIND_SP, target_mode="Sandbox",
    target_name=SAMPLE_SP.name,   # exactly the source save name
    source_world=sp,
    characters=[compose.CharacterSpec(character=sp_char, source_save=sp)],
)
errs = compose.precheck(plan)
assert_(any("already exists" in e or "inside source" in e or "equals a source" in e
            for e in errs),
        "self-collision rejected")

# ====== test 11: .pzchar round-trip ======
print("\n[11] .pzchar export + reload round trip")
from pzmix import pzchar
char_path = tmp_root / "Characters" / "Mackenzie.pzchar"
char_path.parent.mkdir(parents=True, exist_ok=True)
pzchar.export_character(
    sp_char, source_save_name=sp.name, source_kind=sp.kind,
    source_mods=sp.mods, source_map=sp.map_value, output_path=char_path,
    note="e2e",
)
assert_(char_path.is_file(), ".pzchar written")
loaded = pzchar.load_pzchar(char_path)
assert_(loaded.name == sp_char.name, "name round-trips")
assert_(loaded.worldversion == sp_char.worldversion, "worldversion round-trips")
assert_(loaded.blob_size == len(sp_char.data_blob), "blob_size matches")
assert_(loaded.load_blob() == sp_char.data_blob, "blob bytes byte-identical")
assert_(loaded.source_kind == "SP", "source_kind preserved")
# An SP source has no MP creds; with no local_steam_user passed, manifest is SP-only.
assert_(loaded.mp_steamid is None,
        f"SP export without steam fallback has no MP creds, got {loaded.mp_steamid!r}")

# ====== test 12: multi-character MP build with per-character creds ======
print("\n[12] multi-character MP build (group of 3, mixed sources)")
# Synthesize a second MP character with a DIFFERENT steamid so we exercise
# the per-character credential path without colliding with the duplicate-
# steamid guard.
import copy as _copy
alive2_data = _copy.copy(mp_char_alive)
alive2_data.name = "Second Player"
alive2_data.steamid = "76561111000000002"
alive2_data.username = "SecondAccount"
specs = [
    compose.CharacterSpec(character=mp_char_alive, source_save=mp,
                          source_label="alive1"),
    compose.CharacterSpec(character=alive2_data, source_save=mp,
                          source_label="alive2 (synthesized)"),
    # Third is an SP-sourced character that needs default creds.
    compose.CharacterSpec(character=sp_char, source_save=sp,
                          source_label="sp_visitor"),
]
plan = compose.ExportPlan(
    target_kind=SAVE_KIND_MP, target_mode="Multiplayer",
    target_name="t12_group",
    source_world=mp,
    characters=specs,
    default_username="VisitorAccount",
    default_steamid="76561222222222222",
)
out = compose.execute(plan)
rows = db_rows(out["world"] / "players.db", "networkPlayers")
# steamids in MP rows: alive1 + alive2 (own creds) + sp_visitor (default).
sids = [str(r[5]) for r in rows]
unique_sids = set(sids)
assert_(len(rows) == 3,
        f"three networkPlayers rows inserted, got {len(rows)}")
assert_(str(mp_char_alive.steamid) in unique_sids,
        f"alive1's original steamid carried through, got {sids!r}")
assert_("76561222222222222" in unique_sids,
        f"SP visitor used the default steamid, got {sids!r}")

# ====== test 13: SP target rejects multiple characters ======
print("\n[13] SP target rejects multiple characters")
plan = compose.ExportPlan(
    target_kind=SAVE_KIND_SP, target_mode="Sandbox",
    target_name="t13_multi_sp",
    source_world=sp,
    characters=[
        compose.CharacterSpec(character=sp_char, source_save=sp),
        compose.CharacterSpec(character=mp_char_alive, source_save=mp),
    ],
)
errs = compose.precheck(plan)
assert_(any("at most 1 character" in e for e in errs),
        f"SP multi-char rejected, errs={errs}")

# ====== test 14: duplicate steamid in MP build is rejected ======
print("\n[14] duplicate steamid in one MP build is refused")
plan = compose.ExportPlan(
    target_kind=SAVE_KIND_MP, target_mode="Multiplayer",
    target_name="t14_dup_steam",
    source_world=mp,
    characters=[
        compose.CharacterSpec(character=mp_char_alive, source_save=mp),
        # Reuse the same steamid for the second character.
        compose.CharacterSpec(
            character=Character(**{**mp_char_alive.__dict__,
                                   "name": "Imposter",
                                   "row_id": 999}),
            source_save=mp),
    ],
)
errs = compose.precheck(plan)
assert_(any("share steamid" in e for e in errs),
        f"duplicate steamid rejected, errs={errs}")

print("\nALL E2E CHECKS PASSED")
print(f"(temp dir kept for inspection: {tmp_root})")
