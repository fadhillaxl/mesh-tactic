#!/usr/bin/env python3
"""
Root entrypoint shim for Tactical SDR 2-FSK Modem.
Forwards execution to `tsm.modem.sdr_driver.main()` with src in sys.path.
"""

import sys
from pathlib import Path

SRC_PATH = Path(__file__).resolve().parent / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tsm.modem.sdr_driver import main

if __name__ == "__main__":
    main()
