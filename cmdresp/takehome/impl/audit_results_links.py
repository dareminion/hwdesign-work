"""Check every relative Markdown link in the take-home results report."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "results.md"
REPO = ROOT.parent.parent


def main() -> None:
    text = REPORT.read_text(encoding="utf-8")
    targets = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
    missing = [target for target in targets if not (ROOT / target).exists()]
    ignored = []
    for target in targets:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", f"cmdresp/takehome/{target}"],
            cwd=REPO,
            check=False,
        )
        if result.returncode == 0:
            ignored.append(target)
    print(f"links checked: {len(targets)}")
    print(f"missing links: {len(missing)}")
    print(f"ignored evidence links: {len(ignored)}")
    for target in missing:
        print(f"MISSING {target}")
    for target in ignored:
        print(f"IGNORED {target}")
    if missing or ignored:
        raise SystemExit(1)


if __name__ == "__main__":
    main()