"""Compatibility diff between a character (and its source save) and a target world."""
from __future__ import annotations

from dataclasses import dataclass, field

from .saves import Save, Character


@dataclass
class CompatReport:
    worldversion_match: bool
    worldversion_char: int
    worldversion_world: int

    mods_missing_in_target: list[str] = field(default_factory=list)
    mods_extra_in_target: list[str] = field(default_factory=list)
    mods_common: int = 0

    map_char_source: str | None = None
    map_target: str | None = None
    map_matches: bool = True

    cross_kind: bool = False        # SP → MP or vice versa

    @property
    def has_warnings(self) -> bool:
        return (not self.worldversion_match
                or bool(self.mods_missing_in_target)
                or bool(self.mods_extra_in_target)
                or not self.map_matches
                or self.cross_kind)

    @property
    def severity(self) -> str:
        # "ok" / "warn" / "danger"
        if not self.worldversion_match:
            return "danger"
        if self.mods_missing_in_target or self.cross_kind:
            return "warn"
        if self.mods_extra_in_target or not self.map_matches:
            return "warn"
        return "ok"


def compare(character: Character, source_save: Save, target_world: Save
            ) -> CompatReport:
    src_mods = set(source_save.mods)
    tgt_mods = set(target_world.mods)
    missing = sorted(src_mods - tgt_mods)
    extra = sorted(tgt_mods - src_mods)

    rep = CompatReport(
        worldversion_match=(character.worldversion == (target_world.worldversion or character.worldversion)),
        worldversion_char=character.worldversion,
        worldversion_world=target_world.worldversion or 0,
        mods_missing_in_target=missing,
        mods_extra_in_target=extra,
        mods_common=len(src_mods & tgt_mods),
        map_char_source=source_save.map_value,
        map_target=target_world.map_value,
        map_matches=(source_save.map_value == target_world.map_value)
                    if (source_save.map_value and target_world.map_value)
                    else True,
        cross_kind=(source_save.kind != target_world.kind),
    )
    return rep
