"""Entry point — numbered-menu loop."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# Allow running as either `python -m pzmix.main` or `python pzmix\main.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pzmix import paths, saves as save_mod, compat, compose, backup, ui, steam
from pzmix.ui import (
    MenuItem, menu, header, hr, pause, clear, prompt_text, confirm,
    typed_confirm, tag_for_kind, fmt_wv,
    SENTINEL_BACK, SENTINEL_QUIT, SENTINEL_REFRESH,
    HEAD, ACCENT, OK, WARN, BAD, MUTED, RESET, DIM, BOLD,
    FG_CYAN, FG_GREEN, FG_YELLOW, FG_RED,
)
from pzmix.saves import Save, Character, SAVE_KIND_SP, SAVE_KIND_MP


_DEST_LABEL = {
    "world":            "World",
    "player":           "Player",
    "server_ini":       "Server INI",
    "server_sandbox":   "Sandbox vars",
    "server_spawnpts":  "Spawn points",
    "server_spawnreg":  "Spawn regions",
}


def _fmt_dest_label(key: str) -> str:
    return f"{_DEST_LABEL.get(key, key):<14}"


# ---------- state ----------

class State:
    def __init__(self) -> None:
        self.saves: list[Save] = []
        self.status: str | None = None

    def refresh(self) -> None:
        self.status = f"{MUTED}scanning {paths.zomboid_root()}…{RESET}"
        try:
            self.saves = save_mod.discover_all()
            self.status = f"{OK}found {len(self.saves)} save(s){RESET}"
        except Exception as e:
            self.saves = []
            self.status = f"{BAD}error: {e}{RESET}"

    def status_once(self) -> str | None:
        s, self.status = self.status, None
        return s


STATE = State()


# ---------- helpers ----------

def _save_item(s: Save) -> MenuItem:
    tag = "MP" if s.kind == SAVE_KIND_MP else "SP"
    last = s.last_played.strftime("%Y-%m-%d %H:%M") if s.last_played else "?"
    extra = f"  {WARN}[client-cache]{RESET}" if s.is_mp_client_cache else ""
    hint = (f"{s.mode:<11} {last}  {fmt_wv(s.worldversion)}  "
            f"chars={len(s.characters)} mods={len(s.mods)}{extra}")
    return MenuItem(label=s.name, hint=hint, value=s, tag=tag)


def _char_item(c: Character, s: Save) -> MenuItem:
    extra = f" {DIM}user={c.username}{RESET}" if c.username else ""
    dead = f" {BAD}[DEAD]{RESET}" if c.is_dead else ""
    hint = (f"from {s.name} ({s.mode}) {c.short_coords} {fmt_wv(c.worldversion)}"
            f" blob={len(c.data_blob)}B{extra}{dead}")
    tag = "MP" if c.is_network else "SP"
    return MenuItem(label=c.name, hint=hint, value=(c, s), tag=tag)


# ---------- main menu ----------

def main_menu() -> None:
    if not STATE.saves:
        STATE.refresh()
    while True:
        items = [
            MenuItem("Browse saves",
                     hint="inspect everything discovered under Zomboid/Saves",
                     value="browse"),
            MenuItem("Mix and export",
                     hint="pick a world + a character → new save",
                     value="mix"),
            MenuItem("Backup & restore",
                     hint="archive existing saves; restore as new (or overwrite)",
                     value="backup"),
            MenuItem("Settings & info",
                     hint=f"Zomboid root: {paths.zomboid_root()}",
                     value="settings"),
        ]
        ans = menu(["Main"], items,
                   status=STATE.status_once(),
                   allow_back=False, allow_refresh=True)
        if ans is SENTINEL_QUIT:
            return
        if ans is SENTINEL_REFRESH:
            STATE.refresh(); continue
        if ans == "browse":
            browse_menu()
        elif ans == "mix":
            mix_flow()
        elif ans == "backup":
            backup_menu()
        elif ans == "settings":
            settings_menu()


# ---------- browse ----------

def browse_menu() -> None:
    while True:
        items = [_save_item(s) for s in STATE.saves]
        ans = menu(["Main", "Browse saves"], items,
                   status=STATE.status_once(),
                   allow_refresh=True,
                   empty_msg="no saves found")
        if ans in (SENTINEL_BACK, SENTINEL_QUIT):
            return
        if ans is SENTINEL_REFRESH:
            STATE.refresh(); continue
        if isinstance(ans, Save):
            save_detail(ans)


def save_detail(s: Save) -> None:
    while True:
        items: list[MenuItem] = []
        for c in s.characters:
            items.append(_char_item(c, s))
        if not items:
            items.append(MenuItem("(no characters in this save)",
                                  disabled=True, value=None))
        extras = [("p", "print details", "details"),
                  ("k", "backup this save", "backup")]
        ans = menu(["Main", "Browse saves", s.name], items,
                   status=STATE.status_once(),
                   allow_refresh=False,
                   extra=extras)
        if ans in (SENTINEL_BACK, SENTINEL_QUIT):
            return
        if ans == "details":
            print_save_details(s); pause(); continue
        if ans == "backup":
            do_backup(s); continue
        if isinstance(ans, tuple) and isinstance(ans[0], Character):
            print_character(ans[0], s); pause(); continue


def print_save_details(s: Save) -> None:
    print()
    print(f"  {HEAD}{s.name}{RESET}  [{s.kind}/{s.mode}]")
    print(f"  {MUTED}world_dir : {RESET}{s.world_dir}")
    if s.player_dir:
        print(f"  {MUTED}player_dir: {RESET}{s.player_dir}")
    if s.server_ini:
        print(f"  {MUTED}server_ini: {RESET}{s.server_ini}")
    print(f"  {MUTED}worldver  : {RESET}{s.worldversion}")
    print(f"  {MUTED}map       : {RESET}{s.map_value}")
    print(f"  {MUTED}last      : {RESET}{s.last_played}")
    print(f"  {MUTED}mods({len(s.mods)}):{RESET}")
    for m in s.mods:
        print(f"      - {m}")


def print_character(c: Character, s: Save) -> None:
    print()
    tag = "MP" if c.is_network else "SP"
    print(f"  {HEAD}{c.name}{RESET}  [{tag}] from {s.name}")
    print(f"  {MUTED}coords    : {RESET}{c.short_coords} z={c.z}")
    print(f"  {MUTED}wxwy      : {RESET}{c.wx},{c.wy}")
    print(f"  {MUTED}worldver  : {RESET}{c.worldversion}")
    print(f"  {MUTED}is_dead   : {RESET}{c.is_dead}")
    print(f"  {MUTED}data blob : {RESET}{len(c.data_blob)} bytes")
    if c.username: print(f"  {MUTED}username  : {RESET}{c.username}")
    if c.steamid:  print(f"  {MUTED}steamid   : {RESET}{c.steamid}")
    if c.world:    print(f"  {MUTED}world     : {RESET}{c.world}")


# ---------- mix flow ----------

def mix_flow() -> None:
    """Pick world → pick character → pick target mode → name → confirm → export."""
    # 1. World
    world_items = [_save_item(s) for s in STATE.saves]
    world = menu(["Main", "Mix", "1/5  pick world"], world_items,
                 prompt="world",
                 empty_msg="no saves found — refresh main menu first")
    if not isinstance(world, Save):
        return

    # 2. Character (from any save)
    char_items: list[MenuItem] = []
    for s in STATE.saves:
        for c in s.characters:
            char_items.append(_char_item(c, s))
    char_items.append(MenuItem(
        f"{MUTED}— empty world (no character) —{RESET}",
        value=("NONE", None), hint="export the world by itself"))
    pick = menu(["Main", "Mix", "2/5  pick character"], char_items,
                prompt="character",
                empty_msg="no characters across any saves")
    if pick is SENTINEL_BACK or pick is SENTINEL_QUIT:
        return
    if pick == ("NONE", None):
        chosen_char = None
        char_source = None
    elif isinstance(pick, tuple):
        chosen_char, char_source = pick
    else:
        return

    # 3. Target mode
    mode_items = [
        MenuItem("SP — Sandbox",     value=("SP", "Sandbox")),
        MenuItem("SP — Apocalypse",  value=("SP", "Apocalypse")),
        MenuItem("SP — Survivor",    value=("SP", "Survivor")),
        MenuItem("SP — Builder",     value=("SP", "Builder")),
        MenuItem("MP — Hosted (self-host)", value=("MP", "Multiplayer"),
                 hint="creates Saves/Multiplayer/<name> + _player + Server/<name>.ini"),
    ]
    mode_pick = menu(["Main", "Mix", "3/5  target mode"], mode_items,
                     prompt="mode")
    if not isinstance(mode_pick, tuple):
        return
    target_kind, target_mode = mode_pick

    # 4. Name + steamid (MP)
    suggested = f"{world.name}_mix"
    name = prompt_text("new save name", default=suggested)
    if not name:
        return
    plan = compose.ExportPlan(
        target_kind=target_kind, target_mode=target_mode, target_name=name,
        source_world=world, source_character=chosen_char,
        character_source_save=char_source,
    )
    if target_kind == SAVE_KIND_MP:
        # Detect the currently-logged-in Steam account on this machine so
        # the defaults are correct out of the box.
        local_user = steam.find_current_user()

        # Username default: prefer the source character's existing one (so
        # MP→MP rename preserves it), otherwise fall back to the local
        # Steam AccountName, otherwise a placeholder.
        if chosen_char and chosen_char.username:
            default_user = chosen_char.username
        elif local_user and local_user.account_name:
            default_user = local_user.account_name
        else:
            default_user = "Player1"

        # Steamid default: same priority — existing > local Steam > "0".
        if chosen_char and chosen_char.steamid:
            default_sid = chosen_char.steamid
        elif local_user:
            default_sid = local_user.steamid64
        else:
            default_sid = "0"

        if local_user:
            print(f"  {MUTED}detected local Steam user: "
                  f"{local_user.persona_name or local_user.account_name} "
                  f"({local_user.steamid64}){RESET}")

        plan.target_username = prompt_text("MP username for this character",
                                           default=default_user)
        plan.target_steamid = prompt_text(
            "steamid (numeric, no quotes — use 0 for non-Steam)",
            default=default_sid)
        # Map= for MP source: copy from the existing server INI.
        # For SP source: the base PZ map "Muldraugh, KY" is correct unless
        # the user has a custom map mod installed (rare). We don't prompt —
        # the confirm screen tells them how to override if needed.
        plan.target_map = world.map_value or "Muldraugh, KY"

    confirm_export(plan)


def confirm_export(plan: compose.ExportPlan) -> None:
    while True:
        clear()
        header(["Main", "Mix", "5/5  confirm"])
        print()
        print(f"  {HEAD}destination{RESET}")
        dests = compose.plan_destinations(plan)
        for label, p in dests.items():
            mark = f"{BAD}EXISTS{RESET}" if p.exists() else f"{OK}new{RESET}"
            print(f"    {_fmt_dest_label(label)} [{mark}] {p}")
        print()
        print(f"  {HEAD}contents{RESET}")
        print(f"    world  : {plan.source_world.name} ({plan.source_world.mode})")
        if plan.source_world.is_mp_client_cache:
            print(f"             {WARN}! client cache only — no server config; "
                  f"world may be incomplete{RESET}")
        if plan.source_character:
            c = plan.source_character
            print(f"    char   : {c.name} {c.short_coords} {fmt_wv(c.worldversion)}"
                  f"{(' (DEAD)' if c.is_dead else '')}")
        else:
            print(f"    char   : {MUTED}(none — world only){RESET}")
        if plan.target_kind == SAVE_KIND_MP:
            print(f"    MP     : username={plan.target_username} "
                  f"steamid={plan.target_steamid}")
            map_src = (f"copied from source INI" if plan.source_world.kind == SAVE_KIND_MP
                       else f"default — edit Server\\{plan.target_name}.ini if "
                            f"you have a map mod")
            print(f"    Map=   : {plan.target_map}  {MUTED}({map_src}){RESET}")
        # Mod list inherited from the source world.
        n_mods = len(plan.source_world.mods)
        if n_mods:
            print(f"    mods   : {n_mods} (inherited from source world)")
            if (plan.target_kind == SAVE_KIND_MP
                    and plan.source_world.kind == SAVE_KIND_SP):
                # Three-bucket resolution: workshop (will populate
                # WorkshopItems=), local (already on disk in ~/Zomboid/mods),
                # and missing (not present anywhere → will fail to load).
                ws_map = steam.find_workshop_mods()
                local_set = steam.find_local_mods()
                workshop_hits = [m for m in plan.source_world.mods if m in ws_map]
                local_hits = [m for m in plan.source_world.mods
                              if m not in ws_map and m in local_set]
                missing = [m for m in plan.source_world.mods
                           if m not in ws_map and m not in local_set]

                print(f"             {OK}✓ {len(workshop_hits)} via Steam "
                      f"workshop  →  WorkshopItems= populated{RESET}")
                if local_hits:
                    print(f"             {OK}✓ {len(local_hits)} via local "
                          f"~/Zomboid/mods/  →  carried in Mods= only{RESET}")
                if missing:
                    print(f"             {BAD}× {len(missing)} not present "
                          f"anywhere — won't load, may crash MP:{RESET}")
                    for m in missing[:8]:
                        print(f"               - {m}")
                    if len(missing) > 8:
                        print(f"               {MUTED}…and "
                              f"{len(missing)-8} more{RESET}")
                    print(f"             {MUTED}re-subscribe these on Steam "
                          f"Workshop, or remove from Server\\{plan.target_name}.ini "
                          f"Mods= after export.{RESET}")
        else:
            print(f"    mods   : {MUTED}(none){RESET}")
        print()

        if plan.source_character and plan.character_source_save:
            rep = compat.compare(plan.source_character,
                                 plan.character_source_save,
                                 plan.source_world)
            _print_compat(rep)

        errs = compose.precheck(plan)
        if errs:
            print(f"\n  {BAD}blocked:{RESET}")
            for e in errs:
                print(f"    - {e}")
            print(hr())
            print(f"  {ACCENT}n{RESET} {MUTED}rename and retry{RESET}    "
                  f"{ACCENT}b{RESET} {MUTED}back{RESET}    "
                  f"{ACCENT}q{RESET} {MUTED}quit{RESET}")
            try:
                raw = input("  » ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return
            if raw == "q":
                sys.exit(0)
            if raw == "n":
                new = prompt_text("new target name", default=plan.target_name)
                if new:
                    plan.target_name = new
                continue
            return

        print(hr())
        print(f"  {ACCENT}y{RESET} {MUTED}export{RESET}    "
              f"{ACCENT}n{RESET} {MUTED}rename target{RESET}    "
              f"{ACCENT}b{RESET} {MUTED}back{RESET}")
        try:
            raw = input("  » ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if raw == "b":
            return
        if raw == "n":
            new = prompt_text("new target name", default=plan.target_name)
            if new:
                plan.target_name = new
            continue
        if raw in ("y", "yes"):
            _do_export(plan); return


def _print_compat(rep: compat.CompatReport) -> None:
    sev = rep.severity
    sev_col = {"ok": OK, "warn": WARN, "danger": BAD}[sev]
    print(f"  {HEAD}compatibility{RESET}  [{sev_col}{sev}{RESET}]")
    if rep.worldversion_match:
        print(f"    {OK}✓ worldversion matches{RESET} "
              f"(char={rep.worldversion_char} world={rep.worldversion_world})")
    else:
        print(f"    {BAD}× worldversion mismatch{RESET} "
              f"(char={rep.worldversion_char} world={rep.worldversion_world}) "
              f"{MUTED}— character blob may fail to load{RESET}")
    if rep.cross_kind:
        print(f"    {WARN}! cross-kind move (SP↔MP){RESET}")
    if rep.mods_missing_in_target:
        print(f"    {WARN}! mods on character's source but NOT in target world "
              f"({len(rep.mods_missing_in_target)}){RESET}:")
        for m in rep.mods_missing_in_target[:10]:
            print(f"        - {m}")
        if len(rep.mods_missing_in_target) > 10:
            print(f"        {MUTED}…and {len(rep.mods_missing_in_target)-10} more{RESET}")
    if rep.mods_extra_in_target:
        print(f"    {MUTED}+ mods in target NOT in source "
              f"({len(rep.mods_extra_in_target)}){RESET}")
    if rep.map_target and rep.map_char_source and not rep.map_matches:
        print(f"    {WARN}! map mismatch{RESET} "
              f"src={rep.map_char_source} target={rep.map_target}")


def _do_export(plan: compose.ExportPlan) -> None:
    import time as _time
    state = {"last": 0.0, "stage": "", "n": 0, "total": 0}

    def progress(stage: str, copied: int, total: int) -> None:
        now = _time.monotonic()
        state["stage"], state["n"], state["total"] = stage, copied, total
        if stage in ("counting", "rewriting") or now - state["last"] >= 0.1:
            state["last"] = now
            _render_progress(stage, copied, total)

    print()
    try:
        dests = compose.execute(plan, progress=progress)
    except Exception as e:
        sys.stdout.write("\r" + " " * 78 + "\r")
        print(f"  {BAD}export failed: {e}{RESET}")
        traceback.print_exc()
        pause(); return
    sys.stdout.write("\r" + " " * 78 + "\r")
    print(f"  {OK}✓ exported {state['n']} files{RESET}")
    for label, p in dests.items():
        print(f"    {_fmt_dest_label(label)} {p}")
    STATE.refresh()
    pause()


def _render_progress(stage: str, copied: int, total: int) -> None:
    bar_width = 30
    label_map = {
        "counting":  "counting files…",
        "copying":   "copying world data",
        "rewriting": "rewriting players.db",
    }
    label = label_map.get(stage, stage)
    if total > 0:
        pct = min(1.0, copied / total)
        filled = int(pct * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        line = (f"  {ACCENT}{label}{RESET}  "
                f"{MUTED}[{RESET}{bar}{MUTED}]{RESET} "
                f"{copied}/{total} ({pct*100:5.1f}%)")
    else:
        spin = "|/-\\"[copied % 4]
        line = f"  {ACCENT}{label}{RESET} {spin}"
    sys.stdout.write("\r" + line + " " * 6)
    sys.stdout.flush()


# ---------- backup menu ----------

def backup_menu() -> None:
    while True:
        items = [
            MenuItem("Backup an existing save",
                     hint="zip a save (+ MP player & server config) to ~/Zomboid/PZSaveMixer_Backups",
                     value="b"),
            MenuItem("Restore from backup",
                     hint="default: restore as a new save",
                     value="r"),
            MenuItem("List / manage backups",
                     hint="show, delete",
                     value="l"),
        ]
        ans = menu(["Main", "Backup & restore"], items)
        if ans in (SENTINEL_BACK, SENTINEL_QUIT):
            return
        if ans == "b":
            backup_pick_save()
        elif ans == "r":
            restore_flow()
        elif ans == "l":
            list_backups_flow()


def backup_pick_save() -> None:
    items = [_save_item(s) for s in STATE.saves]
    pick = menu(["Main", "Backup & restore", "pick save"], items,
                empty_msg="no saves found")
    if isinstance(pick, Save):
        do_backup(pick)


def do_backup(s: Save) -> None:
    note = prompt_text("optional note (or empty)", default="", allow_empty=True)
    print(f"\n  {MUTED}archiving {s.name}…{RESET}")
    try:
        # progress: print a dot every N files
        n = [0]
        def prog(name):
            n[0] += 1
            if n[0] % 200 == 0:
                sys.stdout.write("."); sys.stdout.flush()
        rec = backup.backup(s, note=note or None, progress=prog)
        print()
        print(f"  {OK}✓ {rec.zip_path.name}{RESET}  ({backup.human_size(rec.size_bytes)})")
        print(f"    {MUTED}{rec.zip_path}{RESET}")
    except Exception as e:
        print(f"  {BAD}backup failed: {e}{RESET}")
        traceback.print_exc()
    pause()


def restore_flow() -> None:
    records = backup.list_backups()
    items = []
    for r in records:
        hint = (f"{r.kind}/{r.mode}  {r.stamp}  "
                f"{backup.human_size(r.size_bytes)}"
                + (f"  note: {r.note}" if r.note else ""))
        items.append(MenuItem(r.save_name, hint=hint, value=r))
    pick = menu(["Main", "Backup & restore", "restore"], items,
                empty_msg="no backups found")
    if not isinstance(pick, backup.BackupRecord):
        return

    # Default = restore as new
    suggested = f"{pick.save_name}_restored"
    name = prompt_text("restore as save name", default=suggested)
    if not name:
        return

    # Check collisions
    dests = backup._plan_restore_dests(kind=pick.kind, mode=pick.mode, name=name)
    collisions = [p for p in dests.values() if p.exists()]
    overwrite = False
    if collisions:
        print(f"\n  {WARN}destination already exists:{RESET}")
        for p in collisions:
            print(f"    {p}")
        if confirm("overwrite?", default=False):
            if not typed_confirm(
                "this will DELETE the existing save before restoring",
                must_type=name,
            ):
                print(f"  {MUTED}aborted.{RESET}"); pause(); return
            overwrite = True
        else:
            print(f"  {MUTED}aborted.{RESET}"); pause(); return

    try:
        out = backup.restore(pick, target_name=name, overwrite=overwrite)
        print(f"\n  {OK}✓ restored{RESET}")
        for label, p in out.items():
            print(f"    {_fmt_dest_label(label)} {p}")
    except Exception as e:
        print(f"  {BAD}restore failed: {e}{RESET}")
        traceback.print_exc()
    STATE.refresh()
    pause()


def list_backups_flow() -> None:
    while True:
        records = backup.list_backups()
        items = []
        for r in records:
            hint = (f"{r.kind}/{r.mode}  {r.stamp}  "
                    f"{backup.human_size(r.size_bytes)}"
                    + (f"  note: {r.note}" if r.note else ""))
            items.append(MenuItem(r.save_name, hint=hint, value=r))
        pick = menu(["Main", "Backup & restore", "list"], items,
                    empty_msg="no backups found")
        if pick in (SENTINEL_BACK, SENTINEL_QUIT):
            return
        if not isinstance(pick, backup.BackupRecord):
            return
        item_menu = [
            MenuItem("Show metadata", value="show"),
            MenuItem("Delete this backup", value="del"),
        ]
        a = menu(["Main", "Backup & restore", "list", pick.save_name],
                 item_menu)
        if a == "show":
            print(); print(f"  {MUTED}{pick.meta_path}{RESET}")
            try:
                print(pick.meta_path.read_text(encoding="utf-8"))
            except OSError:
                print("  (no meta sidecar)")
            pause()
        elif a == "del":
            if typed_confirm("delete this backup permanently",
                             must_type=pick.zip_path.name):
                backup.delete_backup(pick)
                print(f"  {OK}✓ deleted{RESET}"); pause()
            else:
                print(f"  {MUTED}aborted — text didn't match.{RESET}"); pause()


# ---------- settings ----------

def settings_menu() -> None:
    while True:
        info = (
            f"  {MUTED}Zomboid root :{RESET} {paths.zomboid_root()}\n"
            f"  {MUTED}Saves root   :{RESET} {paths.saves_root()}\n"
            f"  {MUTED}Backups      :{RESET} {backup.backups_dir()}\n"
            f"  {MUTED}override root with env var PZ_HOME{RESET}"
        )
        items = [
            MenuItem("Rescan saves",
                     hint="re-discover everything under the Zomboid root",
                     value="rescan"),
            MenuItem("Open backups folder",
                     hint=f"{backup.backups_dir()}",
                     value="openbackups"),
        ]
        ans = menu(["Main", "Settings & info"], items, status=info)
        if ans in (SENTINEL_BACK, SENTINEL_QUIT):
            return
        if ans == "rescan":
            STATE.refresh()
        elif ans == "openbackups":
            try:
                os.startfile(backup.backups_dir())  # type: ignore[attr-defined]
            except Exception as e:
                print(f"  {WARN}could not open: {e}{RESET}"); pause()


# ---------- entry ----------

def run() -> int:
    try:
        main_menu()
    except KeyboardInterrupt:
        print()
    print(f"\n{MUTED}bye.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
