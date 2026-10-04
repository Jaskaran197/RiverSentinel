---
name: smoke
description: Call-free end-to-end check of the RiverSentinel app — lint, unit tests, Databricks connectivity, and a headless render of the Streamlit page stepping through real events. Use after changing timeline.py, logic.py, page.py, or the SQL/queries, or when asked to verify the app works.
---

Run these steps from the repo root and report pass/fail for each with a one-line reason. Never place a phone call in this skill — don't run `page.py` (other than `--setup`) or `test_twilio.py --call`.

1. **Lint + unit tests (offline):** `ruff check . && pytest -q`
2. **Databricks:** `python test_databricks.py --no-ai`. If the user asked to verify AI/verdict changes, run it without `--no-ai` too (slower; the first query may wait for the warehouse to wake up).
3. **App renders:** `python .claude/skills/smoke/app_check.py [N]` — runs `timeline.py` headlessly via Streamlit `AppTest` against real data (read-only) with `real_calls` and `live_mode` forced off, clicks "Next event" N times (default 3), and fails on any exception. Takes ~20-40 s. For UI changes that only appear later in the month or in a specific state, increase N or adapt the script's session_state setup.
4. **Voice config (optional, no call):** if the change touched `page.py` or voice code, run `python test_twilio.py` (credential check only).

Caveats to state in the report:
- AppTest proves the script runs and which elements exist; it doesn't show layout or styling. For visual UI changes, also look at it in a browser (Playwright plugin, if installed) at http://localhost:8501 after `streamlit run timeline.py --server.headless true`, with "Place real phone calls" off.
- Live mode falls back to stored verdicts on failure, so a passing render doesn't prove `ai_query` works — step 2 without `--no-ai` does.
