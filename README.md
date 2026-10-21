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

**Phase 1 is implemented and awaiting review.** It covers data-source smoke tests, an address resolver, a FastAPI `POST /resolve` endpoint, and offline CI.

The resolver returns one of four typed outcomes: `verified` (at `building` or `tax_lot` level), `ambiguous`, `no_reliable_match`, or `source_unavailable`.

24-address benchmark (v2, see `data/eval/benchmark_results.md`):

| Measure | Result |
|---|---|
| Correct automatic matches | 14/14 |
| Incorrect automatic matches | 0 |
| Ambiguous cases handled | 6/6 |
| Correct abstentions | 4/4 |
| Unresolved BINs preserved | 2/2 |

These 24 addresses are a small acceptance test, not evidence of citywide accuracy.

## API

```bash
cd backend
uv run uvicorn nyc_apartment_agent.api.app:app --port 8000
curl -X POST localhost:8000/resolve -H 'content-type: application/json' \
     -d '{"address": "1134 West Farms Rd, Bronx, NY 10459"}'
```

| Response | When |
|---|---|
| 200 | `verified`, `ambiguous` or `no_reliable_match` |
| 503 | `source_unavailable` (same typed body, plus `Retry-After`) |
| 422 | Invalid request; submitted values are not echoed |
| 500 | Unexpected error, with no internal details |

`GET /health` is a liveness check that makes no external calls. Interactive docs are at `/docs`. Each verified result includes `resolution_level` and `permitted_join_keys`: building-level results allow joining on `bbl` and `bin`, tax-lot results on `bbl` only.

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
    api/                     FastAPI app: POST /resolve, GET /health
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

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request, using the locked dependencies. It runs lint, a format check, the offline test suite, and the 24-address benchmark replayed from recorded responses. It needs no NYC API access and no secrets.

## Planned stack

- **Agent:** LangGraph
- **Backend:** Python, FastAPI, Pydantic, PostgreSQL
- **Frontend:** Next.js, TypeScript, Tailwind CSS
- **Hosting:** Vercel (frontend), Render or Railway (backend)
- **Quality:** pytest, Playwright, Docker, GitHub Actions

## License

Source code: MIT, see [LICENSE](LICENSE). Data and dependencies keep their own terms, see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
