"""Visual demo of the export progress bar."""
import sys, time, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for stream in (sys.stdout, sys.stderr):
    try: stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

from pzmix.main import _render_progress
from pzmix.ui import OK, RESET

_render_progress("counting", 0, 0)
time.sleep(0.4)
_render_progress("counting", 1, 0)
time.sleep(0.2)

for i in range(0, 4001, 73):
    _render_progress("copying", i, 4000)
    time.sleep(0.01)
_render_progress("copying", 4000, 4000)
time.sleep(0.3)
_render_progress("rewriting", 4000, 4000)
time.sleep(0.2)
sys.stdout.write("\r" + " " * 78 + "\r")
print(f"  {OK}✓ done{RESET}")
