"""Headless render of timeline.py via Streamlit AppTest: real (read-only) Databricks data, real calls and live AI forced off.
AppTest can't click inside the custom player component, so it moves the playhead through session_state instead.
Usage: python .claude/skills/smoke/app_check.py [N]   # render at N evenly spaced points in the month (default 3)"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

MDT = timezone(timedelta(hours=-6))
T0, T1 = datetime(2026, 9, 1, tzinfo=MDT), datetime(2026, 10, 2, tzinfo=MDT)
steps = int(sys.argv[1]) if len(sys.argv) > 1 else 3
at = AppTest.from_file(str(Path(__file__).resolve().parents[3] / "timeline.py"), default_timeout=150)
at.session_state["real_calls"] = False      # never dial
at.session_state["live_mode"] = False       # stored results, no ai_decide/ai_query
at.run()
ok = not at.exception
print("idle render:", "ok" if ok else [e.value for e in at.exception])
cards = 0
for i in range(1, steps + 1):
    at.session_state["t"] = T0 + (T1 - T0) * i / (steps + 1)
    at.session_state["nav_at"] = 0.0         # already settled: no settle-timer reruns
    at.run()
    assert not at.session_state["real_calls"]
    errs = [e.value for e in at.exception]
    ok &= not errs
    html = " ".join(m.value for m in at.markdown)
    cards += 'class="hero' in html and 'class="flow"' in html
    print(f"t={at.session_state['t']:%b %d %H:%M}:", errs or "ok", "| metrics:", [m.value for m in at.metric])
print(f"decision card rendered at {cards}/{steps} points")
sys.exit(0 if ok else 1)
