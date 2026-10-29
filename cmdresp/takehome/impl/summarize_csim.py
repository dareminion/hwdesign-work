"""Summarize retained C-simulation status artifacts when the raw console log is absent."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "w32"
LOG = ROOT / "logs" / "csim_scenarios_summary.log"


def main() -> None:
    lines = ["Derived from retained C simulation status/unread artifacts."]
    for name in (DATA / "scenarios.txt").read_text(encoding="utf-8").split():
        directory = DATA / name / "csim"
        status = json.loads((directory / "status.json").read_text(encoding="utf-8"))
        unread = (directory / "unread_words.txt").read_text(encoding="utf-8").strip()
        lines.append(
            f"{name} halted={status['halted']} error={status['error']} "
            f"tx_id={status['tx_id']} unread_words={unread}"
        )
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines) - 1} activation rows to {LOG.relative_to(ROOT)}")


if __name__ == "__main__":
    main()