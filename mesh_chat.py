#!/usr/bin/env python3
"""
Direct Tactical Pluto SDR RF Chat entrypoint.
Communicates directly over Pluto SDR RX/TX via 2-FSK at 915.000 MHz.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SRC_PATH = REPO_ROOT / "src"
for p in (str(REPO_ROOT), str(SRC_PATH)):
    if p not in sys.path:
        sys.path.insert(0, p)

from tools.rf_chat import main

if __name__ == "__main__":
    main()

