from __future__ import annotations
from atlas.transform import main as transform_main

def main(argv=None) -> int:
    return transform_main(argv)

if __name__ == "__main__":
    raise SystemExit(main())
