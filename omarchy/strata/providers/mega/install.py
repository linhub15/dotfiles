#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Install the adapter into the current user's Strata provider directory."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys


def install(root=None):
    source = Path(__file__).resolve().parent
    if root is None:
        config = os.environ.get("XDG_CONFIG_HOME", "")
        base = Path(config) if os.path.isabs(config) else Path.home() / ".config"
        root = base / "strata/providers"
    destination = Path(root) / "mega"
    if destination.is_symlink():
        raise SystemExit("Refusing a symlinked installation directory")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)
    files = [source / "provider.py", source / "mega_metadata.py", source / "mega_index.py", source / "mega_watch.py", source / "mega_process.py", *sorted((source / "icons").glob("*.png"))]
    for file in files:
        target = destination / file.name
        if target.is_symlink():
            raise SystemExit("Refusing a symlinked installation file")
        shutil.copyfile(file, target)
        os.chmod(target, 0o600)
    manifest = {
        "version": 1, "id": "mega", "name": "MEGA", "category": "cloud",
        "command": [str(Path(sys.executable).resolve()), str(destination / "provider.py")],
        "icons": {state: f"{state}.png" for state in ("synced", "pending", "syncing", "ignored", "paused", "error", "matched", "different", "missing", "unknown", "cloud-folder")},
    }
    target = destination / "provider.json"
    if target.is_symlink():
        raise SystemExit("Refusing a symlinked manifest")
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Alternative Strata providers directory")
    args = parser.parse_args()
    print(install(args.root))
