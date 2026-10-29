"""Run the approved Stage 1 check and retain its reproducible output."""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stage1_check


ROOT = Path(__file__).resolve().parent
LOG = ROOT / "logs" / "stage1_check_latest.log"


def main() -> None:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        stage1_check.main()
    text = output.getvalue()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"saved output to {LOG.relative_to(ROOT)}")


if __name__ == "__main__":
    main()