"""Storage for unredacted source rows ("raw evidence").

Raw rows may contain apartment numbers and other unit detail, so they never go into
git, logs or API responses. Task 1 keeps them in memory or in a local directory that
is git-ignored (`.evidence/`). A later Phase 2 task moves this into PostgreSQL.

Each item is stored under the SHA-256 of the original row, so any public record can be
checked against its source by re-hashing (`verify`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .models import RawEvidence
from .redaction import canonical_sha256


class EvidenceStore(Protocol):
    def put(self, item: RawEvidence) -> None: ...
    def get(self, original_sha256: str) -> RawEvidence | None: ...


class InMemoryEvidenceStore:
    def __init__(self) -> None:
        self.items: dict[str, RawEvidence] = {}

    def put(self, item: RawEvidence) -> None:
        self.items[item.original_sha256] = item

    def get(self, original_sha256: str) -> RawEvidence | None:
        return self.items.get(original_sha256)


class DirectoryEvidenceStore:
    """One JSON file per row under `root/<dataset>/<sha[:2]>/<sha>.json`."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, dataset_id: str, sha: str) -> Path:
        return self.root / dataset_id / sha[:2] / f"{sha}.json"

    def put(self, item: RawEvidence) -> None:
        path = self._path(item.dataset_id, item.original_sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item.model_dump_json(indent=1), encoding="utf-8")

    def get(self, original_sha256: str) -> RawEvidence | None:
        for path in self.root.glob(f"*/{original_sha256[:2]}/{original_sha256}.json"):
            return RawEvidence.model_validate(json.loads(path.read_text(encoding="utf-8")))
        return None


def verify(item: RawEvidence) -> bool:
    """True if the stored row still hashes to the key it was stored under."""
    return canonical_sha256(item.original_row) == item.original_sha256
