"""Quick smoke test of the discovery layer. Run with: python -m pzmix._smoke"""
from pzmix.saves import discover_all

if __name__ == "__main__":
    for s in discover_all():
        print(f"[{s.kind:<2}] {s.mode:<11} {s.name}")
        print(f"      world_dir : {s.world_dir}")
        if s.player_dir:
            print(f"      player_dir: {s.player_dir}")
        if s.server_ini:
            print(f"      server_ini: {s.server_ini}")
        print(f"      worldver  : {s.worldversion}")
        print(f"      last      : {s.last_played}")
        print(f"      map       : {s.map_value}")
        print(f"      mods({len(s.mods)}): {', '.join(s.mods[:5])}"
              + ("..." if len(s.mods) > 5 else ""))
        print(f"      chars({len(s.characters)}):")
        for c in s.characters:
            tag = "MP" if c.is_network else "SP"
            dead = " [DEAD]" if c.is_dead else ""
            extra = f" user={c.username}" if c.username else ""
            print(f"        - {tag} #{c.row_id} {c.name!r:<30} {c.short_coords}"
                  f" wv={c.worldversion}{extra}{dead} blob={len(c.data_blob)}B")
        print()
