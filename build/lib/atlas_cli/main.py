# src/atlas_cli/main.py
from __future__ import annotations

import sys
from atlas.transform import main as transform_main

def main() -> int:
    return transform_main(sys.argv[1:])

if __name__ == "__main__":
    raise SystemExit(main())