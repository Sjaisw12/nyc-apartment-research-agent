# NYC Apartment Research Agent

A web app that helps NYC renters investigate an apartment building before signing a lease.

Enter an NYC address and the system will:

1. Resolve the correct building (BBL/BIN).
2. Collect official HPD, DOB, and 311 complaints and violations, plus registration and ownership records.
3. Run a bounded LangGraph agent to follow up on important findings.
4. Check factual claims against the source records.
5. Produce a cited report that separates verified findings from allegations and from information that is uncertain or unavailable.

This is not legal advice or an automated lease approval tool.

## Status

**Phase 1 (in review):**
- Data-source smoke tests are done.
- The address resolver is implemented. It returns one of four outcomes: verified (at building or tax-lot level), ambiguous, no reliable match, or source unavailable.
- On the 24-address benchmark (see `data/eval/benchmark_results.md`):
  - 14/14 correct automatic matches
  - 0 wrong-building matches
  - 1 right building but wrong outcome class (A09)
  - 5/6 ambiguous cases handled
  - 4/4 correct abstentions
  - 2/2 unresolved BINs preserved

  This is a small acceptance test, not evidence of citywide accuracy.

## Repository layout

```
backend/                     Python package (FastAPI service arrives in a later task)
  src/nyc_apartment_agent/
    config.py                settings from environment variables / .env
    errors.py                typed errors for every way a source can fail
    http_client.py           timeouts, bounded retries for 429/5xx, provenance headers
    identifiers.py           BBL/BIN validation, placeholder-BIN detection
    sources/                 one adapter per external source (GeoSearch, HPD, Footprints)
    resolver/                address parsing, per-candidate checks, decision rules
    cassette.py              record live HTTP responses once, replay them offline
  scripts/
    smoke_sources.py         live check; saves hashed, timestamped fixtures
    collect_address_evidence.py  evidence for the test-address set
    evaluate_resolver.py     24-address benchmark (offline replay, or --record live)
  tests/
    unit/                    offline tests (default)
    live/                    real-API tests (opt in)
    fixtures/                real responses captured by smoke_sources.py
data/eval/                   draft test addresses, evidence, review notes
docs/                        source registry and investigations
```

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env            # optional: add SOCRATA_APP_TOKEN
cd backend
uv sync                         # installs locked dependencies
uv run pytest                   # offline tests, no network needed
RUN_LIVE_TESTS=1 uv run pytest tests/live -v   # hits the real NYC APIs
uv run python scripts/smoke_sources.py         # refresh fixtures + smoke report
uv run python scripts/evaluate_resolver.py     # benchmark, offline from recorded responses
```

## Planned stack

- **Agent:** LangGraph
- **Backend:** Python, FastAPI, Pydantic, PostgreSQL
- **Frontend:** Next.js, TypeScript, Tailwind CSS
- **Hosting:** Vercel (frontend), Render or Railway (backend)
- **Quality:** pytest, Playwright, Docker, GitHub Actions

## License

Source code: MIT, see [LICENSE](LICENSE). Data and dependencies keep their own terms, see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
