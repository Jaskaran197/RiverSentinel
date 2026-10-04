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
from datetime import timedelta
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
from logic import (derive, has, fmt, _safe_json, _ids, _nearby, neighbour_pcts, AUTO_RESOLVE_THRESHOLD, NEIGHBOUR_MOVE_PCT,
                   build_custom_event, decide_route, finalize, pct, nice_name, humanize)

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

st.set_page_config(page_title="RiverSentinel", layout="wide")
st.markdown("""<style>
.block-container{padding-top:1rem;padding-bottom:2rem}
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
        view = pdk.ViewState(latitude=float(st_df.lat.mean()), longitude=float(st_df.lon.mean()), zoom=3.0, pitch=0)
    deck = pdk.Deck(layers=layers, initial_view_state=view, map_style="light", tooltip={"text": "{label}"})
    try:
        st.pydeck_chart(deck, use_container_width=True, height=height)
    except TypeError:                       # older Streamlit without the height argument
        st.pydeck_chart(deck, use_container_width=True)

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

# ----------------------------------------------------------------------------- try your own event (same models, same rules, nothing stored)
def custom_event_form():
    """The form. Returns a dynamic event on submit, else None."""
    st.subheader("Try your own event")
    st.caption("Describe a reading at any gauge. The same two model calls run on the warehouse and the same rules decide what happens. Nothing is stored.")
    with st.form("custom_event"):
        c1, c2, c3 = st.columns([1.3, 1, 1])
        sid = c1.selectbox("Gauge", list(stations.station_id), format_func=lambda s_: f"{nice_name(stations.set_index('station_id').loc[s_, 'name'])} · {s_}")
        signal = c2.selectbox("Measure", ["DISCHARGE", "LEVEL"], format_func=lambda s_: "Flow (m³/s)" if s_ == "DISCHARGE" else "Water level (m)")
        rule_override = c3.selectbox("Spotted as", ["auto", "RAPID_RISE", "STEP", "SPIKE", "PHYSICAL_BREACH", "GAP", "FLATLINE"],
                                     format_func=lambda r: "auto (from the numbers)" if r == "auto" else RULE_WORD.get(r, r))
        c4, c5, c6, c7 = st.columns(4)
        value = c4.number_input("Reading now", value=0.0, step=0.1, format="%.3f")
        prev6 = c5.number_input("Reading 6 hours ago", value=70.0, step=0.1, format="%.3f")
        rain24 = c6.number_input("Rain, last 24 h (mm)", value=0.0, min_value=0.0, step=1.0)
        rain48 = c7.number_input("Rain, last 48 h (mm)", value=0.0, min_value=0.0, step=1.0)
        c8, c9, c10, c11 = st.columns(4)
        gap_min = c8.number_input("Minutes with no data", value=0.0, min_value=0.0, step=5.0)
        flatline_len = c9.number_input("Identical readings in a row", value=0, min_value=0, step=1)
        gauge_trust = c10.slider("Rain-gauge trust", 0.0, 1.0, 1.0, 0.05)
        spike = c11.checkbox("Single reading that jumped and came back")
        stn = stations.set_index("station_id").loc[sid].to_dict(); stn["station_id"] = sid
        nb_ids = [i for i in _ids(stn.get("upstream_ids")) + _ids(stn.get("downstream_ids"))] + [i for i, _ in _nearby(stn.get("nearby_ids"))]
        nb_ids = [i for i in dict.fromkeys(nb_ids) if i in set(stations.station_id)]
        st.markdown("**Neighbouring gauges — change over the last 24 hours (%)**")
        nb_cols = st.columns(max(1, min(len(nb_ids), 6)))
        nb_pct = {}
        for k, i in enumerate(nb_ids[:6]):
            nm = nice_name(stations.set_index("station_id").loc[i, "name"])
            nb_pct[i] = nb_cols[k % len(nb_cols)].number_input(nm, value=0, min_value=-100, max_value=1000, step=5, key=f"nb_{i}") / 100.0
        go_ = st.form_submit_button("Run the agent", type="primary")
    if go_:
        return build_custom_event(stn, signal, float(value), float(prev6), float(rain24), float(rain48), float(gauge_trust),
                                  float(gap_min), int(flatline_len), bool(spike), nb_pct, rule_override)
    return None

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
            head_slot.markdown(f'<div class="hero {anim}" style="border-left-color:{vc_}"><div class="when">{"Your event · " if dynamic else ""}{fmt(ev.visible_at)}</div><div class="where">{station}</div>'
                               f'<div class="odd">{odd_cap}: <b>{value_txt}</b>{was}.</div>'
                               f'<span class="verdict" style="background:{vc_}">{badge_txt}</span></div>', unsafe_allow_html=True)
        paint_head()
        station_map(stations, ev.station_id, vc, neighbour_pcts(ev.state_json), height=240)
        st.caption("Neighbouring gauges in the last 24 hours: blue rising, orange falling, grey steady. Solid line = same river.")
        rd = load_readings(ev.station_id, ev.signal)
        t0, t1 = ev.visible_at - timedelta(hours=6), ev.visible_at
        d = rd[(rd.ts_utc >= t0) & (rd.ts_utc <= t1)]
        if len(d):
            sp = go.Figure(go.Scatter(x=d.ts_utc, y=d.value, mode="lines", line=dict(width=2, color=vc), hoverinfo="skip"))
            sp.add_vrect(x0=max(ev.ts_start_utc, t0), x1=min(max(ev.ts_end_utc, ev.ts_start_utc + timedelta(minutes=10)), t1),
                         fillcolor=vc, opacity=0.15, line_width=0)
            sp.update_layout(height=90, margin=dict(l=0, r=0, t=6, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                             xaxis=dict(visible=False, range=[t0, t1]), yaxis=dict(visible=False, rangemode="tozero"), showlegend=False)
            st.plotly_chart(sp, use_container_width=True, config={"displayModeBar": False, "staticPlot": True})
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

        cached = ss.live_results.get(ev.candidate_id)
        if (ss.live_mode or dynamic) and fresh and cached is None:
            # --- real-time path: run the two models now, painting each stage as its result arrives
            paint("gate_running")
            try:
                gate, gate_ms = run_gate_live(ev.state_json)
            except Exception as e:
                gate, gate_ms = None, None
                st.caption(f"Live ai_decide unavailable ({str(e)[:90]}); showing the stored result.")
            if dynamic:                                     # a user event: the route is decided now, from the live gate
                ev.route = decide_route(ev, gate); closer = ev.route == "INVESTIGATE"
            llm, llm_ms = None, None
            if closer:
                paint("llm_running", gate, gate_ms)
                try:
                    llm, llm_ms = run_llm_live(ev.agent_state or ev.state_json)
                except Exception as e:
                    st.caption(f"Live ai_query unavailable ({str(e)[:90]}); showing the stored reasoning.")
            if dynamic:                                     # verdict, severity and actions from the same rules as the pipeline
                finalize(ev, gate, llm)
                vc = VERDICT_COLOR.get(ev.verdict, MUTED); sev = SEVERITY_WORD.get(ev.severity, "")
                why = humanize(ev.rationale); facts = [humanize(e.get("fact", "")) for e in _safe_json(ev.evidence) if e.get("fact")][:3]
                acts = sorted(ev.actions, key=lambda x: ACTION_ORDER.index(x) if x in ACTION_ORDER else 9) or ["LOG"]
                used_rain = '"RAIN"' in (ev.evidence or "")
                paint_head()
                if "PAGE" in ev.actions and ss.pending_call is None:
                    on_page(ev); pending = ss.pending_call
                    ss.paged.add(ev.candidate_id)
            ss.live_results[ev.candidate_id] = {"gate": gate, "gate_ms": gate_ms, "llm": llm, "llm_ms": llm_ms}
            paint("done", gate, gate_ms, llm, llm_ms)
            if pending is not None:                      # the countdown starts only after the live decision is on screen
                pending["deadline"] = time.time() + ss.countdown_s
            ss.hero_shown_at = 0                           # nothing left to reveal; reruns may proceed
        elif cached is not None:
            paint("done", cached["gate"], cached["gate_ms"], cached["llm"], cached["llm_ms"])
        else:
            paint("done", css_play=anim)                    # replay mode: stored values with the CSS reveal


    # call controls (Streamlit buttons cannot live inside the HTML)
    if pending is not None:
        remaining = int(round(pending["deadline"] - time.time()))
        b1, b2, b3 = st.columns([4, 1, 1])
        if remaining > 0:
            mode = "A real call will be placed" if ss.real_calls else "Simulated: no real call will be placed"
            b1.caption(f"The system has decided to call. {mode} in {remaining} s unless someone cancels.")
            if b2.button("Cancel", use_container_width=True, key="cancel_call"):
                ss.calls[ev.candidate_id] = {"status": "CANCELLED", "result": "cancelled by operator"}
                ss.pending_call = None
                st.rerun()
            if b3.button("Call now", use_container_width=True, type="primary", key="call_now"):
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
            if ss.calls[ev.candidate_id]["status"] != "CALLING" and b3.button("Dismiss", use_container_width=True, key="dismiss_call"):
                ss.pending_call = None
                st.rerun()

# ----------------------------------------------------------------------------- state
events, stations, cases = derive(load_events()), load_stations(), load_cases()
golden, plan = load_results()
T_MIN = events["visible_at"].min().to_pydatetime() - timedelta(hours=3)
T_MAX = events["visible_at"].max().to_pydatetime() + timedelta(hours=3)

ss = st.session_state
ss.setdefault("t", T_MIN); ss.setdefault("playing", False); ss.setdefault("speed_min", 15)
ss.setdefault("paged", set()); ss.setdefault("focus_station", None); ss.setdefault("last_pick", None)
ss.setdefault("pending_call", None); ss.setdefault("calls", {}); ss.setdefault("countdown_s", 10)
voice_ready = pager is not None and all(os.environ.get(k) for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "CONTACT_1"))
ss.setdefault("real_calls", voice_ready)
ss.setdefault("live_mode", True); ss.setdefault("live_results", {})
ss.setdefault("nav_at", 0.0)
ss.setdefault("epoch_t", None)      # "started watching" moment: set on Restart / case pick; tiles and history count from here
SETTLE_S = 1.5   # the playhead must rest this long before live calls or a countdown start (lets you skip past events quickly)

def nav():
    """Any navigation: restart the settle timer and drop a countdown that has not dialled yet (the operator moved on)."""
    ss.nav_at = time.time()
    pc = ss.pending_call
    if pc and ss.calls.get(pc["candidate_id"], {}).get("status") not in ("CALLING", "DONE"):
        ss.calls[pc["candidate_id"]] = {"status": "CANCELLED", "result": "navigated away before the call was placed"}
        ss.pending_call = None

# ----------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("RiverSentinel")
    st.caption("Sentry mode, replayed over September 2026. The system watches the gauges, decides on its own, and shows its reasoning as it goes. "
               "The screen shows only what it knew at the time shown.")

    ss.mode = st.radio("Mode", ["Replay September", "Try your own event"], horizontal=True, label_visibility="collapsed",
                       index=0 if ss.get("mode", "Replay September") == "Replay September" else 1)
    st.divider()
    opts = ["Whole month"] + [f"{r.case_id} — {(r.notes or r.station_id).split(':')[0]}" for r in cases.itertuples()]
    pick = st.selectbox("Go to a known event", opts)
    if pick != "Whole month" and ss.last_pick != pick:
        row = cases.iloc[opts.index(pick) - 1]
        ss.t = row.win_start_utc.to_pydatetime() - timedelta(minutes=30)
        ss.epoch_t = ss.t; ss.last_hero = None; ss.live_results = {}
        ss.focus_station = row.station_id; ss.last_pick = pick; ss.playing = False; nav()
    if pick == "Whole month":
        ss.last_pick = pick

    c1, c2, c3 = st.columns(3)
    if c1.button("Next event", use_container_width=True, type="primary"):
        nxt = events[events["visible_at"] > ss.t]["visible_at"]
        ss.t = (nxt.min().to_pydatetime() + timedelta(minutes=1)) if len(nxt) else T_MAX
        ss.playing = False; nav()
    if c2.button("Pause" if ss.playing else "Play", use_container_width=True):
        ss.playing = not ss.playing
    if c3.button("Restart", use_container_width=True):
        ss.t = T_MIN; ss.epoch_t = None; ss.last_hero = None; ss.live_results = {}
        ss.playing = False; ss.paged = set(); ss.pending_call = None; ss.calls = {}; nav()
    ss.speed_min = st.select_slider("Speed (river minutes per second)", options=[5, 15, 30, 60, 180, 360], value=ss.speed_min)
    t_val = st.slider("Time", min_value=T_MIN, max_value=T_MAX, value=ss.t, step=timedelta(minutes=5), format="MMM D, HH:mm")
    if t_val != ss.t:
        ss.t = t_val; ss.playing = False; nav()

    st.divider()
    provinces = st.multiselect("Rivers in", sorted(stations.province.unique()), default=sorted(stations.province.unique()),
                               format_func=lambda p: {"AB": "Alberta — Bow & Elbow", "NS": "Nova Scotia — Cape Breton"}.get(p, p))

    st.divider()
    ss.live_mode = st.toggle("Re-run the AI live on each new decision", value=ss.live_mode,
                             help="Calls ai_decide and ai_query on the Databricks warehouse with the exact stored input and shows the real timings. Off = replay the stored results.")
    st.caption(f"Model for the closer look: {LLM_MODEL}")

    st.divider()
    st.markdown("**When it decides to call**")
    ss.countdown_s = st.slider("Seconds before the call is placed", 5, 30, ss.countdown_s, help="A human can cancel during this window")
    ss.real_calls = st.toggle("Place real phone calls", value=ss.real_calls, disabled=not voice_ready,
                              help=None if voice_ready else "Needs page.py plus ElevenLabs, Twilio and CONTACT_1 settings")
    if not voice_ready:
        missing = [k for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "CONTACT_1") if not os.environ.get(k)]
        st.caption(("page.py not found next to the app. " if pager is None else "") + (f"Missing in .env: {', '.join(missing)}" if missing else ""))
    escalate = st.toggle("If no answer, call the second contact", value=bool(os.environ.get("CONTACT_2")), disabled=not os.environ.get("CONTACT_2"))
    if voice_ready:
        st.caption("Will call " + pager.mask(os.environ["CONTACT_1"]) + (f", then {pager.mask(os.environ['CONTACT_2'])}" if escalate and os.environ.get("CONTACT_2") else ""))

if ss.mode == "Try your own event":
    ss.setdefault("custom_ev", None); ss.setdefault("custom_fresh", False)
    new_ev = custom_event_form()
    if new_ev is not None:
        nav()                                   # drops any undialled countdown from a previous event
        ss.custom_ev, ss.custom_fresh = new_ev, True
    ev_c = ss.custom_ev
    if ev_c is not None:
        pc = ss.pending_call
        hero(ev_c, ss.custom_fresh, pc if (pc and pc["candidate_id"] == ev_c.candidate_id) else None, ss.calls.get(ev_c.candidate_id))
        ss.custom_fresh = False
        st.caption(f"Route cut-off {AUTO_RESOLVE_THRESHOLD:.2f} (round R2) · neighbours count as moving above {int(NEIGHBOUR_MOVE_PCT*100)} % · "
                   f"model {LLM_MODEL}. The event is not written to the tables.")
    if ss.pending_call and (ss.pending_call["deadline"] > time.time() or ss.calls.get(ss.pending_call["candidate_id"], {}).get("status") == "CALLING"):
        time.sleep(1.0); st.rerun()
    st.stop()

T = ss.t
lanes = stations[stations.province.isin(provinces)]
ev_lanes = events[events.station_id.isin(lanes.station_id)]
known = ev_lanes[ev_lanes["visible_at"] <= T]
seen = known if ss.epoch_t is None else known[known["visible_at"] > ss.epoch_t]      # since you started watching
upcoming = ev_lanes[ev_lanes["visible_at"] > T]
just_now = known[known["visible_at"] > T - timedelta(minutes=max(ss.speed_min, 5) + 1)]

settled = (time.time() - ss.nav_at) >= SETTLE_S

for ev in (just_now.sort_values("visible_at", ascending=False).itertuples() if settled else []):
    if "PAGE" in ev.actions and ev.candidate_id not in ss.paged:
        ss.paged.add(ev.candidate_id); ss.playing = False
        if ss.pending_call is None:                     # one call per event even if several detectors fired together
            on_page(ev)

# ----------------------------------------------------------------------------- header
st.markdown(f"## {fmt(T)}" + (f'<span style="color:#898781;font-size:1rem;font-weight:400;margin-left:14px">watching since {fmt(ss.epoch_t)}</span>' if ss.epoch_t is not None else ""), unsafe_allow_html=True)
m = st.columns(4)
m[0].metric("Odd readings spotted", len(seen))
m[1].metric("Settled instantly", f"{(seen.route == 'AUTO_RESOLVE').mean()*100:.0f}%" if len(seen) else "–",
            help="Decided by a quick check, without AI reasoning")
m[2].metric("Sent to a technician", int(has(seen, "TICKET").sum()), help="Bad-sensor tickets; readings flagged so they do not poison downstream models")
m[3].metric("Calls to the on-call person", int(seen[has(seen, "PAGE")].event_key.nunique()), help="One call per gauge per event")

# ----------------------------------------------------------------------------- live decision (the highlight)
if len(seen):
    latest = seen.sort_values("visible_at").iloc[-1]
    if ss.pending_call and (seen.candidate_id == ss.pending_call["candidate_id"]).any():
        latest = seen[seen.candidate_id == ss.pending_call["candidate_id"]].iloc[0]   # the card must be the one the call belongs to
    fresh = settled and ss.get("last_hero") != latest.candidate_id
    if settled:
        ss.last_hero = latest.candidate_id
    if fresh:
        ss.hero_shown_at = time.time()          # the reveal runs ~5 s; reruns are held off until it has played
    pc = ss.pending_call
    hero(latest, fresh, pc if (pc and pc["candidate_id"] == latest.candidate_id) else None, ss.calls.get(latest.candidate_id))
    if not settled:
        st.caption("Moving… the live check starts when the playhead rests.")
else:
    # nothing seen since the jump: quiet sentry state (earlier history stays visible on the timeline as context)
    st.markdown(f'<div class="idle"><div class="dot"></div><div><b>Watching {len(lanes)} gauges</b> across two provinces. Nothing unusual so far. '
                f'Press <b>Next event</b> to move to the first odd reading.</div></div>', unsafe_allow_html=True)
    station_map(lanes, None, height=320)

# ----------------------------------------------------------------------------- timeline
lane_order = list(lanes.station_id)
lane_label = {r.station_id: nice_name(r.name) for r in lanes.itertuples()}
fig = go.Figure()
for k in cases[cases.station_id.isin(lane_order)].itertuples():
    y = lane_order.index(k.station_id)
    fig.add_shape(type="rect", x0=k.win_start_utc, x1=k.win_end_utc, y0=y - 0.45, y1=y + 0.45, fillcolor=GRID, opacity=0.6, line_width=0, layer="below")
    fig.add_annotation(x=k.win_start_utc, y=y + 0.42, text=k.case_id, showarrow=False, font=dict(size=10, color=INK2), xanchor="left", yanchor="bottom")
if len(upcoming):
    fig.add_trace(go.Scatter(x=upcoming.visible_at, y=[lane_order.index(s) for s in upcoming.station_id], mode="markers",
                             marker=dict(size=6, color=VERDICT_COLOR["PENDING"]), name="Not yet happened",
                             hovertemplate="not yet happened<extra></extra>"))
if ss.epoch_t is not None:
    before = known[known["visible_at"] <= ss.epoch_t]
    if len(before):
        fig.add_trace(go.Scatter(x=before.visible_at, y=[lane_order.index(s_) for s_ in before.station_id], mode="markers",
                                 marker=dict(size=7, color="#d9d8d2"), name="Before you started watching",
                                 hovertemplate="earlier in the month<extra></extra>"))
for verdict, color in VERDICT_COLOR.items():
    if verdict == "PENDING": continue
    d = seen[seen.verdict == verdict]
    if not len(d): continue
    fig.add_trace(go.Scatter(
        x=d.visible_at, y=[lane_order.index(s) for s in d.station_id], mode="markers", name=VERDICT_WORD[verdict],
        marker=dict(size=[16 if a == "PAGE" else 11 for a in d.primary_action], color=color, line=dict(width=2, color=SURFACE),
                    symbol=[ACTION_SYMBOL.get(a, "circle") for a in d.primary_action]),
        customdata=list(zip([nice_name(n) for n in d.station_name], [RULE_WORD.get(r, r) for r in d.rule], [ACTION_WORD.get(a, a) for a in d.primary_action])),
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>" + VERDICT_WORD[verdict] + " → %{customdata[2]}<extra></extra>"))
fig.add_vline(x=T, line_width=2, line_color=INK)
fig.update_layout(height=max(240, 28 * len(lane_order) + 60), margin=dict(l=10, r=10, t=10, b=10),
                  paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(color=INK2, family="system-ui, -apple-system, Segoe UI, sans-serif"),
                  legend=dict(orientation="h", y=-0.1, font=dict(color=INK2)),
                  xaxis=dict(range=[T_MIN, T_MAX], gridcolor=GRID, linecolor=AXIS, tickfont=dict(color=MUTED)),
                  yaxis=dict(tickmode="array", tickvals=list(range(len(lane_order))), ticktext=[lane_label[s] for s in lane_order],
                             autorange="reversed", gridcolor=GRID, tickfont=dict(size=11, color=INK2)))
st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
st.caption("One row per river gauge. Each dot is a reading the system thought was odd. Colour: what it decided. "
           "Shape: what it did — star = called someone · cross = flagged bad data · square = sent a technician · circle = just noted it. "
           "Grey bands are the events we know really happened. Grey dots have not happened yet.")

# ----------------------------------------------------------------------------- decisions + gauge
left, right = st.columns([1.15, 1])
with left:
    st.subheader("Earlier decisions")
    earlier = seen.sort_values("visible_at", ascending=False).iloc[1:13]
    if not len(earlier):
        st.caption("No earlier decisions since you started watching." if ss.epoch_t is not None else "Only one decision so far.")
    for ev in earlier.itertuples():
        vc = VERDICT_COLOR.get(ev.verdict, MUTED)
        acts = " · ".join(ACTION_TITLE.get(a, a) for a in ev.actions) or "Logged"
        st.markdown(f'<div class="card" style="border-left:5px solid {vc};padding:10px 14px">'
                    f'<div style="color:#52514e;font-size:.85rem">{fmt(ev.visible_at)}</div>'
                    f'<div><b>{nice_name(ev.station_name)}</b> — {VERDICT_WORD.get(ev.verdict, ev.verdict)}'
                    f'<span style="color:#52514e"> · {RULE_WORD.get(ev.rule, ev.rule)}</span></div>'
                    f'<div style="color:#0b0b0b;margin-top:2px">{acts}</div></div>', unsafe_allow_html=True)

with right:
    focus = ss.focus_station or (known.iloc[-1].station_id if len(known) else lane_order[0])
    st.subheader(lane_label.get(focus, focus))
    sig = st.radio("Measure", ["DISCHARGE", "LEVEL"], horizontal=True, label_visibility="collapsed",
                   format_func=lambda s: "Flow (m³/s)" if s == "DISCHARGE" else "Water level (m)")
    rd = load_readings(focus, sig)
    win_start = T - timedelta(hours=36)
    d = rd[(rd.ts_utc >= win_start) & (rd.ts_utc <= T)]
    f2 = go.Figure()
    f2.add_trace(go.Scatter(x=d.ts_utc, y=d.value, mode="lines", line=dict(width=2, color=VERDICT_COLOR["NATURAL_EVENT"]), name="Reading",
                            hovertemplate="%{x|%b %d %H:%M} · %{y:.3g}<extra></extra>"))
    qd = d[d.is_quarantined == True]
    if len(qd):
        f2.add_trace(go.Scatter(x=qd.ts_utc, y=qd.value, mode="markers", name="Flagged as bad data",
                                marker=dict(symbol="x", size=10, color=VERDICT_COLOR["SENSOR_FAULT"], line=dict(width=1, color=SURFACE))))
    for ev in known[(known.station_id == focus) & (known.ts_end_utc >= win_start)].itertuples():
        f2.add_vrect(x0=ev.ts_start_utc, x1=max(ev.ts_end_utc, ev.ts_start_utc + timedelta(minutes=10)),
                     fillcolor=VERDICT_COLOR.get(ev.verdict, MUTED), opacity=0.15, line_width=0)
    f2.add_vline(x=T, line_width=2, line_color=INK)
    f2.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, showlegend=len(qd) > 0,
                     font=dict(color=INK2, family="system-ui, -apple-system, Segoe UI, sans-serif"),
                     xaxis=dict(gridcolor=GRID, linecolor=AXIS, tickfont=dict(color=MUTED)),
                     yaxis=dict(title="m³/s" if sig == "DISCHARGE" else "m", gridcolor=GRID, tickfont=dict(color=MUTED), rangemode="tozero"),
                     hovermode="x unified")
    st.plotly_chart(f2, use_container_width=True, config={"displayModeBar": False})
    st.caption("Last 36 hours up to the time shown. Shaded = the odd readings, coloured by decision. Crosses = readings flagged as bad data.")

# ----------------------------------------------------------------------------- footer: two numbers and one sentence
done = golden[golden.case_id.isin(cases[cases.win_end_utc <= T].case_id)] if len(golden) else golden
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
