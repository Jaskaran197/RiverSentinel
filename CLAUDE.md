# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

RiverSentinel local demo: a Streamlit app (`timeline.py`) that replays river-gauge anomaly events from Databricks, re-runs the AI verdict live, and pages a duty officer by phone (`page.py`, ElevenLabs + Twilio). Decision rules and helpers live in `logic.py` (no Streamlit) so they can be unit-tested; UI code stays in `timeline.py`.

## Commands

- Run app: `streamlit run timeline.py` (port 8501). The user often has their own instance on 8501 — for browser checks run a separate one with `--server.headless true --server.port 8599`.
- Databricks check: `python test_databricks.py --no-ai` (drop `--no-ai` to also exercise `ai_decide`/`ai_query`)
- Twilio credential check (no call): `python test_twilio.py`
- Unit tests (offline, no network): `pytest -q`; single test: `pytest tests/test_logic.py -k finalize`. `tests/conftest.py` blocks all network access.
- Lint: `ruff check .` (`pip install ruff pytest`; config in `ruff.toml`). Lint only — don't run `ruff format`, the dense one-liner style is intentional.
- Headless app render: `python .claude/skills/smoke/app_check.py` (Streamlit AppTest, real data, calls + live AI off). `/smoke` runs everything.
- The root `test_*.py` files are manual go/no-go scripts, **not pytest** (pytest only collects `tests/`).

## Real phone calls — never run without asking

These dial real numbers and cost money. Always ask the user first:
- `page.py` with anything other than `--setup` (`--test`, `--station ...`, `--escalate`)
- `test_twilio.py --call ...`
- Letting the app's call countdown complete (if driving the UI)

The Dev Panel's "Place Call" toggle (`session_state["real_calls"]`) defaults off; keep it off when driving the UI (browser or AppTest). Running a scenario or a replay that reaches a PAGE decision starts a countdown — with Place Call on, it dials.

## Layout & gotchas

