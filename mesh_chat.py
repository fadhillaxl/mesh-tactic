#!/usr/bin/env python3
"""
Root entrypoint shim for Tactical SDR Mesh Chat.
Forwards execution to `tsm.apps.chat.main()` with src in sys.path.
"""

import sys
from pathlib import Path

SRC_PATH = Path(__file__).resolve().parent / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tsm.apps.chat import main

if __name__ == "__main__":
    main()
