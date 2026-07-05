from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
PACKAGE = SRC / "bookrecall"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if __name__ == "bookrecall":
    # Allow `python -c "from bookrecall.web import ..."` from the repo root.
    __path__ = [str(PACKAGE)]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from bookrecall.cli import main


if __name__ == "__main__":
    main()
