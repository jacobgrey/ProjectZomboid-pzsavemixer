"""Targeted tests for the config persistence + path-discovery layering.

Uses a temp directory as %LOCALAPPDATA% so it can't touch the real
user config on this machine. Run with:
  python -m pzmix._config_test
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    try: stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

# Isolate %LOCALAPPDATA% BEFORE importing pzmix.config (its config_dir()
# reads the env var lazily on each call, so we're safe to set it now).
TMP_LOCAL = Path(tempfile.mkdtemp(prefix="pzmix_cfgtest_local_"))
os.environ["LOCALAPPDATA"] = str(TMP_LOCAL)
# Also clobber PZ_HOME in case the runner has it set.
os.environ.pop("PZ_HOME", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pzmix import config, paths   # noqa: E402

passed = 0
def check(cond: bool, label: str) -> None:
    global passed
    if not cond:
        raise AssertionError(label)
    passed += 1
    print(f"  ✓ {label}")


print("[1] no config exists initially")
check(not config.config_exists(),
      "config file does not exist before any write")
check(not config.config_path().parent.is_dir(),
      "config dir does not exist before any write")

print("\n[2] reading without a config returns no override")
check(config.get_zomboid_root() is None,
      "get_zomboid_root() returns None when nothing is configured")
check(config.load_config() == {},
      "load_config() returns empty dict when no file")

print("\n[3] zomboid_root() falls back to default when nothing else is set")
# Move HOME to a tempdir so the default ~/Zomboid is a known non-existent path.
TMP_HOME = Path(tempfile.mkdtemp(prefix="pzmix_cfgtest_home_"))
os.environ["USERPROFILE"] = str(TMP_HOME)
os.environ["HOME"] = str(TMP_HOME)
# Path.home() caches platform info but not the env, so this should take effect.
# Re-import paths to make sure DEFAULT_ZOMBOID_ROOT is recomputed for this run.
import importlib                                                          # noqa: E402
importlib.reload(paths)
check(paths.zomboid_root() == TMP_HOME / "Zomboid",
      f"with no config + tmp HOME, root is {TMP_HOME / 'Zomboid'}")
check(not paths.zomboid_root_exists(),
      "zomboid_root_exists() returns False when the path is missing")

print("\n[4] set_zomboid_root writes the config file (first disk write)")
custom = Path(tempfile.mkdtemp(prefix="pzmix_cfgtest_custom_"))
(custom / "Saves").mkdir()
(custom / "Server").mkdir()
config.set_zomboid_root(custom)
check(config.config_exists(),
      "config file exists after set_zomboid_root()")
check(config.get_zomboid_root() == custom,
      "get_zomboid_root() returns the just-saved path")
check(paths.zomboid_root() == custom,
      "paths.zomboid_root() now picks up the config override")
check(paths.zomboid_root_exists(),
      "zomboid_root_exists() True now that the configured dir exists")

print("\n[5] clear_zomboid_root removes both the file and the empty dir")
config.clear_zomboid_root()
check(not config.config_exists(),
      "config file deleted after clear_zomboid_root()")
check(not config.config_dir().is_dir(),
      "empty config dir removed")
check(paths.zomboid_root() == TMP_HOME / "Zomboid",
      "after clear, root is back to the default")

print("\n[6] PZ_HOME env var takes priority over config")
config.set_zomboid_root(custom)   # set a config-level override
ENV_DIR = Path(tempfile.mkdtemp(prefix="pzmix_cfgtest_env_"))
os.environ["PZ_HOME"] = str(ENV_DIR)
check(paths.zomboid_root() == ENV_DIR,
      "PZ_HOME wins over the saved config")
del os.environ["PZ_HOME"]
check(paths.zomboid_root() == custom,
      "with PZ_HOME unset, falls back to the saved config")

print("\n[7] stale config (path no longer exists) is ignored")
shutil.rmtree(custom)
check(config.get_zomboid_root() is None,
      "get_zomboid_root() returns None when the saved path was deleted")
check(paths.zomboid_root() == TMP_HOME / "Zomboid",
      "falls back to default when configured path is gone")

print(f"\n{passed} checks passed.")
# Best-effort cleanup
for d in (TMP_LOCAL, TMP_HOME, ENV_DIR):
    try: shutil.rmtree(d, ignore_errors=True)
    except Exception: pass
