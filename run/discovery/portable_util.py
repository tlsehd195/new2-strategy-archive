"""Tiny helpers for the two scripts that run on a user's own PC (Windows
included): ``public_market_collect.py`` and ``public_cost_estimate.py``.

They must not import ``engine_lib`` (it uses ``fcntl``, which does not exist
on Windows) or ``costs_bridge`` (it needs the NEW2 source checkout), so the
few helpers they need live here.  Standard library only.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Mapping


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canonical_json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def append_line(path: Path, record: Mapping) -> None:
    """Append one JSON line with fsync.  If an earlier crash left a torn last
    line (no trailing newline), a newline is written first so the fragment
    stays its own (unparseable, counted) line instead of merging with this one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_nl = False
    if path.exists() and path.stat().st_size > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            needs_nl = f.read(1) != b"\n"
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if needs_nl:
            f.write("\n")
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
