"""Entry point — numbered-menu loop."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# Allow running as either `python -m pzmix.main` or `python pzmix\main.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pzmix import paths, saves as save_mod, compat, compose, backup, ui, steam, pzchar, config as cfg
from pzmix.ui import (
    MenuItem, menu, multi_menu, header, hr, pause, clear, prompt_text, confirm,
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
            MenuItem("Characters (portable .pzchar files)",
                     hint=f"export to / list ~/Zomboid/Characters/ for sharing & re-import",
                     value="chars"),
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
        elif ans == "chars":
            characters_menu()
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
    """Pick world → pick target mode → pick character(s) → name → confirm → export.

    For MP targets, characters can be picked from any existing save OR from
    .pzchar files in ~/Zomboid/Characters/ (multi-select). SP targets accept
    at most one character.
    """
    # 1. World
    world_items = [_save_item(s) for s in STATE.saves]
    world = menu(["Main", "Mix", "1/5  pick world"], world_items,
                 prompt="world",
                 empty_msg="no saves found — refresh main menu first")
    if not isinstance(world, Save):
        return

    # 2. Target mode (determines whether character selection is single or multi)
    mode_items = [
        MenuItem("SP — Sandbox",     value=("SP", "Sandbox")),
        MenuItem("SP — Apocalypse",  value=("SP", "Apocalypse")),
        MenuItem("SP — Survivor",    value=("SP", "Survivor")),
        MenuItem("SP — Builder",     value=("SP", "Builder")),
        MenuItem("MP — Hosted (self-host)", value=("MP", "Multiplayer"),
                 hint="creates Saves/Multiplayer/<name> + _player + Server/<name>.ini"),
    ]
    mode_pick = menu(["Main", "Mix", "2/5  target mode"], mode_items,
                     prompt="mode")
    if not isinstance(mode_pick, tuple):
        return
    target_kind, target_mode = mode_pick

    # 3. Character(s) — pool of saves + .pzchar files
    pool_items, pool_specs = _build_character_pool()
    is_mp = (target_kind == SAVE_KIND_MP)
    if is_mp:
        # Empty selection is permitted (export world only).
        picked = multi_menu(
            ["Main", "Mix", "3/5  pick character(s)"],
            pool_items,
            prompt="toggle",
            status=f"{MUTED}pick zero or more — for group play, select each "
                   f"joining player's character{RESET}",
            empty_msg="no characters or .pzchar files found",
        )
        if picked in (SENTINEL_BACK, SENTINEL_QUIT):
            return
        picked_indices = list(picked) if isinstance(picked, list) else []
    else:
        # SP: single-select, with an explicit "no character" option.
        sp_items = list(pool_items) + [MenuItem(
            f"{MUTED}— empty world (no character) —{RESET}",
            value="NONE", hint="export the world by itself")]
        single = menu(["Main", "Mix", "3/5  pick character"], sp_items,
                      prompt="character",
                      empty_msg="no characters or .pzchar files found")
        if single in (SENTINEL_BACK, SENTINEL_QUIT):
            return
        if single == "NONE":
            picked_indices = []
        elif isinstance(single, int):
            picked_indices = [single]
        else:
            return

    chosen_specs = [pool_specs[i] for i in picked_indices]

    # 4. Name (always) + default MP creds (only needed if any chosen char lacks them)
    suggested = f"{world.name}_mix"
    name = prompt_text("new save name", default=suggested)
    if not name:
        return
    plan = compose.ExportPlan(
        target_kind=target_kind, target_mode=target_mode, target_name=name,
        source_world=world, characters=chosen_specs,
    )

    if is_mp:
        # Map= for MP source: copy from the existing server INI.
        # For SP source: stock Muldraugh KY (user can edit INI for a map mod).
        plan.target_map = world.map_value or "Muldraugh, KY"

        # Default creds only matter for characters that don't carry their own.
        chars_needing_creds = [s for s in chosen_specs
                               if not s.character.steamid]
        if chars_needing_creds:
            local_user = steam.find_current_user()
            if local_user:
                print(f"  {MUTED}detected local Steam user: "
                      f"{local_user.persona_name or local_user.account_name} "
                      f"({local_user.steamid64}){RESET}")
            print(f"  {MUTED}{len(chars_needing_creds)} character(s) need "
                  f"MP credentials (no .pzchar / MP-source data bundled). "
                  f"These defaults apply to every such character.{RESET}")
            default_user = (local_user.account_name if local_user else "Player1")
            default_sid = (local_user.steamid64 if local_user else "0")
            plan.default_username = prompt_text(
                "default MP username", default=default_user)
            plan.default_steamid = prompt_text(
                "default steamid (numeric, no quotes)", default=default_sid)

    confirm_export(plan)


def _build_character_pool() -> tuple[list[MenuItem], list[compose.CharacterSpec]]:
    """Aggregate every selectable character from discovered saves + every
    .pzchar file under ~/Zomboid/Characters/. Returns parallel lists where
    MenuItem.value is the index into the spec list."""
    items: list[MenuItem] = []
    specs: list[compose.CharacterSpec] = []

    # From saves
    for s in STATE.saves:
        for c in s.characters:
            specs.append(compose.CharacterSpec(
                character=c, source_save=s,
                source_label=f"{s.name} ({s.mode})"))
            tag = "MP" if c.is_network else "SP"
            dead = f" {BAD}[DEAD]{RESET}" if c.is_dead else ""
            user = f" {DIM}user={c.username}{RESET}" if c.username else ""
            items.append(MenuItem(
                label=c.name,
                hint=f"from {s.name} ({s.mode}) {c.short_coords} "
                     f"{fmt_wv(c.worldversion)}{user}{dead}",
                value=len(specs) - 1,
                tag=tag,
            ))

    # From .pzchar files
    for pzf in pzchar.list_pzchar_files(paths.characters_dir()):
        ch = pzf.to_character()
        specs.append(compose.CharacterSpec(
            character=ch, source_save=None,
            source_label=f".pzchar: {pzf.path.name}"))
        cred_hint = (f"creds={pzf.mp_username}/{pzf.mp_steamid}"
                     if pzf.has_mp_creds else f"{WARN}no MP creds{RESET}")
        dead = f" {BAD}[DEAD]{RESET}" if pzf.is_dead else ""
        items.append(MenuItem(
            label=pzf.name,
            hint=f"{pzf.path.name}  src={pzf.source_save} "
                 f"{fmt_wv(pzf.worldversion)}  {cred_hint}{dead}",
            value=len(specs) - 1,
            tag="PZ",
        ))
    return items, specs


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
        if plan.characters:
            print(f"    chars  : {len(plan.characters)}")
            for spec in plan.characters:
                c = spec.character
                dead = f" {BAD}(DEAD){RESET}" if c.is_dead else ""
                # Effective credentials for MP display.
                eff_user = c.username or plan.default_username or ""
                eff_sid = c.steamid or plan.default_steamid or ""
                if plan.target_kind == SAVE_KIND_MP:
                    cred = f"  user={eff_user} sid={eff_sid}"
                    cred_src = (MUTED + "(own)" + RESET if c.steamid
                                else WARN + "(default)" + RESET)
                    cred = f"  {eff_user}/{eff_sid} {cred_src}"
                else:
                    cred = ""
                print(f"      - {c.name} {c.short_coords} "
                      f"{fmt_wv(c.worldversion)}{dead}{cred}")
                print(f"        {MUTED}from {spec.source_label}{RESET}")
        else:
            print(f"    chars  : {MUTED}(none — world only){RESET}")
        if plan.target_kind == SAVE_KIND_MP:
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

        # Per-character compatibility diffs (only when we have a source save
        # to diff against; .pzchar sources may not carry one).
        for spec in plan.characters:
            if spec.source_save is None:
                continue
            rep = compat.compare(spec.character, spec.source_save,
                                 plan.source_world)
            if rep.has_warnings:
                print(f"\n  {HEAD}{spec.character.name}{RESET} "
                      f"{MUTED}({spec.source_label}){RESET}")
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


# ---------- characters menu ----------

def characters_menu() -> None:
    while True:
        chars_dir = paths.characters_dir()
        existing = pzchar.list_pzchar_files(chars_dir)
        info = f"  {MUTED}.pzchar storage:{RESET} {chars_dir}  " \
               f"{MUTED}({len(existing)} file(s)){RESET}"
        items = [
            MenuItem("Export a character to .pzchar",
                     hint="pick a character from any save → portable file",
                     value="export"),
            MenuItem("List / manage existing .pzchar files",
                     hint="show details, delete",
                     value="list"),
        ]
        ans = menu(["Main", "Characters"], items, status=info)
        if ans in (SENTINEL_BACK, SENTINEL_QUIT):
            return
        if ans == "export":
            export_character_flow()
        elif ans == "list":
            list_pzchar_flow()


def export_character_flow() -> None:
    """Pick one character from any save and write it to ~/Zomboid/Characters/."""
    items: list[MenuItem] = []
    pairs: list[tuple[Character, Save]] = []
    for s in STATE.saves:
        for c in s.characters:
            pairs.append((c, s))
            items.append(_char_item(c, s))
    if not items:
        print(f"  {MUTED}no characters found in any save.{RESET}"); pause(); return
    pick = menu(["Main", "Characters", "export"], items,
                prompt="character",
                empty_msg="no characters found")
    if not isinstance(pick, tuple) or not isinstance(pick[0], Character):
        return
    c, s = pick

    # Suggest filename. User can override.
    suggested = pzchar.suggested_filename(c, s.name)
    filename = prompt_text("filename", default=suggested)
    if not filename:
        return
    if not filename.lower().endswith(pzchar.PZCHAR_EXTENSION):
        filename += pzchar.PZCHAR_EXTENSION
    out = paths.characters_dir() / filename
    if out.exists():
        if not confirm(f"{out.name} already exists — overwrite?", default=False):
            print(f"  {MUTED}cancelled.{RESET}"); pause(); return

    note = prompt_text("optional note", default="", allow_empty=True)

    # Steam auto-detect for SP characters so the file is MP-importable
    # without any host-side prompts.
    local_user = None
    if not c.steamid:
        local_user = steam.find_current_user()
        if local_user:
            print(f"  {MUTED}bundling local Steam credentials so the host "
                  f"doesn't need to ask: "
                  f"{local_user.account_name} / {local_user.steamid64}{RESET}")
        else:
            print(f"  {WARN}Steam not detected — this .pzchar will have no MP "
                  f"credentials. Importing host will have to supply them.{RESET}")

    try:
        path = pzchar.export_character(
            c, source_save_name=s.name, source_kind=s.kind,
            source_mods=s.mods, source_map=s.map_value, output_path=out,
            note=note or None, local_steam_user=local_user, overwrite=True,
        )
    except Exception as e:
        print(f"  {BAD}export failed: {e}{RESET}"); pause(); return
    print(f"\n  {OK}✓ wrote {path.name}{RESET}  {MUTED}({path.stat().st_size} bytes){RESET}")
    print(f"    {MUTED}{path}{RESET}")
    pause()


def list_pzchar_flow() -> None:
    while True:
        chars_dir = paths.characters_dir()
        records = pzchar.list_pzchar_files(chars_dir)
        items = []
        for r in records:
            cred = (f"{r.mp_username}/{r.mp_steamid}" if r.has_mp_creds
                    else f"{WARN}no MP creds{RESET}")
            dead = f"  {BAD}[DEAD]{RESET}" if r.is_dead else ""
            items.append(MenuItem(
                label=r.display_label,
                hint=f"{r.path.name}  src={r.source_save}  "
                     f"{fmt_wv(r.worldversion)}  {cred}{dead}",
                value=r,
            ))
        pick = menu(["Main", "Characters", "list"], items,
                    empty_msg=f"no .pzchar files in {chars_dir}")
        if pick in (SENTINEL_BACK, SENTINEL_QUIT):
            return
        if not isinstance(pick, pzchar.PZCharFile):
            return
        action = menu(
            ["Main", "Characters", "list", pick.display_label],
            [MenuItem("Show details", value="show"),
             MenuItem("Delete this .pzchar", value="del")],
        )
        if action == "show":
            print(); print(f"  {HEAD}{pick.name}{RESET}")
            print(f"  {MUTED}path        : {RESET}{pick.path}")
            print(f"  {MUTED}exported_at : {RESET}{pick.exported_at}")
            print(f"  {MUTED}source      : {RESET}{pick.source_save} ({pick.source_kind})")
            print(f"  {MUTED}worldver    : {RESET}{pick.worldversion}")
            print(f"  {MUTED}blob_size   : {RESET}{pick.blob_size} bytes")
            print(f"  {MUTED}is_dead     : {RESET}{pick.is_dead}")
            print(f"  {MUTED}coords      : {RESET}{pick.coords}")
            print(f"  {MUTED}MP user     : {RESET}{pick.mp_username}")
            print(f"  {MUTED}MP steamid  : {RESET}{pick.mp_steamid}")
            print(f"  {MUTED}source_map  : {RESET}{pick.source_map}")
            print(f"  {MUTED}mods({len(pick.source_mods)}):{RESET}")
            for m in pick.source_mods[:20]:
                print(f"      - {m}")
            if len(pick.source_mods) > 20:
                print(f"      {MUTED}…and {len(pick.source_mods)-20} more{RESET}")
            if pick.note:
                print(f"  {MUTED}note        : {RESET}{pick.note}")
            pause()
        elif action == "del":
            if typed_confirm("delete this .pzchar permanently",
                             must_type=pick.path.name):
                try:
                    pick.path.unlink()
                    print(f"  {OK}✓ deleted{RESET}"); pause()
                except OSError as e:
                    print(f"  {BAD}delete failed: {e}{RESET}"); pause()
            else:
                print(f"  {MUTED}aborted — text didn't match.{RESET}"); pause()


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
        # Show where the resolved root came from so the user understands
        # which knob is in play.
        env_set = bool(os.environ.get("PZ_HOME"))
        configured = cfg.get_zomboid_root()
        if env_set:
            origin = f"{ACCENT}env var PZ_HOME{RESET}"
        elif configured:
            origin = f"{ACCENT}saved config{RESET} ({cfg.config_path()})"
        elif paths.zomboid_root() == paths.DEFAULT_ZOMBOID_ROOT:
            origin = f"{MUTED}default{RESET}"
        else:
            origin = f"{MUTED}fallback{RESET}"

        info = (
            f"  {MUTED}Zomboid root :{RESET} {paths.zomboid_root()}  "
            f"{MUTED}[{RESET}{origin}{MUTED}]{RESET}\n"
            f"  {MUTED}Saves root   :{RESET} {paths.saves_root()}\n"
            f"  {MUTED}Backups      :{RESET} {backup.backups_dir()}"
        )
        items = [
            MenuItem("Rescan saves",
                     hint="re-discover everything under the Zomboid root",
                     value="rescan"),
            MenuItem("Change Zomboid root path",
                     hint="prompts for a new path and persists it",
                     value="changeroot"),
            MenuItem("Open backups folder",
                     hint=f"{backup.backups_dir()}",
                     value="openbackups"),
        ]
        if configured:
            items.append(MenuItem(
                "Reset to default (~/Zomboid)",
                hint="forgets the saved custom path",
                value="resetroot"))
        ans = menu(["Main", "Settings & info"], items, status=info)
        if ans in (SENTINEL_BACK, SENTINEL_QUIT):
            return
        if ans == "rescan":
            STATE.refresh()
        elif ans == "changeroot":
            if _prompt_for_zomboid_root(current=paths.zomboid_root()):
                STATE.refresh()
        elif ans == "resetroot":
            if confirm("forget custom path and use default?", default=False):
                cfg.clear_zomboid_root()
                STATE.refresh()
        elif ans == "openbackups":
            try:
                os.startfile(backup.backups_dir())  # type: ignore[attr-defined]
            except Exception as e:
                print(f"  {WARN}could not open: {e}{RESET}"); pause()


# ---------- first-run setup ----------

def _prompt_for_zomboid_root(*, current: Path | None = None,
                             first_run: bool = False) -> bool:
    """Ask the user where their Zomboid data folder lives. Saves to the
    persistent config on success (this is the only code path that writes
    a config file). Returns True if a path was set, False if the user
    cancelled."""
    clear()
    if first_run:
        header(["First-run setup"])
        print()
        print(f"  {WARN}Project Zomboid data folder not found at the "
              f"default location:{RESET}")
        print(f"    {paths.DEFAULT_ZOMBOID_ROOT}")
        print()
        print(f"  {MUTED}This usually means one of:{RESET}")
        print(f"    {MUTED}• You haven't launched Project Zomboid yet — it "
              f"creates the folder on first launch. Run PZ once, then "
              f"come back.{RESET}")
        print(f"    {MUTED}• Your data lives in a non-standard location. "
              f"Tell me where and I'll remember it for future runs.{RESET}")
        print()
    else:
        header(["Main", "Settings & info", "Change Zomboid root"])
        print()
        print(f"  {MUTED}Current root:{RESET} {current}")
        print()
    print(f"  {MUTED}Enter the path to the Zomboid folder (containing "
          f"Saves/, Server/, etc.). Empty input cancels.{RESET}")
    print(hr())

    while True:
        try:
            raw = input(f"  path » ").strip().strip('"').strip("'")
        except (EOFError, KeyboardInterrupt):
            return False
        if not raw:
            print(f"  {MUTED}cancelled.{RESET}")
            return False
        # ~ expansion + env vars
        candidate = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not candidate.is_dir():
            print(f"  {BAD}not a directory:{RESET} {candidate}")
            print(f"  {MUTED}try again, or leave blank to cancel.{RESET}")
            continue
        # Soft hint when it doesn't look like a Zomboid folder, but still
        # accept — could be a fresh path the user wants to set up.
        if not any((candidate / sub).is_dir()
                   for sub in ("Saves", "Server", "mods")):
            if not confirm(
                f"this folder has no Saves/, Server/, or mods/ subdir — "
                f"use it anyway?",
                default=False,
            ):
                continue
        cfg.set_zomboid_root(candidate)
        print(f"\n  {OK}✓ saved root → {candidate}{RESET}")
        print(f"  {MUTED}stored in {cfg.config_path()}{RESET}")
        pause()
        return True


# ---------- entry ----------

def run() -> int:
    # First-run path discovery: if neither the default ~/Zomboid nor a
    # saved override resolves, ask the user. Skipped when $PZ_HOME points
    # at something valid because that's an explicit, transient override.
    if not paths.zomboid_root_exists():
        if not _prompt_for_zomboid_root(first_run=True):
            print(f"\n{MUTED}can't continue without a Zomboid folder. bye.{RESET}")
            return 1

    try:
        main_menu()
    except KeyboardInterrupt:
        print()
    print(f"\n{MUTED}bye.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
