"""Benchmark backup + restore against a real save folder."""
from __future__ import annotations
import os, sys, shutil, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for s in (sys.stdout, sys.stderr):
    try: s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

REAL_SP = Path.home() / "Zomboid" / "Saves" / "Sandbox" / "2026-05-05_23-02-38"
assert REAL_SP.is_dir(), f"missing fixture: {REAL_SP}"

tmp = Path(tempfile.mkdtemp(prefix="pzmix_bench_"))
os.environ["PZ_HOME"] = str(tmp)
(tmp / "Saves" / "Sandbox").mkdir(parents=True)
print(f"copying source SP save into temp ({REAL_SP})…")
t0 = time.perf_counter()
shutil.copytree(REAL_SP, tmp / "Saves" / "Sandbox" / REAL_SP.name)
size_mb = sum(f.stat().st_size for f in (tmp / "Saves").rglob("*") if f.is_file()) / 1024**2
print(f"  copied {size_mb:.1f} MB in {time.perf_counter()-t0:.2f}s")

from pzmix import paths, saves, backup

all_saves = saves.discover_all()
sp = next(s for s in all_saves if s.name == REAL_SP.name)

print("\n== BACKUP ==")
t0 = time.perf_counter()
rec = backup.backup(sp, note="bench")
dt = time.perf_counter() - t0
zip_mb = rec.zip_path.stat().st_size / 1024**2
print(f"  wrote {zip_mb:.1f} MB zip in {dt:.2f}s "
      f"({size_mb/dt:.1f} MB/s read, {zip_mb/dt:.1f} MB/s write)")

print("\n== RESTORE (parallel) ==")
t0 = time.perf_counter()
out = backup.restore(rec, target_name="bench_restore", overwrite=False)
dt = time.perf_counter() - t0
restored_mb = sum(f.stat().st_size for f in out["world"].rglob("*") if f.is_file()) / 1024**2
n_workers = backup._extract_workers()
print(f"  restored {restored_mb:.1f} MB in {dt:.2f}s "
      f"using {n_workers} worker thread(s) ({restored_mb/dt:.1f} MB/s)")

# Cleanup just the bench tmp, not the e2e leftovers.
print(f"\ntemp dir kept for inspection: {tmp}")
