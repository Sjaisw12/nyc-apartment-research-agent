"""Hold raw evidence in memory during retrieval; nothing touches the database then.

Adapters call `put()` as they normalize rows. The buffer is written later, inside the
same transaction as the public source result (storage/evidence_pg.py: write_raw_rows),
so a failed save leaves no evidence behind and a failed retrieval writes nothing.
"""

from __future__ import annotations

from ..records.models import RawEvidence


class EvidenceConflictError(ValueError):
    """Two different rows arrived under one keyed digest (should be impossible)."""


class BufferedEvidenceStore:
    def __init__(self) -> None:
        self._items: dict[str, RawEvidence] = {}

    def put(self, item: RawEvidence) -> None:
        held = self._items.get(item.original_digest)
        if held is not None:
            if held.original_row != item.original_row or held.basis != item.basis:
                raise EvidenceConflictError("different rows under one digest")
            return  # the same row seen again (e.g. by two sources or two pages)
        self._items[item.original_digest] = item

    def get(self, original_digest: str) -> RawEvidence | None:
        """Only what this buffer holds; stored evidence is read with an audit reader."""
        return self._items.get(original_digest)

    def items(self) -> list[RawEvidence]:
        return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()
