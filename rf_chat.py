#!/usr/bin/env python3
"""
Direct Tactical Pluto SDR RF Chat entrypoint.
Communicates directly over Pluto SDR RX/TX via 2-FSK at 915.000 MHz.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.rf_chat import main

if __name__ == "__main__":
    main()
