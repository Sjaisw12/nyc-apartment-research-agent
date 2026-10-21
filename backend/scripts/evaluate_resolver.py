"""Run the address resolver on the labelled benchmark and report metrics separately.

    uv run python scripts/evaluate_resolver.py --record   # live; saves a cassette
    uv run python scripts/evaluate_resolver.py            # offline replay of the cassette

Outputs data/eval/benchmark_results.json and data/eval/benchmark_results.md.
Metrics are never blended into one accuracy number (see docs/resolver_design.md, section 7).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from nyc_apartment_agent.cassette import (
    RecordingTransport,
    ReplayTransport,
    load_cassette,
    save_cassette,
)
from nyc_apartment_agent.resolver import ResolutionLevel, resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

REPO = Path(__file__).resolve().parents[2]
BENCHMARK = REPO / "data" / "eval" / "candidate_addresses_draft.csv"
CASSETTE = REPO / "backend" / "tests" / "fixtures" / "benchmark" / "resolver_cassette.json"
OUT_JSON = REPO / "data" / "eval" / "benchmark_results.json"
OUT_MD = REPO / "data" / "eval" / "benchmark_results.md"


# Extra inputs recorded into the same cassette for offline regression tests (not graded).
EXTRA_REGRESSION_INPUTS = [
    "530 2nd Street",  # no borough: must be inferred as Brooklyn
    "99999 Broadway",  # no borough, nonexistent number: all five borough checks find nothing
    "530 2nd Street, Brooklyn, NY 10001",  # ZIP contradicts the PAD record
    # Unseen Queens cases for the hyphenation policy (not part of the labelled benchmark):
    "7217 34th Avenue, Queens",  # official form 72-17; must ask
    "7217 34th Avenue",  # same, no borough
    "2705 1st Street, Queens",  # official form 27-05; must ask
    "72-17 34th Avenue, Queens",  # correctly hyphenated: verified
    "311 Beach 32nd Street, Queens",  # genuinely hyphenless Rockaway number: verified
    "250 Beach 15th Street, Queens",  # genuinely hyphenless: verified
]


def load_rows() -> list[dict]:
    return list(csv.DictReader(BENCHMARK.open(encoding="utf-8")))


def grade(row: dict, result) -> dict:
    expected = row["expected_behavior"]
    exp_bbls = [b for b in row["proposed_bbl"].split("|") if b]
    exp_bins = [b for b in row["proposed_bin"].split("|") if b]
    got = result.outcome.value
    g = {
        "id": row["id"],
        "query": row["query"],
        "expected": expected,
        "got": got,
        "expected_bbl": exp_bbls,
        "expected_bin": exp_bins,
        "explanation": result.explanation,
        "reasons": [r.value for r in result.reasons],
    }
    if result.building:
        g.update(
            bbl=result.building.bbl,
            bin=result.building.bin,
            bin_evidence=result.building.bin_evidence.value,
            resolution_level=result.resolution_level.value,
        )
    g["options"] = [{"bbl": o.bbl, "bin": o.bin} for o in result.options]

    if got == "source_unavailable":
        g["category"] = "source_unavailable"
    elif got == "verified":
        right_bbl = result.building.bbl in exp_bbls
        right_bin = (result.building.bin in exp_bins) if exp_bins else result.building.bin is None
        if expected == "verified" and right_bbl and right_bin:
            g["category"] = "correct_auto_match"
        elif right_bbl and right_bin:
            g["category"] = "incorrect_auto_match_right_building"
        else:
            g["category"] = "incorrect_auto_match_wrong_building"
    elif got == "ambiguous":
        option_bbls = {o.bbl for o in result.options}
        if expected == "ambiguous":
            ok = all(b in option_bbls for b in exp_bbls)
            g["category"] = "ambiguity_handled" if ok else "ambiguous_missing_option"
        elif expected == "verified":
            g["category"] = "over_cautious"
        else:
            g["category"] = "ambiguous_instead_of_abstain"
    else:  # no_reliable_match
        g["category"] = (
            "correct_abstention" if expected == "no_reliable_match" else "missed_" + expected
        )

    # Unresolved-BIN handling: rows whose label has a BBL but no BIN.
    if exp_bbls and not exp_bins and expected in ("verified", "ambiguous"):
        bin_kept_null = (
            result.building.bin is None
            if result.building
            else all(o.bin is None for o in result.options if o.bbl in exp_bbls)
        )
        not_building = result.resolution_level is not ResolutionLevel.BUILDING
        g["unresolved_bin_preserved"] = bool(bin_kept_null and not_building)
    return g


def summarize(graded: list[dict]) -> dict:
    def count(cat: str) -> int:
        return sum(1 for g in graded if g["category"] == cat)

    n_ver = sum(1 for g in graded if g["expected"] == "verified")
    n_amb = sum(1 for g in graded if g["expected"] == "ambiguous")
    n_nrm = sum(1 for g in graded if g["expected"] == "no_reliable_match")
    unresolved = [g for g in graded if "unresolved_bin_preserved" in g]
    return {
        "rows": len(graded),
        "correct_auto_matches": f"{count('correct_auto_match')}/{n_ver}",
        "incorrect_auto_matches_wrong_building": count("incorrect_auto_match_wrong_building"),
        "incorrect_auto_matches_right_building_but_expected_non_verified": count(
            "incorrect_auto_match_right_building"
        ),
        "ambiguity_handled": f"{count('ambiguity_handled')}/{n_amb}",
        "ambiguous_missing_expected_option": count("ambiguous_missing_option"),
        "correct_abstentions": f"{count('correct_abstention')}/{n_nrm}",
        "over_cautious": count("over_cautious"),
        "missed_or_other": sum(
            1
            for g in graded
            if g["category"].startswith("missed_")
            or g["category"] == "ambiguous_instead_of_abstain"
        ),
        "source_unavailable": count("source_unavailable"),
        "unresolved_bin_preserved": f"{sum(g['unresolved_bin_preserved'] for g in unresolved)}"
        f"/{len(unresolved)}",
    }


def write_reports(graded: list[dict], summary: dict, mode: str) -> None:
    OUT_JSON.write_text(
        json.dumps(
            {
                "run_at": datetime.now(UTC).isoformat(),
                "mode": mode,
                "summary": summary,
                "rows": graded,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = (
        [
            "# Resolver benchmark results",
            "",
            f"Mode: **{mode}** · rows: {summary['rows']} · generated by "
            "`backend/scripts/evaluate_resolver.py`.",
            "",
            "**Caveat:** 24 hand-picked addresses form a small acceptance test, not evidence of "
            "citywide accuracy.",
            "",
            "| Metric | Result |",
            "|---|---|",
        ]
        + [f"| {k.replace('_', ' ')} | {v} |" for k, v in summary.items() if k != "rows"]
        + [
            "",
            "| ID | Expected | Got | Category | BBL / BIN returned | Explanation |",
            "|---|---|---|---|---|---|",
        ]
    )
    for g in graded:
        ids = (
            f"{g.get('bbl')} / {g.get('bin')} ({g.get('resolution_level')}, "
            f"{g.get('bin_evidence')})"
            if g["got"] == "verified"
            else "; ".join(f"{o['bbl']} / {o['bin']}" for o in g["options"]) or "none"
        )
        lines.append(
            f"| {g['id']} | {g['expected']} | {g['got']} | {g['category']} | {ids} | "
            f"{g['explanation'].replace('|', '/')} |"
        )
    ok = {"correct_auto_match", "ambiguity_handled", "correct_abstention"}
    review = [g for g in graded if g["category"] not in ok]
    lines += ["", "## Rows needing review", ""]
    lines += [
        f"- **{g['id']}** expected `{g['expected']}`, got `{g['got']}` ({g['category']})."
        for g in review
    ] or ["None."]
    lines += [
        "",
        "## Policy history",
        "",
        "- **v1 (2026-10-21, before the Queens hyphenation policy):** 14/14 correct auto-matches, "
        "0 wrong-building, **1 right-building auto-match on an expected-ambiguous row (A09)**, "
        "5/6 ambiguity handled, 4/4 abstentions, 2/2 unresolved BINs. Preserved unchanged in "
        "`benchmark_results_v1_2026-10-21.md` / `.json`.",
        "- **v2 (2026-10-21):** a hyphenless Queens number that PAD lists only as an alternate "
        "form of an official hyphenated number on the same lot now needs confirmation "
        "(`QUEENS_NUMBER_NEEDS_HYPHEN`). Genuinely hyphenless Queens numbers are still verified. "
        "No labels were changed.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(record: bool) -> tuple[list[dict], dict, list[str]]:
    rows = load_rows()
    if record:
        transport = RecordingTransport()
    else:
        transport = ReplayTransport(load_cassette(CASSETTE))
    graded = []
    with geosearch_client(transport=transport) as gs, socrata_client(transport=transport) as sc:
        for row in rows:
            graded.append(grade(row, resolve_address(row["query"], gs, sc)))
        if record:
            for text in EXTRA_REGRESSION_INPUTS:
                resolve_address(text, gs, sc)
    if record:
        save_cassette(CASSETTE, transport.entries)
    misses = [] if record else transport.misses
    return graded, summarize(graded), misses


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true", help="call live APIs and save cassette")
    args = parser.parse_args()
    graded, summary, misses = run(args.record)
    write_reports(graded, summary, "live (recorded)" if args.record else "offline replay")
    for g in graded:
        print(f"{g['id']:4} expected={g['expected']:18} got={g['got']:18} {g['category']}")
    print(json.dumps(summary, indent=2))
    if misses:
        print("cassette misses:", *misses, sep="\n  ")
    return 1 if summary["incorrect_auto_matches_wrong_building"] or misses else 0


if __name__ == "__main__":
    sys.exit(main())