- The repo is **flat**. Docstrings saying `app/timeline.py`, `scripts/*.py`, or `SETUP_LOCAL.md` are stale — fix them to the flat paths when touching those files.
- No local or mock data. Everything reads Unity Catalog tables `{CATALOG}.silver.*` and `{CATALOG}.gold.*` (CATALOG defaults to `river`). They're built by a pipeline in a separate repo — treat schemas as fixed; don't invent columns.
- The LLM is Databricks-only, called via SQL `ai_decide(...)` and `ai_query(model, prompt, responseFormat=>...)` — no Anthropic/OpenAI SDK. The prompt and JSON schema live as constants in `timeline.py`.
- `logic.py` mirrors the SQL pipeline's routing/verdict/severity/action rules (`decide_route`, `finalize`) — changing them diverges from stored results; flag it rather than doing it silently. `timeline.py` imports `logic` *after* `load_env()` because `AUTO_RESOLVE_THRESHOLD` is read at import.
- "Live mode" re-runs the AI on stored inputs and silently falls back to stored results on failure (shown as a caption), so a working UI doesn't prove live calls work.
- Queries are cached `st.cache_data(ttl=900)`; the first query after idle pays the warehouse wake-up time.
- Playback uses `time.sleep` + `st.rerun()` loops (`SETTLE_S`, `REVEAL_S`) — keep that pacing in mind before adding blocking work.
- Everything displayed is in a hardcoded `MDT` (UTC-6); data and `session_state.t` stay UTC-aware. (If Plotly charts come back: Plotly has no time zones and can't parse offsets in shapes — pass naive MDT wall times.)
- `page.py` is stdlib-only (keep it that way); `timeline.py` imports it as `pager`, looking in `.` then `../scripts`.
- `load_env()` is duplicated in all four files: it reads `.env` from the script dir, its parent, grandparent, or cwd, and never overrides existing env vars.

## UI conventions (timeline.py)

- Most of the UI is hand-written HTML via `st.markdown(..., unsafe_allow_html=True)`, styled by the single `<style>` block near the top. Streamlit buttons can't live inside that HTML.
- Visual language "Nocturne" (main dashboard only; the previous "Watermark" look is in git at df0a5c3): CSS variables on `:root` (`--night --surface --raised --cream --fog --dim --line --signal --water --amber --glow`; fonts `--display` Syne / `--serif` Instrument Serif / `--mono` Space Mono / `--sans` Inter Tight, loaded via `@import` from Google Fonts) mirrored by Python constants (`NIGHT`, `CREAM`, `SIGNAL`, `WATER`, `AMBER`, `VERDICT_COLOR`, `VERDICT_TEXT`) and `system_map.C`. Signal red = "look here now" (selected, active, calls); periwinkle = water. Use the tokens, not new hex values; don't let main-dashboard CSS leak into the sidebar. Keep the dashboard within a 900px-tall window in replay.
- Process flow (below the map): `flow_html()` renders the six stops; `run_live()` is the **only** place the models are re-run (once per record, live mode or scenario), painting stops as results arrive and caching them in `ss.live_results`; `call_controls()` owns Cancel/Call now/Dismiss and placing the call; its buttons and "More information" sit in an `st.columns(6)` row (key `flow_buttons*`) lifted into the bottom of the Act/Verdict cards — the flow grid's 16px gap matches `gap="small"`, keep them in sync. A new record shows the `pending` stage (Detect only) until revealed, so answers never appear early. The reasoning lives in an `st.dialog` (`flow_info_dialog`). `flow_ev`/`fresh` are computed once.
- Replay reveals are timed CSS (`--d` delays: the `delay` list in `flow_html`); `REVEAL_S` holds off reruns until they finish — keep the last delay + duration under it. Streamlit replaces markdown HTML on every rerun, so any CSS animation restarts unless it is gated on `fresh`.
- User-facing text is plain language via the `*_WORD` / `ACTION_*` maps; never show raw enum or column names.
- Streamlit 1.65 deprecates `use_container_width`; use `width="stretch"`.
- Dev Panel (sidebar): System/Station filter → `lanes`/`ev_lanes`, which every dashboard section uses; playback; scenarios (`scenarios.py` presets → `logic.scenario_event`, shown in the process flow). All playhead moves go through `seek()` (handles `nav()`, re-arming calls when moving back, leaving a scenario). Panel widgets are keyed (`speed_min`, `live_mode` (Live Model, default off), `real_calls`, `countdown_s`, `system`, `stations_<system>`) — read them from `session_state`, don't pass `value=` too.
- `player.py` is an `st.components.v2` component (inline HTML/CSS/JS): SVG transport buttons + seek bar. Its JS is re-invoked on every data change, so DOM/state persist on `parentElement`; its triggers (`action`, `seek`) are handled in on-change callbacks, which run *before* the script, so the page renders the new state in the same rerun. AppTest can't drive it — `app_check.py` sets `session_state.t` instead; verify clicks in a browser.
- Main dashboard, top to bottom: title + brand/clock, system map + summary cards, process flow. `latest` (current record: newest seen, or the one a pending call belongs to) is computed once and shared by the map and the process flow.
- `system_map.py` is a second `st.components.v2` component: MapLibre GL (CDN script, `window.maplibregl`) with our own monochrome style over OpenFreeMap vector tiles + AWS Terrain Tiles hillshade — needs internet, no API keys. The view fits the system once and refits only on a system change (never recentres on the current station); pan/zoom disabled. The current station (crimson, pulsing) and its 3 nearest neighbours from `nearby_ids` (azure) get panels inside the map's right edge; the fit keeps a `GUTTER`-wide band there clear of stations (right fit padding), so the stations sit left of centre. Panel stacking (`arrange()`/`stack()` in the component JS) is pure and property-tested under Node in `tests/test_map_layout.py` (no overlap; selected stays centred unless nothing fits, then moves minimally; nothing off the map if the stack fits) — keep those functions pure and rerun the test when changing them. Streamlit doesn't reliably hot-reload edited component modules — restart the server after editing one.
- The Dev Panel is deliberately styled unlike the dashboard (dark, monospace, square, mint accent `#7ee0b5`): all its CSS is scoped to `section[data-testid="stSidebar"]`, and the portalled dropdown menus are styled only via `body:has(<sidebar combobox>[aria-expanded="true"])`. Streamlit 1.65 widget markup: selects are `[role=group]:has(input[role=combobox])`, toggles are `label > span(input) + div(track) > div(knob)`. Check new panel widgets in a browser — they arrive with the light theme.

## Env

`.env.example` lists every variable (copy to `.env`, which is gitignored). Never print or commit `.env` values.
