# MVP evaluation: deterministic baseline vs LangGraph workflow

Run: `cd backend && uv run python scripts/evaluate_reports.py` (offline, about 10 seconds).
Raw results: [`data/eval/mvp_evaluation.json`](../data/eval/mvp_evaluation.json).
Date: 2026-11-22.

## What was compared

Both arms run on the same evidence: the five recorded Phase 2 buildings (sanitized NYC Open
Data captured 21 to 23 October 2026) plus four resolver edge cases (ambiguous address, wrong
borough, nonsense text, address outside NYC).

| Arm | What it is |
|---|---|
| Baseline | The deterministic report: Python counts, fixed wording, fixed section order. |
| Agent (scripted) | The full LangGraph workflow (resolve, collect, baseline, select findings, plan, tools, write, verify). The model is **scripted**, because no LLM API key is configured. For each building it offers 1 faithful paragraph and 4 known-bad ones (invented number, unknown citation, safety verdict, order described as current, another building's record presented as this one). |

A scripted model measures the guardrails reproducibly. It does **not** measure how useful a
real model's prose is; that needs `LLM_ENABLED=1` and a key, and has not been run.

## Results

| Check | Result |
|---|---|
| Building identity matches the approved labels | 9 / 9 cases (5 verified, 2 ambiguous, 2 abstentions) |
| Records attributed to the wrong building | 0 (both arms) |
| Ground truth: complaint 2411142 (1135 Bryant Ave, BIN 2117584) kept off 1134 West Farms Rd | Yes; shown under "other buildings on this lot" |
| Citations pointing at a missing record | 0 of 6,533 checked |
| Count/sum claims re-computed from the cited records | 81 of 81 match |
| Partially retrieved or unavailable sources visibly flagged | Yes (311 at 1134 West Farms Rd and 1845 ACP Blvd are labelled partial; counts shown as lower bounds, e.g. "89+") |
| Order events presented as currently in effect | 0 |
| Unit/floor detail in free text, or 311 request numbers, in reports | 0 |
| Known-bad agent paragraphs that reached the report | 0 of 20 |
| Faithful agent paragraphs kept | 5 of 5 |
| LLM calls per investigation | at most 2 |
| Deterministic findings changed by the agent | none (the baseline stays the report of record) |
| Baseline latency, demo mode | under 1 s per address |

Live smoke check (same code, `DATA_MODE=live`, 2026-11-22): 1134 West Farms Rd and 530 2nd
Street (Brooklyn, not in the demo set) each completed in about 9 seconds with 26 HTTP requests,
and both reports passed verification. Live DOB complaints for BIN 2006185/2117584 returned 2
rows against 12 in the October recording; a direct count query against the dataset confirms
that the source itself changed. This is why every report shows each source's data date.

## What this does and does not show

- It shows the guardrails hold on the recorded buildings: identity, attribution, citations,
  historical-order wording, missing-data labelling and privacy.
- It does not show citywide accuracy. Five buildings and four edge cases are an acceptance
  test, not a benchmark.
- It does not measure whether a real LLM adds useful explanation over the baseline. With a key
  configured, the same script's agent arm can be pointed at the real provider.
- Phase 2 validation limits still apply: manual checks covered a risk-based sample, and open
  questions (for example DOB "served" versus "issued" wording, and whether any order is
  enforceable today) are shown as uncertain in every report rather than resolved.
