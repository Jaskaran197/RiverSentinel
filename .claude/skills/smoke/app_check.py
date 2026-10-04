"""Headless render of timeline.py via Streamlit AppTest: real (read-only) Databricks data, real calls and live AI forced off.
Usage: python .claude/skills/smoke/app_check.py [N]   # click ⏩ (next record) N times (default 3)"""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

steps = int(sys.argv[1]) if len(sys.argv) > 1 else 3
at = AppTest.from_file(str(Path(__file__).resolve().parents[3] / "timeline.py"), default_timeout=150)
at.session_state["real_calls"] = False      # never dial
at.session_state["live_mode"] = False       # stored results, no ai_decide/ai_query
at.run()
ok = not at.exception
print("idle render:", "ok" if ok else [e.value for e in at.exception])
for i in range(steps):
    next(b for b in at.button if b.label == "⏩").click().run()
    assert not at.session_state["real_calls"]
    errs = [e.value for e in at.exception]
    ok &= not errs
    print(f"event {i + 1}:", errs or "ok", "| metrics:", [m.value for m in at.metric])
html = " ".join(m.value for m in at.markdown)
print("decision card rendered:", 'class="hero' in html and 'class="flow"' in html)
sys.exit(0 if ok else 1)
