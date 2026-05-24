"""Numbered-menu CLI primitives.

DOS-style sensibility, modern niceties:
  - ANSI colors (auto-enabled on Win10 conhost via the VT flag)
  - breadcrumb header
  - numbered choices with extra letter shortcuts (q quit, b back, r refresh, ?)
  - input validation with friendly retry
  - no third-party dependencies
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Callable

# -------- ANSI bootstrap --------

def _enable_win_vt() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)            # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return
        ENABLE_VT = 0x0004
        k.SetConsoleMode(h, mode.value | ENABLE_VT)
        # Also flip the console code page to UTF-8 so box-drawing renders.
        k.SetConsoleOutputCP(65001)
        k.SetConsoleCP(65001)
    except Exception:
        pass


def _force_utf8_stdout() -> None:
    """Make print() tolerate non-cp1252 glyphs even when not on a TTY."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_enable_win_vt()
_force_utf8_stdout()
_USE_COLOR = sys.stdout.isatty() and os.environ.get("PZMIX_NO_COLOR") != "1"


def _c(code: str) -> str:
    return f"\x1b[{code}m" if _USE_COLOR else ""


RESET   = _c("0")
BOLD    = _c("1")
DIM     = _c("2")
INVERT  = _c("7")

FG_GREY    = _c("90")
FG_RED     = _c("31")
FG_GREEN   = _c("32")
FG_YELLOW  = _c("33")
FG_BLUE    = _c("34")
FG_MAGENTA = _c("35")
FG_CYAN    = _c("36")
FG_WHITE   = _c("97")

# semantic
HEAD   = BOLD + FG_CYAN
ACCENT = BOLD + FG_YELLOW
OK     = FG_GREEN
WARN   = FG_YELLOW
BAD    = FG_RED
MUTED  = FG_GREY


# -------- screen helpers --------

def clear() -> None:
    if _USE_COLOR:
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()
    else:
        os.system("cls" if os.name == "nt" else "clear")


def hr(width: int = 78, char: str = "─") -> str:
    return MUTED + (char * width) + RESET


def header(crumbs: list[str]) -> None:
    line = f" {ACCENT}PZSaveMixer{RESET} {MUTED}»{RESET} " + \
           f" {MUTED}»{RESET} ".join(f"{HEAD}{c}{RESET}" for c in crumbs)
    print(line)
    print(hr())


def banner_status(status: str | None) -> None:
    if status:
        print(f" {status}{RESET}")
        print(hr())


def pause(prompt: str = "press Enter to continue") -> None:
    try:
        input(f"\n{MUTED}{prompt}…{RESET} ")
    except (EOFError, KeyboardInterrupt):
        print()


# -------- menu --------

@dataclass
class MenuItem:
    label: str
    value: object = None
    hint: str | None = None      # secondary line under the label
    disabled: bool = False
    tag: str | None = None       # short colored tag at the front (e.g. "MP")


SENTINEL_BACK = object()
SENTINEL_QUIT = object()
SENTINEL_REFRESH = object()


def menu(
    crumbs: list[str],
    items: list[MenuItem],
    *,
    prompt: str = "select",
    status: str | None = None,
    allow_back: bool = True,
    allow_quit: bool = True,
    allow_refresh: bool = False,
    extra: list[tuple[str, str, object]] | None = None,  # (key, label, sentinel)
    empty_msg: str = "(nothing to show)",
) -> object:
    """Render a numbered menu and return the selected MenuItem.value
    (or one of the sentinels)."""
    while True:
        clear()
        header(crumbs)
        banner_status(status)

        if not items:
            print(f"\n  {MUTED}{empty_msg}{RESET}\n")
        else:
            for i, it in enumerate(items, 1):
                num = f"{ACCENT}{i:>3}{RESET}"
                tag = ""
                if it.tag:
                    tag = f" {DIM}[{it.tag}]{RESET}"
                color_label = it.label if not it.disabled \
                    else f"{MUTED}{it.label}{RESET}"
                print(f"  {num}.{tag} {color_label}")
                if it.hint:
                    print(f"       {MUTED}{it.hint}{RESET}")
            print()

        keys: list[tuple[str, str, object]] = []
        if allow_refresh:
            keys.append(("r", "refresh", SENTINEL_REFRESH))
        if allow_back:
            keys.append(("b", "back", SENTINEL_BACK))
        if allow_quit:
            keys.append(("q", "quit", SENTINEL_QUIT))
        for x in extra or []:
            keys.append(x)
        bar = "   ".join(f"{ACCENT}{k}{RESET} {MUTED}{lbl}{RESET}"
                         for k, lbl, _ in keys)
        if bar:
            print(f"  {bar}")
        print(hr())

        try:
            raw = input(f"  {prompt} » ").strip()
        except (EOFError, KeyboardInterrupt):
            return SENTINEL_QUIT
        if not raw:
            continue

        low = raw.lower()
        for k, _lbl, sent in keys:
            if low == k:
                return sent
        if raw.isdigit():
            i = int(raw)
            if 1 <= i <= len(items):
                it = items[i - 1]
                if it.disabled:
                    print(f"  {WARN}that option is disabled.{RESET}")
                    pause()
                    continue
                return it.value
        print(f"  {BAD}? invalid selection: {raw!r}{RESET}")
        pause()


