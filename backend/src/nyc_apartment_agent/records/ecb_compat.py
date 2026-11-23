"""Reading ECB snapshots saved under an earlier adjudication grouping (2026-10-28).

Snapshots saved before the 2026-10-28 revision store the 2026-10-23 grouping: DEFAULT as
`upheld_by_default` (no longer a value of `Adjudication`) and stipulation, admission,
cure and POP outcomes as `upheld`. The stored JSON is evidence and is never rewritten.

On load, the grouping a snapshot was saved under is recognized from what it stored: each
record's (group, note) for its raw hearing status, and the summary statement, compared
with the groupings committed so far (`adjudication_history` in the reference file). A
snapshot that matches no single grouping is refused, never guessed.

- Saved under the current grouping: returned unchanged.
- Saved under the 2026-10-23 grouping: the group and note of each record are derived
  again from the recorded raw hearing status with the current mapping, the summary's
  adjudication counts and notes are recounted, and the statement says so. What was
  stored is returned beside it (`AdjudicationReading.recorded`), so the historical
  classification and the new interpretation are never confused.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .ecb_models import WITHHELD_VALUE
from .ecb_violations import STATEMENT, classify_hearing, reference

LEGACY_NOTE = (
    " This snapshot was saved under the {version} grouping, which counted stipulation, "
    "admission, cure and POP outcomes as upheld and DEFAULT as upheld_by_default. The "
    "grouping shown here was derived again when the snapshot was loaded, from the recorded "
    "hearing status; the stored classification is unchanged and is kept beside it."
)


class UnrecognizedAdjudicationError(ValueError):
    """The stored groups match no committed grouping, or more than one."""


@dataclass(frozen=True)
class AdjudicationReading:
    """How the adjudication groups of a loaded ECB snapshot were obtained."""

    stored_version: str  # the grouping the snapshot was saved under
    current_version: str
    rederived: bool  # True: groups were derived again on load, not read as stored
    # Only when rederived: what was stored, unchanged.
    recorded: dict[str, Any] = field(default_factory=dict)


def _known() -> dict[str, list[dict]]:
    """version -> the groupings committed for it (current reference last)."""
    ref = reference()
    out: dict[str, list[dict]] = {}
    for v in ref["adjudication_history"]:
        out.setdefault(v["version"], []).append(v)
    out.setdefault(ref["adjudication_version"], []).append(
        {"adjudication": ref["adjudication"], "statement": STATEMENT}
    )
    return out


def _versions_of_record(rec: dict, known: dict[str, list[dict]]) -> set[str]:
    raw = rec.get("hearing_status_raw")
    if raw == WITHHELD_VALUE:  # an unlisted value: unknown in every grouping
        return set(known) if rec.get("adjudication") == "unknown" else set()
    key = "None" if raw is None else raw
    pair = (rec.get("adjudication"), rec.get("adjudication_note"))
    return {
        version
        for version, entries in known.items()
        for e in entries
        if key in e["adjudication"]
        and (e["adjudication"][key]["adjudication"], e["adjudication"][key]["note"]) == pair
    }


def stored_version(payload: dict) -> str:
    """The single committed grouping that every stored value agrees with."""
    known = _known()
    current = reference()["adjudication_version"]
    possible = set(known)
    for rec in payload.get("records") or []:
        possible &= _versions_of_record(rec, known)
    summary = payload.get("summary")
    if summary is not None:
        possible &= {
            v
            for v, entries in known.items()
            if any(e["statement"] == summary["statement"] for e in entries)
        }
    if not possible:
        raise UnrecognizedAdjudicationError(
            "the stored ECB adjudication groups match no committed grouping"
        )
    if current in possible:  # identical under the current grouping
        return current
    if len(possible) > 1:
        raise UnrecognizedAdjudicationError("the stored ECB groups match several groupings")
    return possible.pop()


def _sorted(c: Counter) -> dict[str, int]:
    return dict(sorted(c.items()))


def read_stored(payload: dict) -> tuple[dict, AdjudicationReading]:
    """(payload to validate, how its groups were obtained). Never changes `payload`."""
    version = stored_version(payload)
    current = reference()["adjudication_version"]
    if version == current:
        return payload, AdjudicationReading(version, current, rederived=False)

    out = copy.deepcopy(payload)
    recorded_records = []
    for rec in out.get("records") or []:
        recorded_records.append(
            {
                "isn_dob_bis_extract": rec.get("isn_dob_bis_extract"),
                "hearing_status_raw": rec.get("hearing_status_raw"),
                "adjudication": rec.get("adjudication"),
                "adjudication_note": rec.get("adjudication_note"),
            }
        )
        raw = rec.get("hearing_status_raw")
        if raw != WITHHELD_VALUE:
            adjudication, _, note = classify_hearing(raw)
            rec["adjudication"], rec["adjudication_note"] = adjudication.value, note

    recorded: dict[str, Any] = {"records": recorded_records}
    summary = out.get("summary")
    if summary is not None:
        recorded["summary"] = {
            "statement": summary["statement"],
            "adjudication_notes": summary.get("adjudication_notes"),
            "by_attribution": {
                a: {
                    "by_adjudication": b.get("by_adjudication"),
                    "by_correction_and_adjudication": b.get("by_correction_and_adjudication"),
                }
                for a, b in summary["by_attribution"].items()
            },
        }
        by_adj: dict[str, Counter] = {a: Counter() for a in summary["by_attribution"]}
        by_both: dict[str, Counter] = {a: Counter() for a in summary["by_attribution"]}
        notes: dict[str, str] = {}
        for rec in out["records"]:
            a = rec["attribution"]
            by_adj[a][rec["adjudication"]] += 1
            by_both[a][f"{rec['correction_status']}|{rec['adjudication']}"] += 1
            notes[rec.get("hearing_status_raw") or "None"] = rec["adjudication_note"]
        for a, b in summary["by_attribution"].items():
            b["by_adjudication"] = _sorted(by_adj[a])
            b["by_correction_and_adjudication"] = _sorted(by_both[a])
        summary["adjudication_notes"] = dict(sorted(notes.items()))
        summary["statement"] = STATEMENT + LEGACY_NOTE.format(version=version)
    return out, AdjudicationReading(version, current, rederived=True, recorded=recorded)


__all__ = [
    "LEGACY_NOTE",
    "AdjudicationReading",
    "UnrecognizedAdjudicationError",
    "read_stored",
    "stored_version",
]
