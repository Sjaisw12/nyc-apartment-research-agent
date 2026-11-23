"""Record the ECB adjudication groupings that earlier commits stored (Task 5, 2026-10-28).

    uv run python scripts/task5_ecb_adjudication_history.py   # needs the git history

Writes docs/task5/ecb_adjudication_history.json from the committed files themselves
(`git show <commit>:<path>`), so that a snapshot saved under an earlier grouping can be
recognized when it is loaded. Each entry keeps, per hearing status, the group and the
note that were stored, and the summary statement. Nothing is typed by hand.

- 2026-10-23 grouping, as committed at 7445fb8 (the last commit before the revision);
- 2026-10-28 grouping, first committed at 0127027 (same groups as now; only the
  IN VIOLATION note was refined afterwards).
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "task5" / "ecb_adjudication_history.json"
REF = "backend/src/nyc_apartment_agent/records/data/ecb_reference.json"
ADAPTER = "backend/src/nyc_apartment_agent/records/ecb_violations.py"
VERSIONS = (("2026-10-23", "7445fb8"), ("2026-10-28", "0127027"))


def _show(commit: str, path: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is needed to read the committed files")
    args = [git, "show", f"{commit}:{path}"]  # fixed commits and paths, no user input
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True).stdout  # noqa: S603


def _statement(source: str) -> str:
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "STATEMENT":
            return ast.literal_eval(node.value)
    raise LookupError("STATEMENT not found")


def build() -> dict:
    versions = []
    for version, commit in VERSIONS:
        ref = json.loads(_show(commit, REF))
        versions.append(
            {
                "version": version,
                "commit": commit,
                "adjudication": {
                    k: {"adjudication": v["adjudication"], "note": v["note"]}
                    for k, v in sorted(ref["adjudication"].items())
                },
                "statement": _statement(_show(commit, ADAPTER)),
            }
        )
    return {
        "generated_by": "backend/scripts/task5_ecb_adjudication_history.py",
        "versions": versions,
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
