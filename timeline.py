"""
RiverSentinel — river gauge watch, replayed.

Pick a moment in September. The screen shows only what the system knew then: which gauges reported something odd,
what it decided each one was (bad sensor, dam change, real river event), what it did about it, and — when it decides to call someone — a short
countdown during which a human can cancel before the real call is placed. Step forward one event at a time, or press Play.

Run:   streamlit run app/timeline.py
Voice: on_page() is the hook — wire scripts/page.py there when adding the phone call.
"""
from __future__ import annotations
import json, os, time
from datetime import datetime, timedelta
from pathlib import Path

import sys, threading
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# the phone module (scripts/page.py). Optional: without it, or without ElevenLabs/Twilio settings, calls are simulated.
_here = Path(__file__).resolve().parent
for _cand in (_here, _here.parent / "scripts"):
    if (_cand / "page.py").exists():
        sys.path.insert(0, str(_cand)); break
try:
    import page as pager
except Exception:
    pager = None

# ----------------------------------------------------------------------------- env
def load_env():
    here = Path(__file__).resolve().parent
    for p in (here / ".env", here.parent / ".env", here.parent.parent / ".env", Path.cwd() / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return
load_env()
CATALOG = os.environ.get("CATALOG", "river")

# decision rules + helpers (no Streamlit); imported after load_env() because logic reads the environment
from logic import (MDT, derive, has, fmt, _safe_json, _ids, _nearby, neighbour_pcts, AUTO_RESOLVE_THRESHOLD, NEIGHBOUR_MOVE_PCT,
                   decide_route, finalize, pct, nice_name, humanize, scenario_event, route_with_reason, gate_from_probs)
from scenarios import SCENARIOS
from player import player
from system_map import system_map

def mdt(x):
    """UTC time(s) → naive MDT wall-clock time for charts (Plotly has no time zones and can't parse offsets in shapes)."""
    return x.dt.tz_convert(MDT).dt.tz_localize(None) if isinstance(x, pd.Series) else pd.Timestamp(x).tz_convert(MDT).tz_localize(None)

# ----------------------------------------------------------------------------- words (plain language for everything the tables say)
VERDICT_WORD = {"SENSOR_FAULT": "Bad sensor", "NATURAL_EVENT": "Real river event", "OPERATIONAL_CHANGE": "Dam or operator change",
                "INCONCLUSIVE": "Not sure", "PENDING": "Still looking"}
ACTION_WORD = {"PAGE": "Called the on-call person", "QUARANTINE": "Flagged the readings as bad", "TICKET": "Sent a technician ticket",
               "WATCH": "Kept watching", "LOG": "Noted it, no action"}
SEVERITY_WORD = {"DANGER_TO_LIFE": "danger to life", "SIGNIFICANT": "serious", "MINOR": "minor", "NONE": ""}
RULE_WORD = {"PHYSICAL_BREACH": "a reading outside what is physically possible", "SPIKE": "a single reading that jumped and came back",
             "FLATLINE": "a reading stuck on one value", "STEP": "a sudden step up or down", "GAP": "the gauge stopped reporting",
             "RAPID_RISE": "a fast rise"}
ROUTE_WORD = {"AUTO_RESOLVE": "clear-cut, decided instantly", "INVESTIGATE": "needed a closer look"}
WHO_WORD = {"GATE": "quick check", "AGENT_SQL": "closer look (AI reasoning)"}

# palette (validated reference palette; fixed slots)
SURFACE, GRID, AXIS, INK, INK2, MUTED = "#fcfcfb", "#e1e0d9", "#c3c2b7", "#0b0b0b", "#52514e", "#898781"
VERDICT_COLOR = {"SENSOR_FAULT": "#eb6834", "NATURAL_EVENT": "#2a78d6", "OPERATIONAL_CHANGE": "#898781",
                 "INCONCLUSIVE": "#eda100", "PENDING": "#c3c2b7"}
ACTION_SYMBOL = {"PAGE": "star", "QUARANTINE": "x", "TICKET": "square", "WATCH": "diamond", "LOG": "circle"}

st.set_page_config(page_title="RiverSentinel", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
[data-testid="stMainBlockContainer"]{padding:1.25rem 2rem 2rem 2rem;max-width:none}   /* full width */
/* no Streamlit toolbar (Deploy, menu, running status); the header stays only for the collapsed sidebar's expand button */
[data-testid="stHeader"]{background:transparent;height:0;min-height:0;pointer-events:none}
[data-testid="stAppDeployButton"], [data-testid="stMainMenu"], [data-testid="stStatusWidget"], [data-testid="stDecoration"]{display:none !important}
[data-testid="stExpandSidebarButton"]{pointer-events:auto;transform:translateY(10px)}
.card{border:1px solid #e1e0d9;border-radius:8px;padding:14px 16px;margin-bottom:10px;background:#fcfcfb}
.card h4{margin:0 0 6px 0;font-size:1.02rem}
.card .row{margin:3px 0;color:#0b0b0b}
.card .k{color:#52514e;display:inline-block;min-width:170px}
.card .why{color:#52514e;margin-top:8px;font-size:.93rem}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;color:#fff;font-size:.82rem;margin-left:6px;vertical-align:middle}
.stMetric label{color:#52514e}
@keyframes slidein {from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:none}}
@keyframes pulse {0%{box-shadow:0 0 0 0 rgba(42,120,214,.45)} 70%{box-shadow:0 0 0 12px rgba(42,120,214,0)} 100%{box-shadow:0 0 0 0 rgba(42,120,214,0)}}
.hero{border-left:6px solid #898781;padding:4px 0 4px 14px;margin:2px 0 6px 0}
.fresh{animation:slidein .45s ease-out}
.hero .when{color:#52514e;font-size:.9rem}
.hero .where{font-size:1.35rem;font-weight:650;margin:2px 0 6px 0;color:#0b0b0b}
.hero .odd{color:#0b0b0b;margin-bottom:8px}
.verdict{display:inline-block;padding:4px 12px;border-radius:999px;color:#fff;font-weight:650;font-size:1rem;background:#898781}
.flow{display:flex;align-items:stretch;gap:8px;margin-top:4px}
.step{flex:1;border:1px solid #e1e0d9;border-radius:8px;padding:10px 12px;background:#fff;min-width:0}
.step .t{font-size:.78rem;letter-spacing:.04em;text-transform:uppercase;color:#898781;margin-bottom:2px}
.step .v{font-weight:600;color:#0b0b0b}
.step .s{color:#52514e;font-size:.88rem}
.arrow{align-self:center;color:#c3c2b7;font-size:1.3rem}
.bar{display:flex;align-items:center;gap:8px;margin:3px 0;font-size:.86rem;color:#52514e}
.bar .lab{width:92px;flex:none}
.bar .tr{flex:1;height:8px;background:#efeeea;border-radius:4px;overflow:hidden}
.bar .fl{height:100%;border-radius:4px}
.bar .pc{width:36px;text-align:right;flex:none;color:#0b0b0b}
.acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.act{display:flex;align-items:center;gap:12px;border:1px solid #e1e0d9;border-radius:8px;padding:10px 14px;background:#fff;flex:1 1 220px;max-width:100%}
.act.big{border-width:1.5px}
.act svg{flex:none}
.act .at{font-weight:600;color:#0b0b0b}
.act .as{color:#52514e;font-size:.86rem}
.ring{width:54px;height:54px;border-radius:50%;background:#efeeea;display:grid;place-items:center;flex:none}
.ring span{width:42px;height:42px;border-radius:50%;background:#fff;display:grid;place-items:center;font-weight:700;color:#0b0b0b}
.why{margin-top:10px;color:#0b0b0b;border-left:3px solid #c3c2b7;padding:6px 10px;background:#fff;border-radius:4px;font-style:italic}
.whyhead{font-style:normal;font-size:.74rem;letter-spacing:.04em;text-transform:uppercase;color:#898781;margin-bottom:3px}
.model{display:inline-block;font-weight:500;color:#52514e;background:#efeeea;border-radius:4px;padding:0 6px;margin:0 0 6px 0;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.74rem}
.chip{display:inline-block;border:1px solid #e1e0d9;border-radius:999px;padding:2px 10px;margin:4px 6px 0 0;font-size:.84rem;color:#52514e;background:#fff}
.idle{display:flex;align-items:center;gap:14px;border:1px dashed #c3c2b7;border-radius:10px;padding:18px;color:#52514e;background:#fcfcfb}
.dot{width:12px;height:12px;border-radius:50%;background:#2a78d6;animation:pulse 1.8s infinite}

/* ---- staged reveal of the decision pipeline (plays once when a new decision arrives) ---- */
@keyframes appear {from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:none}}
@keyframes grow {from{transform:scaleX(0)} to{transform:scaleX(1)}}
@keyframes fadeout {to{opacity:0;visibility:hidden}}
@keyframes reveal {from{clip-path:inset(0 100% 0 0)} to{clip-path:inset(0 0 0 0)}}
@keyframes blink {0%,80%,100%{opacity:.2} 40%{opacity:1}}
.step{position:relative}
.play .s1{opacity:0;animation:appear .35s ease-out forwards}
.play .s1 + .arrow, .play .s2{opacity:0;animation:appear .35s ease-out .45s forwards}
.play .s2 .fl{transform-origin:left;transform:scaleX(0);animation:grow .5s ease-out .75s forwards}
.play .s2 .verdictline{opacity:0;animation:appear .3s ease-out 1.15s forwards}
.play .s2 + .arrow, .play .s3{opacity:0;animation:appear .35s ease-out 1.4s forwards}
.play .s3 .answer{opacity:0;animation:appear .4s ease-out 3.3s forwards}
.play .s3 .think{animation:fadeout .3s ease-out 3.2s forwards}
.think{position:absolute;inset:0;background:#fff;border-radius:8px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#52514e;font-size:.9rem;gap:6px}
.dots span{display:inline-block;width:7px;height:7px;margin:0 2px;border-radius:50%;background:#52514e;animation:blink 1.1s infinite}
.dots span:nth-child(2){animation-delay:.2s} .dots span:nth-child(3){animation-delay:.4s}
.play .s3 + .arrow, .play .s4, .play.quick .s2 + .arrow, .play.quick .s4{opacity:0;animation:appear .4s ease-out forwards}
.play .s4{animation-delay:3.7s} .play .s3 + .arrow{animation-delay:3.7s}
.play.quick .s4, .play.quick .s2 + .arrow{animation-delay:1.3s}
.play .acts{opacity:0;animation:appear .45s ease-out 4.1s forwards}
.play.quick .acts{animation-delay:1.7s}
.play .why{opacity:0;animation:appear .5s ease-out 3.4s forwards}
.play .why .txt{clip-path:inset(0 100% 0 0);animation:reveal 1.6s linear 3.5s forwards}
.play.quick .why{animation-delay:1.5s} .play.quick .why .txt{animation-delay:1.6s;animation-duration:.8s}
.play .chips{opacity:0;animation:appear .4s ease-out 4.9s forwards}
.play.quick .chips{animation-delay:2.1s}
.lat{float:right;font-size:.72rem;color:#898781;font-family:ui-monospace,Menlo,Consolas,monospace}
.pop{animation:appear .35s ease-out}
.livenote{margin-top:8px;font-size:.76rem;color:#898781}

/* ---- dashboard header (new) ---- */
.dash-head{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin:0 0 14px 0}
.dash-title{font-size:1.65rem;font-weight:650;color:#0b0b0b;line-height:1.2}
.dash-title .chev{color:#c3c2b7;margin:0 .4em;font-weight:400}
.dash-brand{text-align:right;flex:none}
.dash-brand .logo{font-size:1.25rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#0b0b0b}
.dash-brand .clock{font-size:.95rem;color:#52514e;margin-top:2px;font-variant-numeric:tabular-nums}
.legacy-sep{margin:72px 0 28px 0;padding-top:10px;border-top:2px dashed #e1e0d9;color:#898781;font-size:.78rem;letter-spacing:.06em;text-transform:uppercase}

/* ---- summary cards (beside the map) ---- */
.stats{display:flex;flex-direction:column;gap:8px}
.stat{flex:1;position:relative;display:flex;align-items:center;gap:12px;border:1px solid #e1e0d9;border-radius:6px;background:#fff;padding:8px 12px;min-height:0}
.stat .ic{flex:none;width:38px;height:38px;border-radius:50%;background:#f4f3ef;display:grid;place-items:center}
.stat .ic svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.stat .tx{min-width:0}
.stat .lb{font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:#52514e}
.stat .vl{font-size:1.55rem;font-weight:650;line-height:1.15;color:#0b0b0b;font-variant-numeric:tabular-nums}
.stat .sb{font-size:.78rem;color:#898781;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stat .tip{position:absolute;top:7px;right:8px;width:16px;height:16px;border-radius:50%;border:1px solid #c3c2b7;color:#898781;font-size:.66rem;
           line-height:14px;text-align:center;cursor:help;outline:none}
.stat .tip:hover, .stat .tip:focus-visible{border-color:#52514e;color:#0b0b0b}
.stat .tt{position:absolute;right:-2px;top:22px;width:230px;padding:8px 10px;border-radius:6px;background:#0b0b0b;color:#f4f3ef;font-size:.78rem;line-height:1.4;
          text-align:left;text-transform:none;letter-spacing:0;font-weight:400;box-shadow:0 6px 18px rgba(0,0,0,.18);opacity:0;visibility:hidden;
          transform:translateY(-3px);transition:opacity .15s,transform .15s,visibility .15s;z-index:20;pointer-events:none}
.stat .tip:hover .tt, .stat .tip:focus .tt{opacity:1;visibility:visible;transform:none}

/* ---- process flow (new) ---- */
.sec-head{font-size:.78rem;letter-spacing:.08em;text-transform:uppercase;color:#52514e;font-weight:600;margin:26px 0 2px 0}
.sec-head span{margin-left:10px;font-weight:400;letter-spacing:.04em;text-transform:none;color:#898781}
.pf{--on:#0b0b0b;--off:#e1e0d9;margin-top:6px}
.pf .trk{position:relative;display:grid;grid-template-columns:repeat(6,1fr);gap:16px;height:62px}   /* 16px = st.columns(gap='small'), so the button row lines up */
.pf .nd{position:relative;display:flex;align-items:flex-end;justify-content:center;padding-bottom:4px}
.pf .sg{position:absolute;bottom:20px;height:3px;background:var(--off);border-radius:2px}
.pf .sg.l{left:-8px;right:50%} .pf .sg.r{left:50%;right:-8px}
.pf .sg.on{background:var(--on)} .pf .sg.dim{background:repeating-linear-gradient(90deg,#d6d5ce 0 5px,transparent 5px 9px)}
.pf .knob{position:relative;z-index:1;width:34px;height:34px;border-radius:50%;background:#fff;border:2px solid var(--off);display:grid;place-items:center;color:#b5b4ab}
.pf .knob svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
.pf .nd.done .knob{background:var(--on);border-color:var(--on);color:#fff}
.pf .nd.active .knob{border-color:var(--on);color:var(--on);animation:ndpulse 1.3s ease-in-out infinite}
.pf .nd.skip .knob{border-style:dashed;color:#c3c2b7;background:#fafaf8}
@keyframes ndpulse{0%,100%{box-shadow:0 0 0 0 rgba(11,11,11,.25)} 50%{box-shadow:0 0 0 8px rgba(11,11,11,0)}}
/* the fork: a "clear-cut" bypass from Route over Investigate to Verdict */
.pf .arc{position:absolute;top:2px;height:38px;left:calc((100% - 80px) / 6 * 2.5 + 16px * 2);width:calc((100% - 80px) / 6 * 2 + 16px * 2);pointer-events:none}
.pf .arc svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.pf .arc path{fill:none;stroke:var(--off);stroke-width:3;stroke-linecap:round}
.pf .arc.on path{stroke:var(--on)} .pf .arc.dim path{stroke:#d6d5ce;stroke-dasharray:5 4}
.pf .arc span{position:absolute;left:50%;top:-4px;transform:translateX(-50%);background:#fff;padding:0 6px;font-size:.66rem;letter-spacing:.06em;text-transform:uppercase;color:#898781}
.pf .arc.on span{color:var(--on);font-weight:600}
/* stop cards */
.pf .cds{display:grid;grid-template-columns:repeat(6,1fr);gap:16px;margin-top:6px}
.pf .cd{position:relative;display:flex;flex-direction:column;border:1px solid #e1e0d9;border-radius:8px;background:#fff;padding:10px 12px 62px;min-height:216px;min-width:0}   /* bottom: room for lifted buttons */
.pf .cd .bd{flex:1;display:flex;flex-direction:column}
.pf .fact.anchor{margin-top:auto}   /* the call tile sits right above its Cancel / Call now (or Dismiss) buttons */
.pf .cd.done{border-color:#cfcec6} .pf .cd.active{border-color:var(--on);box-shadow:0 0 0 3px rgba(11,11,11,.06)}
.pf .cd.todo{background:#fbfbf9} .pf .cd.skip{background:#fbfbf9;border-style:dashed}
.pf .cd .hd{display:flex;justify-content:space-between;align-items:baseline;gap:6px}
.pf .cd .nm{white-space:nowrap;font-size:.74rem;font-weight:650;letter-spacing:.06em;text-transform:uppercase;color:#0b0b0b}
.pf .cd.todo .nm, .pf .cd.skip .nm{color:#a3a29a}
.pf .cd .lt{font-size:.7rem;color:#898781;font-family:ui-monospace,Menlo,Consolas,monospace;white-space:nowrap}
.pf .chip2{display:inline-block;margin:5px 0 6px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.68rem;color:#52514e;background:#f1f0eb;border-radius:4px;padding:1px 6px}
.pf .bd{font-size:.86rem;color:#0b0b0b}
.pf .big{font-size:1.08rem;font-weight:650;line-height:1.25;margin:2px 0 4px} .pf .big.sm{font-size:.98rem;margin-top:6px}
.pf .ln{line-height:1.35;margin:2px 0}
.pf .meta{font-size:.76rem;color:#898781;line-height:1.35;margin-top:4px}
.pf .wait{color:#52514e;font-size:.84rem;margin-top:6px} .pf .wait::after{content:"";display:inline-block;width:1.2em;animation:ell 1.2s steps(4) infinite;overflow:hidden;vertical-align:bottom}
@keyframes ell{0%{content:""} 25%{content:"."} 50%{content:".."} 75%{content:"..."}}
.pf .pb{display:grid;grid-template-columns:62px 1fr 32px;align-items:center;gap:6px;font-size:.74rem;color:#52514e;margin:3px 0}
.pf .pb .tr{position:relative;height:7px;background:#efeeea;border-radius:4px}
.pf .pb .fl{height:100%;border-radius:4px}
.pf .pb i{position:absolute;top:-3px;bottom:-3px;width:2px;background:#0b0b0b;opacity:.55}
.pf .pb b{text-align:right;color:#0b0b0b;font-weight:600}
.pf .rt{display:flex;align-items:center;gap:7px;border:1px solid #e1e0d9;border-radius:6px;padding:5px 8px;margin:4px 0;font-size:.82rem;color:#a3a29a}
.pf .rt::before{content:"";width:9px;height:9px;border-radius:50%;border:2px solid currentColor}
.pf .rt.on{border-color:#0b0b0b;color:#0b0b0b;font-weight:600} .pf .rt.on::before{background:#0b0b0b}
.pf .why2{color:#52514e}
.pf .pill{display:inline-block;align-self:flex-start;padding:4px 10px;border-radius:8px;color:#fff;font-weight:650;font-size:.92rem;line-height:1.3;margin:4px 0 4px}   /* not a capsule: long verdicts wrap */
.pf .fact{display:flex;align-items:center;gap:8px;border:1px solid #e1e0d9;border-radius:6px;padding:6px 8px;margin:4px 0}
.pf .fact svg{width:20px;height:20px;flex:none}
.pf .fact b{display:block;font-size:.8rem;line-height:1.2} .pf .fact span{display:block;font-size:.7rem;color:#898781;line-height:1.25}
.pf .fact.call{border-color:#d03b3b}
.pf .fact .ring{width:34px;height:34px} .pf .fact .ring span{width:26px;height:26px;font-size:.78rem;color:#0b0b0b}
.pf .think2{position:absolute;inset:0;border-radius:8px;background:#fff;display:none;flex-direction:column;align-items:center;justify-content:center;gap:6px;color:#52514e;font-size:.84rem}
/* reasoning strip */
.pf .strip{margin-top:12px;border:1px solid #e1e0d9;border-left:4px solid #898781;border-radius:6px;background:#fff;padding:10px 14px}
.pf .sh{font-size:.7rem;letter-spacing:.05em;text-transform:uppercase;color:#898781}
.pf .tx2{font-size:.95rem;line-height:1.5;color:#0b0b0b;margin:4px 0 6px;font-style:italic}
.pf .chips2 span{display:inline-block;border:1px solid #e1e0d9;border-radius:999px;padding:2px 10px;margin:3px 6px 0 0;font-size:.8rem;color:#52514e;background:#fbfbf9}
/* replay: a timed reveal along the track (delays per stop in --d) */
.pf.play .nd, .pf.play .cd{opacity:0;animation:appear .4s ease-out var(--d) forwards}
.pf.play .sg.on{transform-origin:left;transform:scaleX(0);animation:grow .4s ease-out var(--d) forwards}
.pf.play .arc{clip-path:inset(0 100% 0 0);animation:reveal .55s ease-out var(--d) forwards}   /* wipe left to right (a dash draw breaks with non-scaling strokes) */
.pf.play .think2{display:flex;animation:fadeout .3s ease-out 3.1s forwards}
.pf.play .strip{opacity:0;animation:appear .5s ease-out var(--d) forwards}
.pf.play .nd.done .knob{animation:ndpop .45s ease-out var(--d) both}
@keyframes ndpop{0%{transform:scale(.6)} 70%{transform:scale(1.12)} 100%{transform:scale(1)}}

/* buttons lifted into the bottom of the Verdict / Act cards (cards reserve the space) */
[class*="st-key-flow_buttons"]{margin-top:-54px;position:relative;z-index:5}
[class*="st-key-flow_buttons"] > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]{padding:0 12px}
[class*="st-key-flow_buttons"] button{min-height:32px;padding:2px 4px}
[class*="st-key-flow_buttons"] button p{font-size:.78rem;white-space:nowrap}
.st-key-flow_buttons_play{opacity:0;animation:appear .35s ease-out 3.6s forwards}
.st-key-flow_buttons_playq{opacity:0;animation:appear .35s ease-out 2.1s forwards}
.pf .fact .ring span{display:grid;place-items:center;width:26px;height:26px;font-size:.8rem;font-weight:700;color:#0b0b0b}
/* the reasoning modal */
div[data-testid="stDialog"]{backdrop-filter:blur(5px);-webkit-backdrop-filter:blur(5px)}   /* blur the app behind the modal */
div[data-testid="stDialog"] [role="dialog"]{border:1px solid #e1e0d9;border-radius:8px}
.mi-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;color:#52514e;font-size:.9rem;margin-bottom:6px}
.mi-head .pill{display:inline-block;padding:3px 10px;border-radius:8px;color:#fff;font-weight:650}
.mi-sec{border:1px solid #e1e0d9;border-radius:8px;padding:10px 14px;margin:10px 0;background:#fff}
.mi-h{font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:#0b0b0b;font-weight:650;margin-bottom:6px}
.mi-h span{font-weight:400;text-transform:none;letter-spacing:0;color:#898781;margin-left:8px}
.mi-why{font-size:.98rem;line-height:1.55;color:#0b0b0b}
.mi-ev{display:grid;grid-template-columns:96px 150px 1fr;gap:10px;align-items:baseline;padding:5px 0;border-top:1px solid #f1f0eb;font-size:.86rem}
.mi-ev:first-of-type{border-top:0}
.mi-ev .src{font-size:.7rem;letter-spacing:.05em;color:#52514e;background:#f1f0eb;border-radius:4px;padding:1px 6px;justify-self:start}
.mi-ev code{font-size:.74rem;color:#52514e;background:none;padding:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mi-kv{display:grid;grid-template-columns:96px 1fr;gap:10px;font-size:.88rem;padding:3px 0}
.mi-kv span{color:#898781;font-size:.78rem}

/* ---- dev panel (sidebar): a utilitarian overlay, deliberately unlike the dashboard — "stats for nerds" ---- */
section[data-testid="stSidebar"]{--dv-bg:rgba(16,17,19,.94);--dv-fg:#d8d8d4;--dv-mute:#8b8b86;--dv-line:#3a3b3e;--dv-acc:#7ee0b5;
  --dv-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;background:var(--dv-bg);border-right:1px solid var(--dv-line)}
section[data-testid="stSidebar"] > div{background:transparent}
section[data-testid="stSidebar"] *{font-family:var(--dv-mono) !important}
section[data-testid="stSidebar"] [data-testid="stIconMaterial"], section[data-testid="stSidebar"] [data-testid="stIconMaterial"] *{font-family:"Material Symbols Rounded" !important}
section[data-testid="stSidebar"], section[data-testid="stSidebar"] p, section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] span{color:var(--dv-fg);font-size:.8rem}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"], section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p{color:var(--dv-mute);font-size:.72rem;line-height:1.45}
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p{color:var(--dv-mute);font-size:.72rem}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.55rem}
/* inputs (selectbox / multiselect render a [role=group] box): flat, square, outlined */
section[data-testid="stSidebar"] [role="group"]:has(input[role="combobox"]){background:transparent !important;border:1px solid var(--dv-line) !important;border-radius:2px !important;min-height:32px}
section[data-testid="stSidebar"] [role="group"]:has(input[role="combobox"]):hover, section[data-testid="stSidebar"] [role="group"]:has(input[role="combobox"]):focus-within{border-color:var(--dv-acc) !important}
section[data-testid="stSidebar"] [role="group"]:has(input[role="combobox"]) input{color:var(--dv-fg) !important;font-size:.8rem !important}
section[data-testid="stSidebar"] [role="group"]:has(input[role="combobox"]) input::placeholder{color:var(--dv-mute) !important}
section[data-testid="stSidebar"] [role="group"]:has(input[role="combobox"]) > button, section[data-testid="stSidebar"] [role="group"]:has(input[role="combobox"]) > button svg{color:var(--dv-mute) !important}
section[data-testid="stSidebar"] [data-testid="stMultiSelectTagsContainer"] [role="group"] > span{background:transparent !important;border:1px solid var(--dv-acc);border-radius:2px !important;color:var(--dv-acc) !important;font-size:.72rem}
section[data-testid="stSidebar"] [data-testid="stMultiSelectTagsContainer"] [role="group"] *{color:var(--dv-acc) !important}
/* dropdown menus are portalled to <body>: style them only while a sidebar combobox is open */
body:has(section[data-testid="stSidebar"] [role="combobox"][aria-expanded="true"]) :is([data-testid="stSelectboxVirtualDropdown"],[data-testid="stMultiSelectDropdown"]){background:#16171a !important;border:1px solid var(--dv-line, #3a3b3e);border-radius:2px;box-shadow:0 6px 18px rgba(0,0,0,.45)}
body:has(section[data-testid="stSidebar"] [role="combobox"][aria-expanded="true"]) :is([data-testid="stSelectboxVirtualDropdown"],[data-testid="stMultiSelectDropdown"]) *{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace !important;font-size:.78rem;color:#d8d8d4}
body:has(section[data-testid="stSidebar"] [role="combobox"][aria-expanded="true"]) :is([data-testid="stSelectboxVirtualDropdown"],[data-testid="stMultiSelectDropdown"]) [role="option"]:hover,
body:has(section[data-testid="stSidebar"] [role="combobox"][aria-expanded="true"]) :is([data-testid="stSelectboxVirtualDropdown"],[data-testid="stMultiSelectDropdown"]) [role="option"][data-focused="true"],
body:has(section[data-testid="stSidebar"] [role="combobox"][aria-expanded="true"]) :is([data-testid="stSelectboxVirtualDropdown"],[data-testid="stMultiSelectDropdown"]) [role="option"][data-hovered="true"]{background:rgba(126,224,181,.12) !important}
body:has(section[data-testid="stSidebar"] [role="combobox"][aria-expanded="true"]) :is([data-testid="stSelectboxVirtualDropdown"],[data-testid="stMultiSelectDropdown"]) [role="option"][aria-selected="true"]{color:#7ee0b5}
body:has(section[data-testid="stSidebar"] [role="combobox"][aria-expanded="true"]) :is([data-testid="stSelectboxVirtualDropdown"],[data-testid="stMultiSelectDropdown"]) [role="option"][aria-selected="true"] *{color:#7ee0b5}
/* toggles (label > hidden input, track div > knob div): square, accent when on */
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label > span + div{background:var(--dv-line) !important;border-radius:2px !important}
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label > span + div > div{background:var(--dv-mute) !important;border-radius:1px !important;box-shadow:none !important}
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:checked) > span + div{background:rgba(126,224,181,.35) !important}
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:checked) > span + div > div{background:var(--dv-acc) !important}
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:disabled){opacity:.45}
/* buttons: bracketed text, no fill */
section[data-testid="stSidebar"] .stButton button{background:transparent;border:1px solid var(--dv-acc);border-radius:2px;min-height:32px;color:var(--dv-acc)}
section[data-testid="stSidebar"] .stButton button p{color:var(--dv-acc);font-size:.78rem;text-transform:lowercase}
section[data-testid="stSidebar"] .stButton button p::before{content:"[ "} section[data-testid="stSidebar"] .stButton button p::after{content:" ]"}
section[data-testid="stSidebar"] .stButton button:hover{background:rgba(126,224,181,.1)}
/* expander */
section[data-testid="stSidebar"] [data-testid="stExpander"] details{border:1px dashed var(--dv-line);border-radius:2px;background:transparent}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover, section[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover p{color:var(--dv-acc)}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary{background:transparent !important}   /* Streamlit paints it near-white when open */
section[data-testid="stSidebar"] [data-testid="stExpander"] details[open] > summary{border-bottom:1px dashed var(--dv-line)}
section[data-testid="stSidebar"] [data-testid="stExpander"] details[open] > summary p{color:var(--dv-acc)}
/* collapse chevron */
section[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] button{color:var(--dv-mute)}
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"]{height:40px;margin-bottom:0}   /* half the default space above the title */
.devtitle{font-size:.82rem !important;color:var(--dv-acc);letter-spacing:.04em;margin:-8px 0 0 0}
.devtitle::before{content:"▍"}
.devsec{font-size:.7rem !important;letter-spacing:.08em;text-transform:uppercase;color:var(--dv-mute);display:flex;align-items:center;gap:8px;margin:12px 0 0 0}
.devsec::before{content:"//";color:var(--dv-acc)}
.devsec::after{content:"";flex:1;border-top:1px dashed var(--dv-line)}
.devlab{font-size:.8rem !important;color:var(--dv-fg)}
/* Streamlit pulls the next element up under markdown (margin-bottom:-1rem): undo it for the panel's own blocks so headings don't crowd and labels centre */
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"]:has(> .devlab, > .devsec, > .devtitle, > .devstats){margin-bottom:0}
section[data-testid="stSidebar"] .stBidiComponent{margin-top:6px}
.devstats{border-top:1px dashed var(--dv-line);margin-top:10px;padding-top:8px;font-size:.7rem !important;color:var(--dv-fg);line-height:1.6}
.devstats div{font-size:.7rem !important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.devstats span{display:inline-block;width:64px;color:var(--dv-mute);font-size:.7rem !important}
</style>""", unsafe_allow_html=True)


# ----------------------------------------------------------------------------- data
@st.cache_resource
def conn():
    from databricks import sql
    http_path = os.environ.get("DATABRICKS_WAREHOUSE_HTTP_PATH") or f"/sql/1.0/warehouses/{os.environ['DATABRICKS_WAREHOUSE_ID']}"
    if os.environ.get("DATABRICKS_CLIENT_ID") or not os.environ.get("DATABRICKS_TOKEN"):
        from databricks.sdk.core import Config
        cfg = Config()
        return sql.connect(server_hostname=cfg.host.replace("https://", ""), http_path=http_path, credentials_provider=lambda: cfg.authenticate)
    return sql.connect(server_hostname=os.environ["DATABRICKS_HOST"].replace("https://", ""), http_path=http_path, access_token=os.environ["DATABRICKS_TOKEN"])

def q(sql_text: str) -> pd.DataFrame:
    with conn().cursor() as cur:
        cur.execute(sql_text)
        return pd.DataFrame(cur.fetchall(), columns=[c[0] for c in cur.description])

@st.cache_data(ttl=900)
def load_events() -> pd.DataFrame:
    return q(f"""
        SELECT g.candidate_id, g.station_id, s.name AS station_name, s.province, g.signal, g.rule,
               g.ts_start_utc, g.ts_end_utc, g.emitted_at_utc, g.value, g.delta_6h, g.rain_24h_mm,
               g.p_fault, g.p_operational, g.p_real_event, coalesce(v.route, g.route) AS route, g.state_json,
               v.verdict, v.severity, v.confidence, v.decided_by, v.rationale, v.evidence, v.agent_state, g.case_id,
               collect_list(a.action_type) AS actions
        FROM {CATALOG}.silver.gate_decisions g
        JOIN {CATALOG}.silver.station_context s USING (station_id)
        LEFT JOIN {CATALOG}.gold.verdicts v USING (candidate_id)
        LEFT JOIN {CATALOG}.gold.actions  a USING (candidate_id)
        GROUP BY ALL
    """)


@st.cache_data(ttl=900)
def load_stations() -> pd.DataFrame:
    return q(f"""SELECT station_id, name, province, basin, lat, lon, regulated, controls, upstream_ids, downstream_ids, nearby_ids,
                        update_cadence_min, normal_flatline_steps, typical_change_fraction,
                        discharge_min_possible_cms, discharge_max_plausible_cms, level_min_possible_m, level_max_plausible_m, p99_cms, notes
                 FROM {CATALOG}.silver.station_context ORDER BY province, station_id""")

@st.cache_data(ttl=900)
def load_cases() -> pd.DataFrame:
    df = q(f"SELECT case_id, station_id, win_start_utc, win_end_utc, expected_verdict, notes FROM {CATALOG}.silver.cases ORDER BY case_id")
    for c in ("win_start_utc", "win_end_utc"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df

@st.cache_data(ttl=900)
def load_results():
    """Two numbers for the footer and the one-line explanation of how the system improved itself."""
    try:
        golden = q(f"SELECT case_id, outcome_ok FROM {CATALOG}.gold.scorecard_golden")
    except Exception:
        golden = pd.DataFrame(columns=["case_id", "outcome_ok"])
    try:
        plan = q(f"SELECT from_round_id, to_round_id, trigger, change FROM {CATALOG}.gold.plan_iterations ORDER BY created_at_utc")
    except Exception:
        plan = pd.DataFrame()
    return golden, plan

@st.cache_data(ttl=900)
def load_readings(station_id: str, signal: str) -> pd.DataFrame:
    df = q(f"SELECT ts_utc, value, is_quarantined FROM {CATALOG}.silver.readings WHERE station_id='{station_id}' AND signal='{signal}' ORDER BY ts_utc")
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df


# ----------------------------------------------------------------------------- hook for the phone call
def on_page(ev) -> None:
    """A 'call the on-call person' decision just crossed the playhead: start the countdown. The call is placed only if
    nobody cancels before it reaches zero (bounded autonomy: the system decides, a human keeps a veto)."""
    sev = SEVERITY_WORD.get(ev.severity, "")
    ss.pending_call = {
        "candidate_id": ev.candidate_id, "station": ev.station_name, "severity": sev,
        "deadline": time.time() + ss.countdown_s + 4.5,      # let the pipeline reveal (~4 s) finish before the ring starts
        "alert": {"severity": sev or "alert", "station": ev.station_name,
                  "summary": (ev.rationale or f"{ev.station_name} needs attention.")[:400],
                  "evidence": " ".join(e.get("fact", "") for e in _safe_json(ev.evidence)[:3])[:600]},
    }


def place_call(alert: dict, contacts: list, escalate: bool, key: str) -> None:
    """Runs pager.page in the background and records the result under ss.calls[key]."""
    ss.calls[key] = {"status": "CALLING", "result": None}
    calls = ss.calls                                   # plain dict reference: safe to update from the worker thread
    def _run():
        try:
            res = pager.page(contacts, alert, escalate=escalate)
            calls[key] = {"status": "DONE", "result": res}
        except SystemExit as e:
            calls[key] = {"status": "FAILED", "result": str(e)}
        except Exception as e:
            calls[key] = {"status": "FAILED", "result": repr(e)}
    t = threading.Thread(target=_run, daemon=True)
    try:
        from streamlit.runtime.scriptrunner import add_script_run_ctx
        add_script_run_ctx(t)                          # lets the thread touch Streamlit state without warnings
    except Exception:
        pass
    t.start()


# ----------------------------------------------------------------------------- map (pydeck: real basemap, rendered natively by Streamlit)
import pydeck as pdk

def _rgb(hex_, a=255):
    h = hex_.lstrip("#"); return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a]

def station_map(st_df: pd.DataFrame, focus_id=None, vc=MUTED, pcts=None, height=240):
    """Gauge network on a real basemap. With a focus station: fly to it, draw same-river links (solid) and nearby
    other-river links (thin), colour neighbours by their 24-hour movement. Without one: the whole network."""
    pcts = pcts or {}
    byid = st_df.set_index("station_id")
    base = pd.DataFrame({"lon": st_df.lon.astype(float), "lat": st_df.lat.astype(float), "label": [nice_name(n) for n in st_df.name]})
    layers = [pdk.Layer("ScatterplotLayer", data=base, get_position="[lon, lat]", get_fill_color=_rgb("#b8b7b0"),
                        get_radius=1, radius_min_pixels=4, radius_max_pixels=6, pickable=True)]
    if focus_id is not None and focus_id in byid.index:
        f = byid.loc[focus_id]
        flat, flon = float(f.lat), float(f.lon)
        conn_ids = [i for i in _ids(f.get("upstream_ids")) + _ids(f.get("downstream_ids")) if i in byid.index]
        near = [(i, km) for i, km in _nearby(f.get("nearby_ids")) if i in byid.index and i not in conn_ids]
        if conn_ids:
            lines = pd.DataFrame([{"s": [flon, flat], "t": [float(byid.loc[i].lon), float(byid.loc[i].lat)]} for i in conn_ids])
            layers.append(pdk.Layer("LineLayer", data=lines, get_source_position="s", get_target_position="t",
                                    get_color=_rgb("#52514e"), get_width=3, width_units="pixels"))
        if near:
            lines = pd.DataFrame([{"s": [flon, flat], "t": [float(byid.loc[i].lon), float(byid.loc[i].lat)]} for i, _ in near])
            layers.append(pdk.Layer("LineLayer", data=lines, get_source_position="s", get_target_position="t",
                                    get_color=_rgb("#c3c2b7"), get_width=1.5, width_units="pixels"))
        nb = conn_ids + [i for i, _ in near]
        if nb:
            rows = []
            for i in nb:
                pcv = pcts.get(i)
                if pcv is None:    col, word = "#898781", "no change data"
                elif pcv > 0.20:   col, word = VERDICT_COLOR["NATURAL_EVENT"], f"rising {pcv*100:+.0f}%"
                elif pcv < -0.20:  col, word = VERDICT_COLOR["SENSOR_FAULT"], f"falling {pcv*100:+.0f}%"
                else:              col, word = "#898781", f"steady {pcv*100:+.0f}%"
                rows.append({"lon": float(byid.loc[i].lon), "lat": float(byid.loc[i].lat), "color": _rgb(col),
                             "txt": f"{pcv*100:+.0f}%" if pcv is not None else "", "label": f"{nice_name(byid.loc[i].name)} · {word}"})
            nbd = pd.DataFrame(rows)
            layers.append(pdk.Layer("ScatterplotLayer", data=nbd, get_position="[lon, lat]", get_fill_color="color",
                                    get_radius=1, radius_min_pixels=7, radius_max_pixels=9, pickable=True))
            layers.append(pdk.Layer("TextLayer", data=nbd, get_position="[lon, lat]", get_text="txt", get_size=12,
                                    get_color=_rgb("#52514e"), get_pixel_offset=[14, -10], get_text_anchor="'start'", get_alignment_baseline="'center'"))
        fd = pd.DataFrame([{"lon": flon, "lat": flat, "label": nice_name(f["name"]), "name": nice_name(f["name"])}])
        layers.append(pdk.Layer("ScatterplotLayer", data=fd, get_position="[lon, lat]", get_fill_color=_rgb("#ffffff"),
                                get_radius=1, radius_min_pixels=14, radius_max_pixels=14))
        layers.append(pdk.Layer("ScatterplotLayer", data=fd, get_position="[lon, lat]", get_fill_color=_rgb(vc),
                                get_radius=1, radius_min_pixels=10, radius_max_pixels=10, pickable=True))
        layers.append(pdk.Layer("TextLayer", data=fd, get_position="[lon, lat]", get_text="name", get_size=13,
                                get_color=_rgb("#0b0b0b"), get_pixel_offset=[0, 22], get_text_anchor="'middle'", get_alignment_baseline="'top'"))
        far = max([km for _, km in near] + [0]) or 30
        zoom = 9.2 if far <= 15 else 8.3 if far <= 35 else 7.6 if far <= 60 else 6.9
        view = pdk.ViewState(latitude=flat, longitude=flon, zoom=zoom, pitch=0)
    else:
        lat, lon = st_df.lat.astype(float), st_df.lon.astype(float)
        span = max(lat.max() - lat.min(), (lon.max() - lon.min()) * 0.6, 0.05)       # degrees; lon squeezed at these latitudes
        zoom = 9.5 if span < 0.25 else 8.3 if span < 0.6 else 7.3 if span < 1.2 else 6.3 if span < 2.5 else 5.3 if span < 5 else 3.0
        view = pdk.ViewState(latitude=float((lat.max() + lat.min()) / 2), longitude=float((lon.max() + lon.min()) / 2), zoom=zoom, pitch=0)
    deck = pdk.Deck(layers=layers, initial_view_state=view, map_style="light", tooltip={"text": "{label}"})
    st.pydeck_chart(deck, width="stretch", height=height)

# ----------------------------------------------------------------------------- live model calls (same inputs as the SQL pipeline)
LLM_MODEL = os.environ.get("LLM_MODEL", "databricks-meta-llama-3-3-70b-instruct")
GATE_QUESTIONS = json.dumps({
    "label": {"type": "choice",
              "instructions": "Given the station context, detector rule, the reading and its neighbours and weather, what best explains this anomaly?",
              "criteria": {"SENSOR_FAULT": "the instrument, datalogger or telemetry produced a wrong or missing value: physically impossible values, isolated single-point spikes with flat neighbours in time, stuck values far beyond the station normal flatline, or telemetry gaps",
                           "OPERATIONAL_CHANGE": "a dam, weir, canal or operator action changed the flow: step changes on regulated reaches without rain and without connected (same-river) neighbours moving",
                           "NATURAL_EVENT": "rain, snowmelt or a real hydrological change: sustained rise, rain in the last 24-48 h, connected neighbours rising, or several stations on nearby rivers rising together"}},
    "impossible": {"type": "noul",
                   "instructions": "The reading value is physically impossible at this station: at or below physical_min (zero or negative flow in a perennial river). A value above plausible_max is NOT impossible; floods exceed it."},
    "severity": {"type": "score",
                 "instructions": "If this reading is real, how serious is it for people and infrastructure downstream?",
                 "criteria": ["routine variation", "noticeable but within seasonal norms", "approaching advisory level or a fast rise", "watch level, or a very fast rise with rain", "warning level: flooding and danger to life plausible"]}})
LLM_PROMPT = ("You are an investigation agent for a river-gauge network. Decide whether this anomaly is a SENSOR_FAULT, an OPERATIONAL_CHANGE, "
              "a NATURAL_EVENT, or INCONCLUSIVE. Use ONLY the evidence present in the JSON state; never assume a value for anything that is absent. "
              "Rules: a value at or below physical_min is a fault; a value above plausible_max is NOT automatically a fault - if connected neighbours "
              "are rising, or several other-river stations are rising together, or the state contains weather showing rain, it is a real flood; "
              "an isolated single-point excursion with flat neighbours in time is a fault; a telemetry gap is a fault in the data but any rise around it "
              "may be real; a step on a regulated reach with no neighbour moving is an operational change; a sustained rise corroborated by neighbours "
              "(and by rain, if weather is present) is a natural event. If the state has no weather block, weather is UNKNOWN: do not claim rain or "
              "no rain, and never cite source RAIN. If evidence conflicts say INCONCLUSIVE rather than guess. Cite each fact you relied on. STATE: ")
LLM_SCHEMA = ('{"type":"json_schema","json_schema":{"name":"verdict","strict":true,"schema":{"type":"object","properties":{'
              '"verdict":{"type":"string","enum":["SENSOR_FAULT","OPERATIONAL_CHANGE","NATURAL_EVENT","INCONCLUSIVE"]},'
              '"confidence":{"type":"number"},'
              '"evidence":{"type":"array","items":{"type":"object","properties":{"source":{"type":"string","enum":["SERIES","PHYSICS","NEIGHBOURS","CONTEXT","RAIN","ADVISORY"]},"tool":{"type":"string"},"fact":{"type":"string"}},"required":["source","tool","fact"]}},'
              '"rationale":{"type":"string"}},"required":["verdict","confidence","evidence","rationale"]}}}')

def _scalar(sql_text: str, params: dict):
    with conn().cursor() as cur:
        cur.execute(sql_text, params)
        row = cur.fetchone()
    v = row[0] if row else None
    if isinstance(v, (bytes, bytearray)): v = v.decode()
    if isinstance(v, str):
        try: return json.loads(v)
        except Exception: return v
    return v

def run_gate_live(state_json: str):
    """ai_decide on the warehouse, exactly as step 1 of the pipeline. Returns (probabilities dict, milliseconds)."""
    t0 = time.perf_counter()
    r = _scalar("SELECT ai_decide(:state, :questions, map('version','1.0'))", {"state": state_json, "questions": GATE_QUESTIONS})
    ms = (time.perf_counter() - t0) * 1000
    ans = ((r or {}).get("response") or {}).get("answers") or {}
    probs = (ans.get("label") or {}).get("probabilities") or {}
    return {"p_fault": probs.get("SENSOR_FAULT"), "p_operational": probs.get("OPERATIONAL_CHANGE"), "p_real_event": probs.get("NATURAL_EVENT"),
            "label": (ans.get("label") or {}).get("choice"), "p_impossible": (ans.get("impossible") or {}).get("probability"),
            "severity_score": (ans.get("severity") or {}).get("score")}, ms

def run_llm_live(agent_state: str):
    """ai_query on the warehouse with the pipeline's prompt and strict schema. Returns (verdict dict, milliseconds)."""
    t0 = time.perf_counter()
    r = _scalar(f"SELECT ai_query(:model, :prompt, responseFormat => '{LLM_SCHEMA}')", {"model": LLM_MODEL, "prompt": LLM_PROMPT + (agent_state or "")})
    ms = (time.perf_counter() - t0) * 1000
    return (r if isinstance(r, dict) else {"rationale": str(r)}), ms

# ----------------------------------------------------------------------------- live decision panel
ICON = {
    "PAGE": '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#d03b3b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.6a2 2 0 0 1-.5 2.1L8 9.7a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.8.3 1.7.5 2.6.7a2 2 0 0 1 1.7 2z"/></svg>',
    "QUARANTINE": '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#eb6834" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 22V4"/><path d="M4 4h12l-2 4 2 4H4"/></svg>',
    "TICKET": '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#52514e" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 0 0 5 5l-9.4 9.4a2.1 2.1 0 0 1-3-3l9.4-9.4z"/><path d="M14.7 6.3L17 4"/></svg>',
    "WATCH": '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#2a78d6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    "LOG": '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#898781" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h8"/></svg>',
}
ACTION_SUB = {"PAGE": "On-call duty officer", "QUARANTINE": "Readings excluded from downstream use; reversible",
              "TICKET": "Field inspection requested for this gauge", "WATCH": "Station placed on watch; re-evaluated on next readings", "LOG": "Recorded, no intervention"}
ACTION_TITLE = {"PAGE": "Phone call", "QUARANTINE": "Data quarantined", "TICKET": "Technician ticket", "WATCH": "Watch", "LOG": "Logged"}
ACTION_ORDER = ["PAGE", "QUARANTINE", "TICKET", "WATCH", "LOG"]
ARROW = '<div class="arrow">&#8594;</div>'


def hero(ev, fresh: bool, pending, call):
    """The live decision card: what was spotted, how it was decided, what was done. Renders HTML, a sparkline, call buttons."""
    vc = VERDICT_COLOR.get(ev.verdict, MUTED)
    sev = SEVERITY_WORD.get(ev.severity, "")
    unit = "m³/s" if ev.signal == "DISCHARGE" else "m"
    value_txt = f"{ev.value:.3g} {unit}" if ev.value is not None else "no value"
    was = ""
    if ev.delta_6h is not None and ev.value is not None and abs(ev.delta_6h) > 1e-9:
        was = f", was {ev.value - ev.delta_6h:.3g} six hours earlier"
    facts = [humanize(e.get("fact", "")) for e in _safe_json(ev.evidence) if e.get("fact")][:3]
    why = humanize((ev.rationale or "").strip())
    closer = ev.route == "INVESTIGATE"
    odd = RULE_WORD.get(ev.rule, ev.rule)
    odd_cap = odd[0].upper() + odd[1:]
    anim = "fresh" if fresh else ""
    station = nice_name(ev.station_name)

    dynamic = getattr(ev, "dynamic", False)
    box = st.container(border=True)
    c1, c2 = box.columns([1.0, 2.1])
    with c1:
        head_slot = st.empty()
        def paint_head():
            vc_ = VERDICT_COLOR.get(ev.verdict, MUTED); sev_ = SEVERITY_WORD.get(ev.severity, "")
            badge_txt = ("Deciding…" if ev.verdict == "PENDING" else VERDICT_WORD.get(ev.verdict, ev.verdict) + (f" · {sev_}" if sev_ else ""))
            head_slot.markdown(f'<div class="hero {anim}" style="border-left-color:{vc_}"><div class="when">{("Scenario · " + getattr(ev, "scenario", "")) + " · " if dynamic else ""}{fmt(ev.visible_at)}</div><div class="where">{station}</div>'
                               f'<div class="odd">{odd_cap}: <b>{value_txt}</b>{was}.</div>'
                               f'<span class="verdict" style="background:{vc_}">{badge_txt}</span></div>', unsafe_allow_html=True)
        paint_head()
        station_map(stations, ev.station_id, vc, neighbour_pcts(ev.state_json), height=240)
        st.caption("Neighbouring gauges in the last 24 hours: blue rising, orange falling, grey steady. Solid line = same river.")
        rd = load_readings(ev.station_id, ev.signal)
        t0, t1 = ev.visible_at - timedelta(hours=6), ev.visible_at
        d = rd[(rd.ts_utc >= t0) & (rd.ts_utc <= t1)]
        if len(d):
            sp = go.Figure(go.Scatter(x=mdt(d.ts_utc), y=d.value, mode="lines", line=dict(width=2, color=vc), hoverinfo="skip"))
            sp.add_vrect(x0=mdt(max(ev.ts_start_utc, t0)), x1=mdt(min(max(ev.ts_end_utc, ev.ts_start_utc + timedelta(minutes=10)), t1)),
                         fillcolor=vc, opacity=0.15, line_width=0)
            sp.update_layout(height=90, margin=dict(l=0, r=0, t=6, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                             xaxis=dict(visible=False, range=[mdt(t0), mdt(t1)]), yaxis=dict(visible=False, rangemode="tozero"), showlegend=False)
            st.plotly_chart(sp, width="stretch", config={"displayModeBar": False, "staticPlot": True})
            st.caption(f"Last 6 hours, {('flow' if ev.signal == 'DISCHARGE' else 'water level')} in {unit}. Shaded: the odd readings.")
    with c2:
        slot = st.empty()
        acts = sorted(ev.actions, key=lambda x: ACTION_ORDER.index(x) if x in ACTION_ORDER else 9) or ["LOG"]
        used_rain = bool(ev.evidence) and '"RAIN"' in str(ev.evidence)

        def tiles_html():
            tiles = ""
            for a in acts:
                if a == "PAGE" and pending is not None:
                    remaining = max(0, int(round(pending["deadline"] - time.time())))
                    if remaining > 0:
                        deg = int(360 * (1 - min(remaining, ss.countdown_s) / max(ss.countdown_s, 1)))
                        mode = "Real call" if ss.real_calls else "Simulated call"
                        shown = remaining if remaining <= ss.countdown_s else ss.countdown_s
                        tiles += (f'<div class="act big" style="border-color:#d03b3b"><div class="ring" style="background:conic-gradient(#d03b3b {deg}deg,#efeeea 0)"><span>{shown}</span></div>'
                                  f'<div><div class="at">Phone call · on-call duty officer</div><div class="as">{mode} placed when the ring closes</div></div></div>')
                        continue
                    status = (call or {}).get("status")
                    label = {"CALLING": "Phone call · dialling", "DONE": "Phone call · placed", "FAILED": "Phone call · failed",
                             "SIMULATED": "Phone call · simulated", "CANCELLED": "Phone call · cancelled by operator"}.get(status, "Phone call")
                    sub = "On-call duty officer"
                    if status == "DONE" and isinstance((call or {}).get("result"), list):
                        sub = " · ".join(f"contact {x.get('contact_order')}: {str(x.get('status', '')).lower().replace('_', ' ')}" for x in call["result"])
                    elif status == "FAILED":
                        sub = str(call.get("result"))[:140]
                    tiles += f'<div class="act big" style="border-color:#d03b3b">{ICON["PAGE"]}<div><div class="at">{label}</div><div class="as">{sub}</div></div></div>'
                else:
                    border = f'style="border-color:{vc}"' if a in ("PAGE", "QUARANTINE") else ""
                    tiles += f'<div class="act" {border}>{ICON.get(a, ICON["LOG"])}<div><div class="at">{ACTION_TITLE.get(a, a)}</div><div class="as">{ACTION_SUB.get(a, "")}</div></div></div>'
            return tiles

        def bars_html(pf, po, pr):
            out = ""
            for lab, p_, col in (("Bad sensor", pf, VERDICT_COLOR["SENSOR_FAULT"]), ("Dam change", po, VERDICT_COLOR["OPERATIONAL_CHANGE"]), ("Real event", pr, VERDICT_COLOR["NATURAL_EVENT"])):
                out += (f'<div class="bar"><span class="lab">{lab}</span><div class="tr"><div class="fl" style="width:{pct(p_)}%;background:{col}"></div></div><span class="pc">{pct(p_)}%</span></div>')
            return out

        THINK = '<div class="think"><div class="dots"><span></span><span></span><span></span></div>{}</div>'

        def paint(stage, gate=None, gate_ms=None, llm=None, llm_ms=None, css_play=False):
            """stage: 'gate_running' | 'llm_running' | 'done'. gate/llm: live results or None (use stored)."""
            pf, po, pr = (gate["p_fault"], gate["p_operational"], gate["p_real_event"]) if gate else (ev.p_fault, ev.p_operational, ev.p_real_event)
            gate_lat = f"{gate_ms:.0f} ms" if gate_ms is not None else "~0.3 s"
            llm_lat = f"{llm_ms/1000:.1f} s" if llm_ms is not None else "~3 s"
            rationale = humanize((llm or {}).get("rationale") or why) if stage == "done" else ""
            facts_live = [humanize(e.get("fact", "")) for e in ((llm or {}).get("evidence") or []) if isinstance(e, dict) and e.get("fact")][:3] if llm else facts
            conf = (llm or {}).get("confidence", ev.confidence)
            conf_txt = "" if conf is None else f" · {pct(conf)}% sure"
            quick_sub = "Not clear-cut, so it took a closer look" if closer else "Clear-cut, decided instantly"
            play = ("play" + ("" if closer else " quick")) if css_play else ""
            pop = "" if css_play else "pop"
            s2_body = (THINK.format("Running ai_decide") if stage == "gate_running" else "") + bars_html(pf, po, pr) + f'<div class="s verdictline">{quick_sub}</div>'
            closer_html = ""
            if closer:
                body = (THINK.format("Running ai_query · reading the evidence") if stage in ("gate_running", "llm_running") else
                        (THINK.format("Reading the evidence") if css_play else ""))
                closer_html = (ARROW + f'<div class="step s3 {pop}"><div class="t">Closer look <span class="lat">{llm_lat}</span></div><span class="model">ai_query · LLM</span>'
                               '<div class="answer"><div class="v">Reasoned over the full evidence</div>'
                               f'<div class="s">Gauge history, physical limits, neighbouring gauges{", rainfall" if used_rain else ""}{conf_txt}</div></div>' + body + '</div>')
            decision_html = ("" if stage != "done" else ARROW +
                             f'<div class="step s4 {pop}" style="flex:0 0 auto;border-color:{vc}"><div class="t">Decision</div><div class="v" style="color:{vc}">{VERDICT_WORD.get(ev.verdict, ev.verdict)}</div><div class="s">{sev.capitalize() if sev else "&nbsp;"}</div></div>')
            why_head = ("AI reasoning · generated live by the LLM (ai_query)" if llm else "AI reasoning · generated by the LLM (ai_query)") if ev.decided_by == "AGENT_SQL" else "Decision note · from the quick check (ai_decide)"
            tail = "" if stage != "done" else (f'<div class="acts {pop}">{tiles_html()}</div>'
                                               f'<div class="why {pop}"><div class="whyhead">{why_head}</div><div class="txt">{rationale}</div></div>'
                                               f'<div class="chips {pop}">{"".join(f"<span class=\"chip\">{f}</span>" for f in facts_live)}</div>')
            note = '<div class="livenote">Models re-run live on the stored input · timings include the round trip to the warehouse</div>' if (gate or llm) else ""
            slot.markdown(f'<div class="{play}"><div class="flow">'
                          f'<div class="step s1"><div class="t">Spotted <span class="lat">live</span></div><span class="model">rule-based detector</span><div class="v">{odd_cap}</div><div class="s">Continuous checks on every reading</div></div>'
                          + ARROW +
                          f'<div class="step s2 {pop}"><div class="t">Quick check <span class="lat">{gate_lat}</span></div><span class="model">ai_decide · Jev</span>{s2_body}</div>'
                          + (closer_html if stage != "gate_running" else "") + decision_html + '</div>' + tail + note + '</div>', unsafe_allow_html=True)

        cached = ss.live_results.get(ev.candidate_id)     # live results come from run_live (process flow); this card only shows them
        if cached is not None:
            paint("done", cached["gate"], cached["gate_ms"], cached["llm"], cached["llm_ms"])
        else:
            paint("done", css_play=anim)                    # replay mode: stored values with the CSS reveal



# ----------------------------------------------------------------------------- process flow (new): one record's path, Detect → Act
FLOW_STAGES = ["Detect", "Quick check", "Route", "Investigate", "Verdict", "Act"]
ROUTE_REASON = {
    "NO_GATE": "No quick-check result, so it takes a closer look.",
    "ALWAYS_RISE": "A fast rise is always investigated — it could be a flood starting.",
    "ALWAYS_HIGH_BREACH": "A reading above the plausible maximum could be a real flood, so it is always investigated.",
    "BELOW_CUTOFF": "The top answer, {top}, is below the {thr} cut-off.",
    "FAULT_FITS": "{top} sure it's a bad sensor, which fits {rule}.",
    "DAM_FITS": "{top} sure it's a dam or operator change on a regulated reach.",
    "LABEL_MISMATCH": "{top} sure it's “{label}”, but that doesn't fit {rule}, so it takes a closer look.",
}
FLOW_ICON = {   # 24x24 stroke icons for the six stops
    "Detect": '<path d="M2 13h4l2.2-6 3.3 11 2.4-8 1.6 3H22"/>',
    "Quick check": '<path d="M12.5 2 5 13h6l-1 9 8-12h-6l1-8z"/>',
    "Route": '<path d="M5 3v6a6 6 0 0 0 6 6h8"/><path d="M5 21v-6"/><path d="m16 12 3 3-3 3"/>',
    "Investigate": '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/><path d="M8 11h6M11 8v6"/>',
    "Verdict": '<path d="M12 3v18"/><path d="M5 7h14"/><path d="m5 7-3 7a3 3 0 0 0 6 0z"/><path d="m19 7-3 7a3 3 0 0 0 6 0z"/>',
    "Act": '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
}

def flow_route(ev, gate):
    """(route, reason sentence) for the Route stop. A stored record keeps the pipeline's route; the reason is only given when this
    round's rule reproduces it (the pipeline may have run an earlier round with another cut-off)."""
    try:
        state = json.loads(ev.state_json or "{}")
    except Exception:
        state = {}
    side = getattr(ev, "breach_side", None) or (state.get("reading") or {}).get("breach_side")
    regulated = getattr(ev, "regulated", None)
    if regulated is None:
        regulated = bool((state.get("station") or {}).get("regulated"))
    route, code, top = route_with_reason(ev.rule, side, regulated, gate)
    if ev.route in ("AUTO_RESOLVE", "INVESTIGATE") and route != ev.route:
        return ev.route, "Routed by the pipeline's own round of rules."
    label = VERDICT_WORD.get((gate or {}).get("label"), "")
    return route, ROUTE_REASON[code].format(top=f"{pct(top)}%" if top is not None else "–", thr=f"{pct(AUTO_RESOLVE_THRESHOLD)}%",
                                             rule=RULE_WORD.get(ev.rule, ev.rule), label=label)

def flow_actions_html(ev, pending, call):
    acts = sorted(ev.actions, key=lambda x: ACTION_ORDER.index(x) if x in ACTION_ORDER else 9) or ["LOG"]
    out = ""
    for a in acts:
        if a == "PAGE" and pending is not None and pending["deadline"] - time.time() > 0:
            remaining = max(0, int(round(pending["deadline"] - time.time())))
            shown = min(remaining, ss.countdown_s)
            deg = int(360 * (1 - shown / max(ss.countdown_s, 1)))
            out += (f'<div class="fact call anchor"><div class="ring" style="background:conic-gradient(#d03b3b {deg}deg,#efeeea 0)"><span>{shown}</span></div>'
                    f'<div><b>Calling on-call officer</b><span>{"Real" if ss.real_calls else "Simulated"} call when the ring closes</span></div></div>')
        elif a == "PAGE":
            status = (call or {}).get("status")
            label = {"CALLING": "Dialling…", "DONE": "Call placed", "FAILED": "Call failed", "SIMULATED": "Call simulated",
                     "CANCELLED": "Call cancelled"}.get(status, "Phone call")
            anchor = " anchor" if pending is not None else ""        # Dismiss shows under it
            out += f'<div class="fact call{anchor}">{ICON["PAGE"]}<div><b>{label}</b><span>On-call duty officer</span></div></div>'
        else:
            out += f'<div class="fact">{ICON.get(a, ICON["LOG"])}<div><b>{ACTION_TITLE.get(a, a)}</b><span>{ACTION_SUB.get(a, "")}</span></div></div>'
    return out

def flow_html(ev, stage, gate_live=None, gate_ms=None, llm=None, llm_ms=None, play=False, pending=None, call=None):
    """The process flow. stage: 'pending' (new record, nothing decided yet) | 'gate_running' | 'llm_running' | 'done'.
    gate_live/llm: live results (None = stored values). play: replay reveal (timed CSS); otherwise the stops show exactly how far
    the run has got — nothing past the current stop is ever shown early."""
    gate = gate_live or (None if stage in ("pending", "gate_running") else gate_from_probs(ev.p_fault, ev.p_operational, ev.p_real_event))
    route_known = stage not in ("pending", "gate_running") and ev.route != "PENDING"
    route, reason = flow_route(ev, gate) if route_known else (None, "")
    closer = route == "INVESTIGATE"
    status = {"pending": ["done", "todo", "todo", "todo", "todo", "todo"],
              "gate_running": ["done", "active", "todo", "todo", "todo", "todo"],
              "llm_running": ["done", "done", "done", "active", "todo", "todo"],
              "done": ["done", "done", "done", "done" if closer else "skip", "done", "done"]}[stage]
    delay = [0, .5, 1.2, 1.7, 3.3, 3.8] if closer else [0, .5, 1.2, 0, 1.8, 2.3]       # replay reveal timeline (s)
    vc = VERDICT_COLOR.get(ev.verdict, MUTED)
    unit = "m³/s" if ev.signal == "DISCHARGE" else "m"
    rule = RULE_WORD.get(ev.rule, ev.rule)
    dynamic = getattr(ev, "dynamic", False)

    # cards
    was = (f" · was {ev.value - ev.delta_6h:.3g} six hours earlier" if ev.delta_6h is not None and ev.value is not None and abs(ev.delta_6h) > 1e-9 else "")
    detect = (f'<div class="big">{rule[0].upper() + rule[1:]}</div><div class="ln"><b>{ev.value:.3g} {unit}</b>{was}</div>'
              f'<div class="meta">{nice_name(ev.station_name)} · {("scenario · " if dynamic else "") + fmt(ev.visible_at)}</div>')
    if gate:
        thr = pct(AUTO_RESOLVE_THRESHOLD)
        bars = "".join(f'<div class="pb"><span>{lab}</span><div class="tr"><div class="fl" style="width:{pct(p)}%;background:{col}"></div>'
                       f'<i style="left:{thr}%"></i></div><b>{pct(p)}%</b></div>'
                       for lab, p, col in (("Bad sensor", gate["p_fault"], VERDICT_COLOR["SENSOR_FAULT"]),
                                           ("Dam change", gate["p_operational"], VERDICT_COLOR["OPERATIONAL_CHANGE"]),
                                           ("Real event", gate["p_real_event"], VERDICT_COLOR["NATURAL_EVENT"])))
        quick = bars + f'<div class="meta">Tick = {thr}% cut-off to settle without the LLM</div>'
    else:
        quick = ('<div class="wait">Asking ai_decide…</div>' if stage == "gate_running" else
                 '<div class="meta">Queued for the quick check</div>' if stage == "pending" else '<div class="meta">No quick-check result</div>')
    if route_known:
        routes = (f'<div class="rt {"on" if not closer else "off"}">Auto-resolve</div><div class="rt {"on" if closer else "off"}">Investigate</div>'
                  f'<div class="meta why2">{reason}</div>')
    else:
        routes = '<div class="rt off">Auto-resolve</div><div class="rt off">Investigate</div><div class="meta">Waiting for the quick check</div>'
    conf = (llm or {}).get("confidence", ev.confidence) if closer else None
    used_rain = '"RAIN"' in str((llm or {}).get("evidence") or ev.evidence or "")
    if status[3] == "skip":
        invest = '<div class="meta">Skipped — the quick check was sure enough.</div>'
    elif status[3] == "active":
        invest = '<div class="wait">Reading the evidence…</div>'
    elif status[3] == "done":
        invest = (f'<div class="ln">Weighed gauge history, physical limits, neighbouring gauges{", rainfall" if used_rain else ""}.</div>'
                  + (f'<div class="big sm">{pct(conf)}% sure</div>' if conf is not None else "")
                  + ('<div class="think2"><div class="dots"><span></span><span></span><span></span></div>Reading the evidence…</div>' if play else ""))
    else:
        invest = '<div class="meta">Only if the quick check isn\'t sure.</div>'
    sev = SEVERITY_WORD.get(ev.severity, "")
    verdict = ((f'<div class="pill" style="background:{vc}">{VERDICT_WORD.get(ev.verdict, ev.verdict)}</div>'
                f'<div class="ln">{sev.capitalize() if sev else "No danger flagged"}</div>'
                f'<div class="meta">Decided by {"the LLM" if ev.decided_by == "AGENT_SQL" else "the quick check"}</div>')
               if status[4] == "done" else '<div class="meta">—</div>')
    act = flow_actions_html(ev, pending, call) if status[5] == "done" else '<div class="meta">—</div>'
    ms = lambda v: f"{v:.0f} ms" if v < 1000 else f"{v / 1000:.1f} s"
    lat = [("live", ""), (ms(gate_ms) if gate_ms is not None else "~0.3 s", ""), ("", ""),
           (f"{llm_ms / 1000:.1f} s" if llm_ms is not None else ("~3 s" if closer else ""), ""), ("", ""), ("", "")]
    chips = ["rule-based detector", "ai_decide", f"cut-off {pct(AUTO_RESOLVE_THRESHOLD)}%", "ai_query · LLM", "", ""]
    bodies = [detect, quick, routes, invest, verdict, act]

    nodes, cards = "", ""
    for i, name in enumerate(FLOW_STAGES):
        on_path = not (i == 3 and status[3] == "skip")
        seg_in = "" if i == 0 else ("on" if status[i] in ("done", "active") and on_path else "dim" if not on_path else "")
        seg_out = "" if i == 5 else ("on" if status[i + 1] in ("done", "active") and not (i + 1 == 3 and status[3] == "skip") and on_path else
                                     "dim" if (i + 1 == 3 and status[3] == "skip") or not on_path else "")
        d = f"--d:{delay[i]}s"
        nodes += (f'<div class="nd {status[i]}" style="{d}">'
                  + (f'<span class="sg l {seg_in}" style="--d:{max(0, delay[i] - .35)}s"></span>' if i else "")
                  + (f'<span class="sg r {seg_out}" style="--d:{max(0, delay[i + 1] - .35) if i < 5 else 0}s"></span>' if i < 5 else "")
                  + f'<span class="knob"><svg viewBox="0 0 24 24">{FLOW_ICON[name]}</svg></span></div>')
        cards += (f'<div class="cd {status[i]}" style="{d}"><div class="hd"><span class="nm">{i + 1}. {name}</span><span class="lt">{lat[i][0]}</span></div>'
                  + (f'<span class="chip2">{chips[i]}</span>' if chips[i] else "") + f'<div class="bd">{bodies[i]}</div></div>')
    arc_cls = "on" if route_known and not closer and status[4] == "done" else "dim" if route_known and closer else ""
    arc = (f'<div class="arc {arc_cls}" style="--d:{delay[2] + .2}s"><svg viewBox="0 0 100 40" preserveAspectRatio="none">'
           '<path pathLength="100" vector-effect="non-scaling-stroke" d="M0 38 C0 8 6 6 18 6 L82 6 C94 6 100 8 100 38"/></svg><span>clear-cut</span></div>')

    return (f'<div class="pf{" play" if play else ""}{" quick" if not closer else ""}"><div class="trk">{nodes}{arc}</div>'
            f'<div class="cds">{cards}</div></div>')

def flow_reasoning(ev, cached):
    """The reasoning modal's content: why the record was decided the way it was, with the evidence, numbers and timings."""
    llm = (cached or {}).get("llm")
    gate = (cached or {}).get("gate") or gate_from_probs(ev.p_fault, ev.p_operational, ev.p_real_event)
    vc = VERDICT_COLOR.get(ev.verdict, MUTED)
    why = humanize((llm or {}).get("rationale") or ev.rationale or "")
    evidence = (llm or {}).get("evidence") if llm else _safe_json(ev.evidence)
    route, reason = flow_route(ev, gate)
    src = (("Generated live by" if llm else "From") + " the LLM (ai_query)") if ev.decided_by == "AGENT_SQL" else "Decision note from the quick check (ai_decide)"
    sev = SEVERITY_WORD.get(ev.severity, "")
    head = (f'<div class="mi-head"><span class="pill" style="background:{vc}">{VERDICT_WORD.get(ev.verdict, ev.verdict)}</span>'
            f'<span>{sev.capitalize() + " · " if sev else ""}{nice_name(ev.station_name)} · {RULE_WORD.get(ev.rule, ev.rule)}</span></div>')
    reasoning = f'<div class="mi-sec"><div class="mi-h">Reasoning <span>{src}</span></div><div class="mi-why">{why or "No rationale recorded."}</div></div>'
    rows = "".join(f'<div class="mi-ev"><span class="src">{e.get("source", "")}</span><code>{e.get("tool", "")}</code><span>{humanize(e.get("fact", ""))}</span></div>'
                   for e in (evidence or []) if isinstance(e, dict) and e.get("fact"))
    ev_html = f'<div class="mi-sec"><div class="mi-h">Evidence cited</div>{rows or "<div class=mi-why>None recorded.</div>"}</div>'
    probs = (" · ".join(f"{lab} {pct(gate[k])}%" for lab, k in (("bad sensor", "p_fault"), ("dam change", "p_operational"), ("real event", "p_real_event")))
             if gate else "no result")
    gm, lm = (cached or {}).get("gate_ms"), (cached or {}).get("llm_ms")
    conf = (llm or {}).get("confidence", ev.confidence if ev.decided_by == "AGENT_SQL" else None)
    gate_t = f" · {gm / 1000:.1f} s" if gm else ""
    llm_t = f" · {lm / 1000:.1f} s" if lm else ""
    conf_t = f" · {pct(conf)}% sure" if conf is not None else ""
    route_t = "Investigate" if route == "INVESTIGATE" else "Auto-resolve"
    numbers = (f'<div class="mi-sec"><div class="mi-h">Quick check and route</div><div class="mi-kv"><span>ai_decide</span>{probs}{gate_t}</div>'
               f'<div class="mi-kv"><span>Route</span>{route_t} — {reason}</div>'
               + (f'<div class="mi-kv"><span>ai_query</span>{LLM_MODEL}{llm_t}{conf_t}</div>' if route == "INVESTIGATE" else "")
               + '</div>')
    return head + reasoning + ev_html + numbers

@st.dialog("Why this decision", width="large", on_dismiss=lambda: ss.pop("flow_info", None))
def flow_info_dialog(ev, cached):
    st.markdown(f'<div class="mi">{flow_reasoning(ev, cached)}</div>', unsafe_allow_html=True)
    with st.expander("Model input (the state JSON both models saw)"):
        try:
            st.json(json.loads(ev.agent_state or ev.state_json or "{}"), expanded=1)
        except Exception:
            st.code(ev.agent_state or ev.state_json or "", language="json")

def run_live(ev, paint):
    """Re-run the two models on this record once (live mode, or always for a scenario), painting each stop as its result arrives.
    Caches the results in ss.live_results; for a scenario also decides route, verdict and actions and may start a call."""
    dynamic = getattr(ev, "dynamic", False)
    closer = ev.route == "INVESTIGATE"
    paint("gate_running")
    try:
        gate, gate_ms = run_gate_live(ev.state_json)
    except Exception as e:
        gate, gate_ms = None, None
        st.caption(f"Live ai_decide unavailable ({str(e)[:90]}); showing the stored result.")
    if dynamic:                                         # a scenario: the route is decided now, from the live gate
        ev.route = decide_route(ev, gate); closer = ev.route == "INVESTIGATE"
    llm, llm_ms = None, None
    if closer:
        paint("llm_running", gate, gate_ms)
        try:
            llm, llm_ms = run_llm_live(ev.agent_state or ev.state_json)
        except Exception as e:
            st.caption(f"Live ai_query unavailable ({str(e)[:90]}); showing the stored reasoning.")
    if dynamic:                                         # verdict, severity and actions from the same rules as the pipeline
        finalize(ev, gate, llm)
        if "PAGE" in ev.actions and ss.pending_call is None:
            on_page(ev); ss.paged.add(ev.candidate_id)
    ss.live_results[ev.candidate_id] = {"gate": gate, "gate_ms": gate_ms, "llm": llm, "llm_ms": llm_ms}
    pc = ss.pending_call
    if pc is not None and pc["candidate_id"] == ev.candidate_id:   # the countdown starts only once the decision is on screen
        pc["deadline"] = time.time() + ss.countdown_s
    ss.hero_shown_at = 0                                # nothing left to reveal; reruns may proceed
    return ss.live_results[ev.candidate_id]

def call_controls(ev, pending, col):
    """Cancel / Call now during the countdown, Dismiss afterwards, in `col` (under the Act card's call tile); places (or simulates)
    the call when the countdown reaches zero. Streamlit buttons can't live in the HTML."""
    if pending is None:
        return
    remaining = int(round(pending["deadline"] - time.time()))
    b2, b3 = col.columns(2, gap="small")
    if remaining > 0:
        if b2.button("Cancel", width="stretch", key="cancel_call"):
            ss.calls[ev.candidate_id] = {"status": "CANCELLED", "result": "cancelled by operator"}
            ss.pending_call = None
            st.rerun()
        if b3.button("Call now", width="stretch", type="primary", key="call_now"):
            pending["deadline"] = time.time()
            st.rerun()
    else:
        if ev.candidate_id not in ss.calls:
            if ss.real_calls:
                contacts = [os.environ["CONTACT_1"]] + ([os.environ["CONTACT_2"]] if escalate and os.environ.get("CONTACT_2") else [])
                place_call(pending["alert"], contacts, escalate, ev.candidate_id)
            else:
                ss.calls[ev.candidate_id] = {"status": "SIMULATED", "result": "simulated call (real calls off)"}
            st.rerun()
        if ss.calls[ev.candidate_id]["status"] != "CALLING" and b3.button("Dismiss", width="stretch", key="dismiss_call"):
            ss.pending_call = None
            st.rerun()

# ----------------------------------------------------------------------------- state
events, stations, cases = derive(load_events()), load_stations(), load_cases()
golden, plan = load_results()
T_MIN = datetime(2026, 9, 1, tzinfo=MDT)       # seek range is pinned, in MDT like every displayed time
T_MAX = datetime(2026, 10, 2, tzinfo=MDT)

SYSTEM_NAME = {"AB": "Bow & Elbow", "NS": "Cape Breton"}
SYSTEM_LABEL = {p: f"{p} - {n}" for p, n in SYSTEM_NAME.items()}
SPEED_LABEL = {5: "5 min/s", 15: "15 min/s", 30: "30 min/s", 60: "1 hr/s", 120: "2 hr/s", 180: "3 hr/s"}
DELAY_S = [5, 10, 15, 30]
SCENARIO_LIST = sorted(SCENARIOS, key=lambda s_: s_["name"])

ss = st.session_state
ss.setdefault("t", T_MIN); ss.setdefault("playing", False); ss.setdefault("speed_min", 15)
ss.setdefault("paged", set()); ss.setdefault("pending_call", None); ss.setdefault("calls", {}); ss.setdefault("countdown_s", 10)
voice_ready = pager is not None and all(os.environ.get(k) for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "CONTACT_1"))
ss.setdefault("real_calls", False)                 # real calls are opt-in, every session
if not voice_ready: ss.real_calls = False
ss.setdefault("live_mode", False); ss.setdefault("live_results", {})
ss.setdefault("nav_at", 0.0)
ss.setdefault("scenario_ev", None); ss.setdefault("scenario_fresh", False)
SETTLE_S = 1.5   # the playhead must rest this long before live calls or a countdown start (lets you skip past events quickly)

def nav():
    """Any navigation: restart the settle timer and drop a countdown that has not dialled yet (the operator moved on)."""
    ss.nav_at = time.time()
    pc = ss.pending_call
    if pc and ss.calls.get(pc["candidate_id"], {}).get("status") not in ("CALLING", "DONE"):
        ss.calls[pc["candidate_id"]] = {"status": "CANCELLED", "result": "navigated away before the call was placed"}
        ss.pending_call = None

def seek(t):
    """Move the playhead (and leave any scenario). Going back re-arms calls for records after the new time, so they can page again."""
    t = min(max(t, T_MIN), T_MAX)
    back = t < ss.t
    nav()
    if back:
        later = set(events.loc[events.visible_at > t, "candidate_id"])
        ss.paged -= later
        ss.calls = {k: v for k, v in ss.calls.items() if k not in later}
    ss.t, ss.playing, ss.scenario_ev = t, False, None

def inline(label: str):
    """A label and a control on one line; returns the control's column."""
    a, b = st.columns([1, 1.45], vertical_alignment="center")
    a.markdown(f'<div class="devlab">{label}</div>', unsafe_allow_html=True)
    return b

# ----------------------------------------------------------------------------- dev panel (sidebar)
with st.sidebar:
    st.markdown('<div class="devtitle">Dev Panel</div>', unsafe_allow_html=True)

    # 1A. system / station
    st.markdown('<div class="devsec">System / Station</div>', unsafe_allow_html=True)
    systems = sorted(stations.province.unique(), key=lambda p: SYSTEM_LABEL.get(p, p))
    system = st.selectbox("System", systems, format_func=lambda p: SYSTEM_LABEL.get(p, p), key="system", on_change=nav, label_visibility="collapsed")
    in_system = stations[stations.province == system]
    station_label = {r.station_id: f"{nice_name(r.name)} ({r.station_id})" for r in in_system.itertuples()}
    picked = st.multiselect("Station", sorted(station_label, key=station_label.get), format_func=station_label.get,
                            key=f"stations_{system}", placeholder="All stations", on_change=nav, label_visibility="collapsed")
    lanes = in_system[in_system.station_id.isin(picked)] if picked else in_system
    ev_lanes = events[events.station_id.isin(lanes.station_id)]

    # 1B. record playback
    st.markdown('<div class="devsec">Record Playback</div>', unsafe_allow_html=True)
    def transport(action):
        """Player buttons (runs as a callback, before the script): previous / next record in the selection, play-pause."""
        if action == "prev":
            cur = ev_lanes.loc[ev_lanes.visible_at <= ss.t, "visible_at"]
            prev = ev_lanes.loc[ev_lanes.visible_at < cur.max(), "visible_at"] if len(cur) else cur
            seek(prev.max().to_pydatetime() + timedelta(minutes=1) if len(prev) else T_MIN)
        elif action == "next":
            nxt = ev_lanes.loc[ev_lanes.visible_at > ss.t, "visible_at"]
            seek(nxt.min().to_pydatetime() + timedelta(minutes=1) if len(nxt) else T_MAX)
        elif action == "toggle":
            playing = not ss.playing
            if playing and ss.t >= T_MAX: seek(T_MIN)
            ss.playing, ss.scenario_ev = playing, None
    cur_rec = ev_lanes.loc[ev_lanes.visible_at <= ss.t, "visible_at"]
    player(T_MIN, T_MAX, ss.t, ev_lanes.visible_at, cur_rec.max() if len(cur_rec) else None, ss.playing,
           key="player", on_seek=seek, on_action=transport)
    inline("Speed").selectbox("Speed", list(SPEED_LABEL), format_func=SPEED_LABEL.get, key="speed_min", label_visibility="collapsed")
    inline("Live Model").toggle("Live Model", key="live_mode", label_visibility="collapsed",
                                help=f"Re-run ai_decide and ai_query ({LLM_MODEL}) on the warehouse for each new decision, with real timings. "
                                     "Off = replay the stored results. Scenarios always run live.")
    inline("Place Call").toggle("Place Call", key="real_calls", disabled=not voice_ready, label_visibility="collapsed",
                                help=None if voice_ready else "Needs page.py plus ElevenLabs, Twilio and CONTACT_1 settings")
    inline("Call Delay").selectbox("Call Delay", DELAY_S, format_func=lambda s_: f"{s_} s", key="countdown_s", label_visibility="collapsed")
    if not voice_ready:
        missing = [k for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "CONTACT_1") if not os.environ.get(k)]
        st.caption("Calls are simulated. " + ("page.py not found next to the app. " if pager is None else "") + (f"Missing in .env: {', '.join(missing)}" if missing else ""))

    # 1C. scenarios
    st.markdown('<div class="devsec">Scenarios</div>', unsafe_allow_html=True)
    names = [s_["name"] for s_ in SCENARIO_LIST]
    sc = SCENARIO_LIST[names.index(st.selectbox("Scenario", names, key="scenario_pick", label_visibility="collapsed"))]
    st.caption(f"Expected: {sc['expect']} · {'illustrative numbers' if sc['source'] == 'illustrative' else 'numbers from the September data'}")
    if st.button("Run scenario", type="primary", width="stretch"):
        if sc["gauge"] in set(stations.station_id):
            stn = stations.set_index("station_id").loc[sc["gauge"]].to_dict(); stn["station_id"] = sc["gauge"]
            nav()                                       # drops any undialled countdown from the replay or a previous scenario
            ss.scenario_ev, ss.scenario_fresh, ss.playing = scenario_event(sc, stn, stations.station_id), True, False
        else:
            st.error(f"Gauge {sc['gauge']} is not in {CATALOG}.silver.station_context.")

    with st.expander("More options"):
        escalate = st.toggle("If no answer, call the second contact", value=bool(os.environ.get("CONTACT_2")), disabled=not os.environ.get("CONTACT_2"))
        if voice_ready:
            st.caption("Will call " + pager.mask(os.environ["CONTACT_1"]) + (f", then {pager.mask(os.environ['CONTACT_2'])}" if escalate and os.environ.get("CONTACT_2") else ""))

    # readout: the panel's "stats for nerds"
    last = ev_lanes[ev_lanes.visible_at <= ss.t].tail(1)
    rows = [("t", pd.Timestamp(ss.t).tz_convert(MDT).strftime("%Y-%m-%d %H:%M MDT")),
            ("records", f"{len(ev_lanes)} · {int((ev_lanes.visible_at <= ss.t).sum())} seen"),
            ("current", f"{last.station_id.iloc[0]} · {last.candidate_id.iloc[0]}" if len(last) else "–"),
            ("state", ("playing" if ss.playing else "paused") + (" · scenario" if ss.scenario_ev is not None else "")
                      + (" · call pending" if ss.pending_call else "") + (" · live" if ss.live_mode else " · stored"))]
    st.markdown('<div class="devstats">' + "".join(f'<div><span>{k}</span>{v}</div>' for k, v in rows) + '</div>', unsafe_allow_html=True)

T = ss.t
known = ev_lanes[ev_lanes["visible_at"] <= T]
seen = known
upcoming = ev_lanes[ev_lanes["visible_at"] > T]
just_now = known[known["visible_at"] > T - timedelta(minutes=max(ss.speed_min, 5) + 1)]
scenario = ss.scenario_ev

settled = (time.time() - ss.nav_at) >= SETTLE_S

for ev in (just_now.sort_values("visible_at", ascending=False).itertuples() if settled and scenario is None else []):
    if "PAGE" in ev.actions and ev.candidate_id not in ss.paged:
        ss.paged.add(ev.candidate_id); ss.playing = False
        if ss.pending_call is None:                     # one call per event even if several detectors fired together
            on_page(ev)

latest = None                                   # the current record: newest one seen, or the one a call belongs to
if len(seen):
    latest = seen.sort_values("visible_at").iloc[-1]
    if ss.pending_call and (seen.candidate_id == ss.pending_call["candidate_id"]).any():
        latest = seen[seen.candidate_id == ss.pending_call["candidate_id"]].iloc[0]   # the card must be the one the call belongs to

# ----------------------------------------------------------------------------- dashboard (new, in development)
def flow_spark(station_id, t_end, hours=24, max_pts=96):
    """Recent flow at a station for the map label: (values, label) or (None, None) without discharge data."""
    rd = load_readings(station_id, "DISCHARGE")
    d = rd[(rd.ts_utc > t_end - timedelta(hours=hours)) & (rd.ts_utc <= t_end)].value.dropna()
    if len(d) < 2:
        return None, None
    step = -(-len(d) // max_pts)                 # ceil: at most max_pts points
    vals = [float(v) for v in d.iloc[::step]]
    if (len(d) - 1) % step: vals.append(float(d.iloc[-1]))          # always end on the latest reading
    return vals, f"{d.iloc[-1]:.3g} m³/s · last {hours} h"

STAT_ICON = {   # 24x24 stroke icons, coloured by the card
    "anomalies": '<path d="M2 13h4l2.2-6 3.3 11 2.4-8 1.6 3H22"/>',
    "auto": '<path d="M12.5 2 5 13h6l-1 9 4.2-6"/><path d="m15 18 2.2 2.2L22 15.5"/>',
    "tickets": '<path d="M14.7 6.3a4 4 0 0 0 5 5l-9.4 9.4a2.1 2.1 0 0 1-3-3l9.4-9.4z"/><path d="M14.7 6.3 17 4"/>',
    "pages": '<path d="M19.5 15.8v2.6a1.8 1.8 0 0 1-2 1.8 17.5 17.5 0 0 1-7.6-2.7 17.2 17.2 0 0 1-5.3-5.3 17.5 17.5 0 0 1-2.7-7.6 1.8 1.8 0 0 1 1.8-2h2.6a1.8 1.8 0 0 1 1.8 1.5c.1.8.3 1.6.6 2.3a1.8 1.8 0 0 1-.4 1.9L7.2 9.4a14 14 0 0 0 5.3 5.3l1.1-1.1a1.8 1.8 0 0 1 1.9-.4c.7.3 1.5.5 2.3.6a1.8 1.8 0 0 1 1.7 1.8z"/>'
             '<path d="M15 2.5a6.5 6.5 0 0 1 6.5 6.5"/><path d="M15 6a3 3 0 0 1 3 3"/>',
}

def summary_cards(seen: pd.DataFrame, height: int) -> str:
    """Four stacked summary cards (icon, label, value, context line, definition tooltip) filling `height` px."""
    n = len(seen)
    auto = int((seen.route == "AUTO_RESOLVE").sum())
    tickets, quarantined = int(has(seen, "TICKET").sum()), int(has(seen, "QUARANTINE").sum())
    paged = seen[has(seen, "PAGE")]
    cards = [
        ("anomalies", "Anomalies", n, f"across {seen.station_id.nunique()} gauge{'s' if seen.station_id.nunique() != 1 else ''}" if n else "none yet", INK,
         "Readings the rule-based detector flagged as unusual — a spike, flatline, step, fast rise, gap or impossible value — up to the time shown."),
        ("auto", "Auto-resolved", f"{auto / n * 100:.0f}%" if n else "–", f"{auto} of {n}" if n else "nothing to settle yet", VERDICT_COLOR["NATURAL_EVENT"],
         "Share of anomalies the quick AI check (ai_decide) settled confidently on its own, without the slower LLM investigation."),
        ("tickets", "Field tickets", tickets, f"{quarantined} reading{'s' if quarantined != 1 else ''} quarantined", VERDICT_COLOR["SENSOR_FAULT"],
         "Bad-sensor cases sent to a field technician. Their readings are quarantined so they don't skew downstream models."),
        ("pages", "On-call pages", int(paged.event_key.nunique()), f"last: {fmt(paged.visible_at.max())}" if len(paged) else "no calls yet", "#d03b3b",
         "Phone calls to the on-call duty officer for likely real floods — counted once per gauge per event."),
    ]
    html = "".join(
        f'<div class="stat"><div class="ic" style="color:{col}"><svg viewBox="0 0 24 24" aria-hidden="true">{STAT_ICON[k]}</svg></div>'
        f'<div class="tx"><div class="lb">{lab}</div><div class="vl">{val}</div><div class="sb">{sub}</div></div>'
        f'<span class="tip" tabindex="0" role="button" aria-label="What is {lab}?">?<span class="tt" role="tooltip">{tip}</span></span></div>'
        for k, lab, val, sub, col, tip in cards)
    return f'<div class="stats" style="height:{height}px">{html}</div>'

if scenario is not None:                        # which station the map highlights
    cur_id = scenario.station_id if scenario.station_id in set(in_system.station_id) else None
elif latest is not None:
    cur_id = latest.station_id
else:
    cur_id = picked[0] if len(picked) == 1 else None
current = None
if cur_id is not None:
    by_id = in_system.set_index("station_id")
    r = by_id.loc[cur_id]
    spark, spark_label = (None, f"Scenario reading {scenario.value:.3g} m³/s") if scenario is not None else flow_spark(cur_id, T)
    current = {"id": cur_id, "name": nice_name(r["name"]), "lat": float(r.lat), "lon": float(r.lon), "spark": spark, "spark_label": spark_label,
               "neighbours": []}
    # its three nearest gauges (nearby_ids, by distance). In a scenario there are no readings at that moment, so show the scenario's input instead.
    scen_pct = neighbour_pcts(scenario.state_json) if scenario is not None else {}
    for nid, km in sorted(_nearby(r.get("nearby_ids")), key=lambda x: x[1])[:3]:
        if nid not in by_id.index or nid == cur_id:
            continue
        n = by_id.loc[nid]
        if scenario is not None:
            n_spark, n_label = None, (f"Scenario: {scen_pct[nid] * 100:+.0f}% in 24 h" if nid in scen_pct else None)
        else:
            n_spark, n_label = flow_spark(nid, T)
        current["neighbours"].append({"id": nid, "name": nice_name(n["name"]), "lat": float(n.lat), "lon": float(n.lon), "km": km,
                                      "spark": n_spark, "spark_label": n_label})

st.markdown(f'<div class="dash-head"><div class="dash-title">{system}<span class="chev">›</span>{SYSTEM_NAME.get(system, system)} River System</div>'
            f'<div class="dash-brand"><div class="logo">River Sentinel</div><div class="clock">{fmt(T)} MDT</div></div></div>', unsafe_allow_html=True)
MAP_H = 380                                          # map height; the summary cards fill the same height beside it
map_col, stats_col = st.columns([3.2, 1], gap="medium")   # the map fills its column; station panels overlay its right edge
with map_col:
    in_sel = set(lanes.station_id)
    system_map(system, [{"id": r.station_id, "name": nice_name(r.name), "lat": float(r.lat), "lon": float(r.lon), "on": r.station_id in in_sel}
                        for r in in_system.itertuples()], current, key="system_map", height=MAP_H)
with stats_col:
    stats_col.markdown(summary_cards(seen, MAP_H + 2), unsafe_allow_html=True)

# ----------------------------------------------------------------------------- process flow (new): how the current record is being handled
flow_ev, fresh = None, False                    # the record on show, and whether it just arrived (plays its reveal / runs live models)
if scenario is not None:
    flow_ev, fresh = scenario, ss.scenario_fresh
elif latest is not None:
    flow_ev = latest
    fresh = settled and ss.get("last_hero") != latest.candidate_id
    if settled:
        ss.last_hero = latest.candidate_id
    if fresh:
        ss.hero_shown_at = time.time()          # the reveal runs ~5 s; reruns are held off until it has played

h1, h2 = st.columns([5, 1], vertical_alignment="bottom")
mode_txt = "scenario · models run live" if scenario is not None else ("models re-run live" if ss.live_mode else "stored results")
h1.markdown(f'<div class="sec-head">Latest record<span>{mode_txt}</span></div>', unsafe_allow_html=True)
if scenario is not None and h2.button("← Back to replay", key="back_to_replay", width="stretch"):
    nav(); ss.scenario_ev = None; st.rerun()
if flow_ev is None:
    st.markdown(f'<div class="idle"><div class="dot"></div><div><b>Watching {len(lanes)} gauge{"s" if len(lanes) != 1 else ""}</b> in '
                f'{SYSTEM_LABEL.get(system, system)}. Nothing unusual so far. Press <b>next ▸▸</b> in the Dev Panel to move to the first odd reading.</div></div>',
                unsafe_allow_html=True)
else:
    flow_slot = st.empty()
    def flow_pending():
        pc = ss.pending_call
        return pc if (pc and pc["candidate_id"] == flow_ev.candidate_id) else None
    def paint_flow(stage, gate=None, gate_ms=None, llm=None, llm_ms=None, play=False):
        flow_slot.markdown(flow_html(flow_ev, stage, gate, gate_ms, llm, llm_ms, play=play, pending=flow_pending(),
                                     call=ss.calls.get(flow_ev.candidate_id)), unsafe_allow_html=True)
    cached = ss.live_results.get(flow_ev.candidate_id)
    if (ss.live_mode or getattr(flow_ev, "dynamic", False)) and fresh and cached is None:
        cached = run_live(flow_ev, paint_flow)  # the only place the models are re-run
    queued = cached is None and scenario is None and not settled and ss.get("last_hero") != flow_ev.candidate_id
    if cached is not None:
        paint_flow("done", cached["gate"], cached["gate_ms"], cached["llm"], cached["llm_ms"])
    elif queued:
        paint_flow("pending")                   # a new record while the playhead settles: nothing decided yet, nothing given away
    else:
        paint_flow("done", play=fresh)          # stored results, with the timed reveal when the record just arrived
    # buttons, lined up with the stops and lifted into the bottom of the Verdict and Act cards
    played = not queued and cached is None and fresh  # during the replay reveal the buttons fade in with the Verdict stop
    with st.container(key="flow_buttons" + ("_play" + ("" if flow_ev.route == "INVESTIGATE" else "q") if played else "")):
        bcols = st.columns(6, gap="small")
        if not queued and bcols[4].button("More information", key="flow_info_btn", type="tertiary", icon=":material/info:"):
            ss.flow_info, ss.playing = flow_ev.candidate_id, False
        call_controls(flow_ev, flow_pending(), bcols[5])
    if ss.get("flow_info") == flow_ev.candidate_id:
        flow_info_dialog(flow_ev, cached)
    elif ss.get("flow_info"):
        ss.flow_info = None                     # the record changed: don't reopen on an old one
    if scenario is not None:
        st.caption(f"Route cut-off {AUTO_RESOLVE_THRESHOLD:.2f} (round R2) · neighbours count as moving above {int(NEIGHBOUR_MOVE_PCT*100)} % · "
                   f"model {LLM_MODEL}. The scenario is not written to the tables.")
    elif not settled:
        st.caption("Moving… the live check starts when the playhead rests.")

st.markdown('<div class="legacy-sep">Legacy dashboard — being phased out</div>', unsafe_allow_html=True)

# ----------------------------------------------------------------------------- header
st.caption("RiverSentinel · river gauge watch, replayed over September 2026. The screen shows only what the system knew at the time shown.")
st.markdown(f"## {fmt(T)}", unsafe_allow_html=True)
m = st.columns(4)
m[0].metric("Odd readings spotted", len(seen))
m[1].metric("Settled instantly", f"{(seen.route == 'AUTO_RESOLVE').mean()*100:.0f}%" if len(seen) else "–",
            help="Decided by a quick check, without AI reasoning")
m[2].metric("Sent to a technician", int(has(seen, "TICKET").sum()), help="Bad-sensor tickets; readings flagged so they do not poison downstream models")
m[3].metric("Calls to the on-call person", int(seen[has(seen, "PAGE")].event_key.nunique()), help="One call per gauge per event")

# ----------------------------------------------------------------------------- live decision (the highlight)
if flow_ev is not None:                         # same record as the process flow above; display only
    pc = ss.pending_call
    hero(flow_ev, fresh, pc if (pc and pc["candidate_id"] == flow_ev.candidate_id) else None, ss.calls.get(flow_ev.candidate_id))
    if scenario is not None:
        ss.scenario_fresh = False
else:
    # nothing seen yet: quiet sentry state
    st.markdown(f'<div class="idle"><div class="dot"></div><div><b>Watching {len(lanes)} gauge{"s" if len(lanes) != 1 else ""}</b> in '
                f'{SYSTEM_LABEL.get(system, system)}. Nothing unusual so far. Press <b>next ▸▸</b> in the Dev Panel to move to the first odd reading.</div></div>', unsafe_allow_html=True)
    station_map(lanes, None, height=320)

# ----------------------------------------------------------------------------- timeline
lane_order = list(lanes.station_id)
lane_label = {r.station_id: nice_name(r.name) for r in lanes.itertuples()}
fig = go.Figure()
cases_l = cases[cases.station_id.isin(lane_order)]
for k in cases_l.itertuples():
    y = lane_order.index(k.station_id)
    fig.add_shape(type="rect", x0=mdt(k.win_start_utc), x1=mdt(k.win_end_utc), y0=y - 0.45, y1=y + 0.45, fillcolor=GRID, opacity=0.6, line_width=0, layer="below")
    fig.add_annotation(x=mdt(k.win_start_utc), y=y + 0.42, text=k.case_id, showarrow=False, font=dict(size=10, color=INK2), xanchor="left", yanchor="bottom")
if len(upcoming):
    fig.add_trace(go.Scatter(x=mdt(upcoming.visible_at), y=[lane_order.index(s) for s in upcoming.station_id], mode="markers",
                             marker=dict(size=6, color=VERDICT_COLOR["PENDING"]), name="Not yet happened",
                             hovertemplate="not yet happened<extra></extra>"))
for verdict, color in VERDICT_COLOR.items():
    if verdict == "PENDING": continue
    d = seen[seen.verdict == verdict]
    if not len(d): continue
    fig.add_trace(go.Scatter(
        x=mdt(d.visible_at), y=[lane_order.index(s) for s in d.station_id], mode="markers", name=VERDICT_WORD[verdict],
        marker=dict(size=[16 if a == "PAGE" else 11 for a in d.primary_action], color=color, line=dict(width=2, color=SURFACE),
                    symbol=[ACTION_SYMBOL.get(a, "circle") for a in d.primary_action]),
        customdata=list(zip([nice_name(n) for n in d.station_name], [RULE_WORD.get(r, r) for r in d.rule], [ACTION_WORD.get(a, a) for a in d.primary_action])),
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>" + VERDICT_WORD[verdict] + " → %{customdata[2]}<extra></extra>"))
fig.add_vline(x=mdt(T), line_width=2, line_color=INK)
fig.update_layout(height=max(240, 28 * len(lane_order) + 60), margin=dict(l=10, r=10, t=10, b=10),
                  paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(color=INK2, family="system-ui, -apple-system, Segoe UI, sans-serif"),
                  legend=dict(orientation="h", y=-0.1, font=dict(color=INK2)),
                  xaxis=dict(range=[mdt(T_MIN), mdt(T_MAX)], gridcolor=GRID, linecolor=AXIS, tickfont=dict(color=MUTED)),
                  yaxis=dict(tickmode="array", tickvals=list(range(len(lane_order))), ticktext=[lane_label[s] for s in lane_order],
                             autorange="reversed", gridcolor=GRID, tickfont=dict(size=11, color=INK2)))
st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
st.caption("One row per river gauge. Each dot is a reading the system thought was odd. Colour: what it decided. "
           "Shape: what it did — star = called someone · cross = flagged bad data · square = sent a technician · circle = just noted it. "
           "Grey bands are the events we know really happened. Grey dots have not happened yet.")

# ----------------------------------------------------------------------------- decisions + gauge
left, right = st.columns([1.15, 1])
with left:
    st.subheader("Earlier decisions")
    earlier = seen.sort_values("visible_at", ascending=False).iloc[1:13]
    if not len(earlier):
        st.caption("Only one decision so far.")
    for ev in earlier.itertuples():
        vc = VERDICT_COLOR.get(ev.verdict, MUTED)
        acts = " · ".join(ACTION_TITLE.get(a, a) for a in ev.actions) or "Logged"
        st.markdown(f'<div class="card" style="border-left:5px solid {vc};padding:10px 14px">'
                    f'<div style="color:#52514e;font-size:.85rem">{fmt(ev.visible_at)}</div>'
                    f'<div><b>{nice_name(ev.station_name)}</b> — {VERDICT_WORD.get(ev.verdict, ev.verdict)}'
                    f'<span style="color:#52514e"> · {RULE_WORD.get(ev.rule, ev.rule)}</span></div>'
                    f'<div style="color:#0b0b0b;margin-top:2px">{acts}</div></div>', unsafe_allow_html=True)

with right:
    focus = known.iloc[-1].station_id if len(known) else lane_order[0]
    st.subheader(lane_label.get(focus, focus))
    sig = st.radio("Measure", ["DISCHARGE", "LEVEL"], horizontal=True, label_visibility="collapsed",
                   format_func=lambda s: "Flow (m³/s)" if s == "DISCHARGE" else "Water level (m)")
    rd = load_readings(focus, sig)
    win_start = T - timedelta(hours=36)
    d = rd[(rd.ts_utc >= win_start) & (rd.ts_utc <= T)]
    f2 = go.Figure()
    f2.add_trace(go.Scatter(x=mdt(d.ts_utc), y=d.value, mode="lines", line=dict(width=2, color=VERDICT_COLOR["NATURAL_EVENT"]), name="Reading",
                            hovertemplate="%{x|%b %d %H:%M} · %{y:.3g}<extra></extra>"))
    qd = d[d.is_quarantined == True]
    if len(qd):
        f2.add_trace(go.Scatter(x=mdt(qd.ts_utc), y=qd.value, mode="markers", name="Flagged as bad data",
                                marker=dict(symbol="x", size=10, color=VERDICT_COLOR["SENSOR_FAULT"], line=dict(width=1, color=SURFACE))))
    for ev in known[(known.station_id == focus) & (known.ts_end_utc >= win_start)].itertuples():
        f2.add_vrect(x0=mdt(ev.ts_start_utc), x1=mdt(max(ev.ts_end_utc, ev.ts_start_utc + timedelta(minutes=10))),
                     fillcolor=VERDICT_COLOR.get(ev.verdict, MUTED), opacity=0.15, line_width=0)
    f2.add_vline(x=mdt(T), line_width=2, line_color=INK)
    f2.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, showlegend=len(qd) > 0,
                     font=dict(color=INK2, family="system-ui, -apple-system, Segoe UI, sans-serif"),
                     xaxis=dict(type="date", range=[mdt(win_start), mdt(T)], gridcolor=GRID, linecolor=AXIS, tickfont=dict(color=MUTED)),
                     yaxis=dict(title="m³/s" if sig == "DISCHARGE" else "m", gridcolor=GRID, tickfont=dict(color=MUTED), rangemode="tozero"),
                     hovermode="x unified")
    st.plotly_chart(f2, width="stretch", config={"displayModeBar": False})
    st.caption("Last 36 hours up to the time shown. Shaded = the odd readings, coloured by decision. Crosses = readings flagged as bad data.")

# ----------------------------------------------------------------------------- footer: two numbers and one sentence
done = golden[golden.case_id.isin(cases_l[cases_l.win_end_utc <= T].case_id)] if len(golden) else golden
if len(done):
    agent_ok = int(done.outcome_ok.fillna(False).astype(bool).sum())
    st.markdown(f"**Known events so far: {len(done)}** — real floods, broken sensors and dam releases that we verified by hand. "
                f"The system handled **{agent_ok}** of them correctly.")
if len(plan):
    with st.expander("How the system improved itself"):
        for r in plan.itertuples():
            st.markdown(f"After the first pass it reviewed its own misses — {r.trigger.replace('R1: ', '')} — and changed one setting "
                        f"(`{r.change}`). The second pass applied that change and nothing else.")

# ----------------------------------------------------------------------------- clock
REVEAL_S = 5.2
def _hold_for_reveal():
    """A rerun replaces the card's HTML and would cut the CSS reveal short; wait until it has played."""
    left = REVEAL_S - (time.time() - ss.get("hero_shown_at", 0))
    if left > 0: time.sleep(left)

if not settled and len(seen):
    time.sleep(max(0.05, SETTLE_S - (time.time() - ss.nav_at))); st.rerun()
elif ss.pending_call and (ss.pending_call["deadline"] > time.time() or ss.calls.get(ss.pending_call["candidate_id"], {}).get("status") == "CALLING"):
    _hold_for_reveal(); time.sleep(1.0); st.rerun()   # countdown / dialling: refresh once a second, clock stays paused
elif ss.playing:
    _hold_for_reveal(); time.sleep(1.0)
    ss.t = min(ss.t + timedelta(minutes=ss.speed_min), T_MAX)
    if ss.t >= T_MAX: ss.playing = False
    st.rerun()
