# Third-party notices

Our own source code is MIT licensed (see [LICENSE](LICENSE)). That license does not cover
external data or dependencies, which keep their own terms.

## Data sources

| Source | Provider | Terms |
|---|---|---|
| NYC Open Data (HPD violations, HPD complaints, PLUTO, Building Footprints, HPD buildings) | City of New York, via Socrata | NYC Open Data Terms of Use. Not yet reviewed: the page was unreachable from the build environment. Must be reviewed before public launch. |
| GeoSearch | NYC Planning Labs (Department of City Planning) | No license or terms text found on the docs page (checked 2026-10-21). Attribution requirements to confirm before launch. |

The app links to original records rather than rehosting them, and does not republish
personal information beyond what is needed to cite a record.

## Python dependencies (backend, locked in `backend/uv.lock`)

| Package | License |
|---|---|
| httpx, httpcore, idna, python-dotenv | BSD-3-Clause |
| pydantic, pydantic-core, pydantic-settings, anyio, h11, annotated-types, typing-inspection | MIT |
| typing-extensions | PSF-2.0 |
| certifi | MPL-2.0 |
| pytest, ruff (development only) | MIT |

| fastapi, langgraph, langchain-core, sqlalchemy, alembic | MIT |
| starlette, uvicorn | BSD-3-Clause |
| anthropic (optional LLM client) | Apache-2.0 |
| psycopg | LGPL-3.0-only (used unmodified as a library) |

## Frontend dependencies (locked in `frontend/package-lock.json`)

| Package | License |
|---|---|
| next, react, react-dom, tailwindcss | MIT |
| typescript, @playwright/test (development only) | Apache-2.0 |

Fonts Inter and Source Serif 4 are served via `next/font` under the SIL Open Font License.

Licenses were read from installed package metadata on 2026-10-21 (backend core) and
2026-11-22 (MVP additions).