def multi_menu(
    crumbs: list[str],
    items: list[MenuItem],
    *,
    prompt: str = "toggle / done",
    status: str | None = None,
    allow_back: bool = True,
    allow_quit: bool = True,
    min_selected: int = 0,
    max_selected: int | None = None,
    empty_msg: str = "(nothing to pick from)",
    initial: list[int] | None = None,
) -> list[object] | object:
    """Multi-select numbered menu. User types numbers (single, comma- or
    space-separated, or ranges like '1-4') to toggle; types 'a' to select all,
    'n' to clear, 'd' to confirm. Returns the selected values in original
    order, or SENTINEL_BACK / SENTINEL_QUIT.

    `max_selected` caps how many can be picked (e.g. 1 for SP target).
    `min_selected` enforces at least N before 'd' is accepted.
    """
    selected: set[int] = set(initial or [])

    def render_status_line() -> str:
        n = len(selected)
        if max_selected == 1:
            return f"{n}/1 selected"
        if max_selected is not None:
            return f"{n}/{max_selected} selected"
        if min_selected:
            return f"{n} selected (≥{min_selected} required)"
        return f"{n} selected"

    while True:
        clear()
        header(crumbs)
        banner_status(status)

        if not items:
            print(f"\n  {MUTED}{empty_msg}{RESET}\n")
        else:
            for i, it in enumerate(items, 1):
                checked = (i - 1) in selected
                mark = f"{OK}[X]{RESET}" if checked else f"{MUTED}[ ]{RESET}"
                num = f"{ACCENT}{i:>3}{RESET}"
                tag = f" {DIM}[{it.tag}]{RESET}" if it.tag else ""
                color_label = it.label if not it.disabled \
                    else f"{MUTED}{it.label}{RESET}"
                print(f"  {num}. {mark}{tag} {color_label}")
                if it.hint:
                    print(f"           {MUTED}{it.hint}{RESET}")
            print()
            print(f"  {MUTED}{render_status_line()}{RESET}")

        keys: list[tuple[str, str]] = [
            ("a", "select all"),
            ("n", "clear all"),
            ("d", "done"),
        ]
        if allow_back: keys.append(("b", "back"))
        if allow_quit: keys.append(("q", "quit"))
        print("  " + "   ".join(f"{ACCENT}{k}{RESET} {MUTED}{lbl}{RESET}"
                                for k, lbl in keys))
        print(hr())

        try:
            raw = input(f"  {prompt} » ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return SENTINEL_QUIT
        if not raw:
            continue
        if raw == "q":
            return SENTINEL_QUIT
        if raw == "b" and allow_back:
            return SENTINEL_BACK
        if raw == "d":
            if len(selected) < min_selected:
                print(f"  {WARN}need at least {min_selected} selection(s).{RESET}")
                pause()
                continue
            return [items[i].value for i in sorted(selected)]
        if raw == "a":
            if max_selected and max_selected < len(items):
                selected = set(range(max_selected))
            else:
                selected = {i for i, it in enumerate(items) if not it.disabled}
            continue
        if raw == "n":
            selected.clear()
            continue

        # Parse number tokens: comma- or space-separated, with ranges.
        tokens = re.split(r"[,\s]+", raw)
        bad = False
        proposed = set(selected)
        for tok in tokens:
            if not tok:
                continue
            m = re.match(r"^(\d+)(?:-(\d+))?$", tok)
            if not m:
                bad = True; break
            lo = int(m.group(1))
            hi = int(m.group(2)) if m.group(2) else lo
            if lo < 1 or hi > len(items) or lo > hi:
                bad = True; break
            for n in range(lo, hi + 1):
                idx = n - 1
                if items[idx].disabled:
                    continue
                if idx in proposed:
                    proposed.discard(idx)
                else:
                    proposed.add(idx)
        if bad:
            print(f"  {BAD}? invalid selection: {raw!r}{RESET}")
            pause()
            continue
        if max_selected is not None and len(proposed) > max_selected:
            print(f"  {WARN}cap of {max_selected} reached — "
                  f"deselect something first.{RESET}")
            pause()
            continue
        selected = proposed


import re  # noqa: E402  — used by multi_menu range parsing


# -------- prompts --------

def prompt_text(label: str, default: str | None = None,
                allow_empty: bool = False) -> str | None:
    suffix = f" {MUTED}[{default}]{RESET}" if default else ""
    while True:
        try:
            raw = input(f"  {label}{suffix} » ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw:
            if default is not None:
                return default
            if allow_empty:
                return ""
            print(f"  {WARN}value required.{RESET}")
            continue
        return raw


def confirm(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    try:
        raw = input(f"  {label} [{suffix}] » ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not raw:
        return default
    return raw in ("y", "yes")


def typed_confirm(label: str, must_type: str) -> bool:
    """High-friction confirm: user must type a specific string exactly."""
    print(f"  {WARN}{label}{RESET}")
    print(f"  {MUTED}to confirm, type exactly: {RESET}{ACCENT}{must_type}{RESET}")
    try:
        raw = input(f"  » ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return raw == must_type


# -------- formatters --------

def tag_for_kind(kind: str) -> str:
    return f"{FG_MAGENTA}MP{RESET}" if kind == "MP" else f"{FG_BLUE}SP{RESET}"


def fmt_wv(wv: int | None) -> str:
    if not wv:
        return f"{MUTED}wv?{RESET}"
    return f"{MUTED}wv{wv}{RESET}"
