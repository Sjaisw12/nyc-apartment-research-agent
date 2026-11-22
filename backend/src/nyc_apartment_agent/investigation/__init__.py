"""Phase 3: one investigation workflow over the accepted Phase 1 resolver and Phase 2 records.

collect.py   resolve an address and gather every source (live, or replayed from fixtures)
schema.py    the stable report schema shared by the baseline, the agent, the API and exports
baseline.py  deterministic, fixed-order cited report (no LLM)
verify.py    checks every finding and every agent sentence against the evidence it cites
agent.py     bounded LangGraph workflow with the baseline as fallback
"""
