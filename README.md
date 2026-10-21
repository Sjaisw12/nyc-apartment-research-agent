# NYC Apartment Research Agent

A web app that helps NYC renters investigate an apartment building before signing a lease.

Enter an NYC address and the system will:

1. Resolve the correct building (BBL/BIN).
2. Collect official HPD, DOB, and 311 complaints and violations, plus registration and ownership records.
3. Run a bounded LangGraph agent to follow up on important findings.
4. Check factual claims against the source records.
5. Produce a cited report that separates verified findings from allegations and from information that is uncertain or unavailable.

This is not legal advice or an automated lease approval tool.

## Planned stack

- **Agent:** LangGraph
- **Backend:** Python, FastAPI, Pydantic, PostgreSQL
- **Frontend:** Next.js, TypeScript, Tailwind CSS
- **Hosting:** Vercel (frontend), Render or Railway (backend)
- **Quality:** pytest, Playwright, Docker, GitHub Actions

## Status

Phase 1 (data source verification and building resolution) is in progress. Development follows a seven-phase roadmap, and each phase is implemented, tested, and verified before the next one starts.
