"""
RiverSentinel decision rules and helpers, kept free of Streamlit so they can be tested (tests/) without a warehouse.
timeline.py imports everything here; the rules mirror the SQL pipeline (gold_build.sql), so keep the two in step.
Import after .env is loaded: AUTO_RESOLVE_THRESHOLD reads the environment at import time.
"""
from __future__ import annotations
import json, os, re, uuid
from datetime import timedelta, timezone
from types import SimpleNamespace

import pandas as pd

MDT = timezone(timedelta(hours=-6))


# ----------------------------------------------------------------------------- tables
def derive(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in ("ts_start_utc", "ts_end_utc", "emitted_at_utc"):
        df[c] = pd.to_datetime(df[c], utc=True)
    df["visible_at"] = df["emitted_at_utc"].fillna(df["ts_end_utc"])      # the moment the system could know
    df["actions"] = [sorted(set(a)) if a is not None else [] for a in df["actions"]]
    df["primary_action"] = [next((k for k in ("PAGE", "QUARANTINE", "WATCH", "TICKET", "LOG") if k in a), "LOG") for a in df["actions"]]
    df["event_key"] = [c if isinstance(c, str) and c else f"{s}_{t:%Y%m%d}_b{t.hour // 6}"
                       for c, s, t in zip(df["case_id"], df["station_id"], df["ts_start_utc"])]
    df["verdict"] = df["verdict"].fillna("INCONCLUSIVE")
    df["severity"] = df["severity"].fillna("NONE")
    return df.sort_values("visible_at").reset_index(drop=True)

def has(df: pd.DataFrame, action: str) -> pd.Series:
    return pd.Series([action in a for a in df["actions"]], index=df.index, dtype=bool)

def fmt(ts) -> str:
    t = pd.Timestamp(ts).tz_convert(MDT)
    return f"{t.strftime('%b %d')}, {t.strftime('%I:%M %p').lstrip('0')}"

# ----------------------------------------------------------------------------- parsing stored JSON
def _safe_json(txt):
    try:
        return json.loads(txt or "[]")
    except Exception:
        return []

def _ids(txt) -> list:
    if txt is None: return []
    try:
        v = json.loads(txt) if isinstance(txt, str) and txt.strip().startswith("[") else None
    except Exception:
        v = None
    if v is None:
        return [x for x in re.split(r"[,\s]+", str(txt).strip("[]\"' ")) if x]
    return [x["id"] if isinstance(x, dict) else str(x) for x in v]

def _nearby(txt) -> list:
    try:
        v = json.loads(txt) if txt else []
        return [(x["id"], float(x.get("km", 0))) for x in v if isinstance(x, dict)]
    except Exception:
        return []

def neighbour_pcts(state_json) -> dict:
    """{station_id: fractional change over the 24 h before emission} from the stored gate state (connected + other rivers)."""
    try:
        n = json.loads(state_json or "{}").get("neighbours", {})
        out = {}
        for k in ("connected_same_river", "other_rivers"):
            m = n.get(k, {}).get("deltas_pct") or {}
            out.update({str(a): float(b) for a, b in m.items() if b is not None})
        return out
    except Exception:
        return {}

# ----------------------------------------------------------------------------- try your own event: same rules as the SQL pipeline
AUTO_RESOLVE_THRESHOLD = float(os.environ.get("AUTO_RESOLVE_THRESHOLD", "0.85"))   # R2 value from gold.plan_iterations
NEIGHBOUR_MOVE_PCT = 0.20
FACTS = ["Discharge is derived from level, so level/discharge agreement proves nothing.",
         "Many stations report every 5 min but only change value in 16-72% of steps; short flatlines are normal.",
         "Regulated reaches show flat runs up to 22 h and step changes when releases change.",
         "Quality symbols in the real-time feed are always null; there are no fault flags.",
         "plausible_max is a historical plausibility bound, not a physical limit: a real flood can exceed it.",
         "Only values below physical_min (e.g. zero or negative flow in a perennial river) are physically impossible."]

def _p99(stn, month):
    try:
        m = json.loads(stn.get("p99_cms") or "{}")
        if isinstance(m, dict):
            v = m.get(str(month)) or m.get(month) or next(iter(m.values()), None)
            return float(v) if v is not None else None
        return float(m)
    except Exception:
        return None

def infer_rule(value, prev6, rise_frac, gap_min, flatline_len, spike, stn, signal):
    lo = stn.get("discharge_min_possible_cms") if signal == "DISCHARGE" else stn.get("level_min_possible_m")
    hi = stn.get("discharge_max_plausible_cms") if signal == "DISCHARGE" else stn.get("level_max_plausible_m")
    if gap_min and gap_min > 0: return "GAP", None
    if lo is not None and value <= float(lo): return "PHYSICAL_BREACH", "LOW"
    if hi is not None and value >= float(hi): return "PHYSICAL_BREACH", "HIGH"
    if spike: return "SPIKE", None
    if flatline_len and stn.get("normal_flatline_steps") and flatline_len > 3 * int(stn["normal_flatline_steps"]): return "FLATLINE", None
    if rise_frac is not None and rise_frac >= 0.5: return "RAPID_RISE", None
    return "STEP", None

def build_custom_event(stn: dict, signal: str, value: float, prev6: float, rain24: float, rain48: float, gauge_trust: float,
                       gap_min: float, flatline_len: int, spike: bool, nb_pct: dict, rule_override: str | None):
    now = pd.Timestamp.now("UTC")
    month = now.month
    delta_6h = value - prev6
    rise_frac = (delta_6h / prev6) if prev6 and prev6 > 0 else None
    rule, breach_side = infer_rule(value, prev6, rise_frac, gap_min, flatline_len, spike, stn, signal)
    if rule_override and rule_override != "auto": rule = rule_override
    conn_ids = [i for i in _ids(stn.get("upstream_ids")) + _ids(stn.get("downstream_ids"))]
    nd_conn = {k: v for k, v in nb_pct.items() if k in conn_ids}
    nd_other = {k: v for k, v in nb_pct.items() if k not in conn_ids}
    n_conn_rising = sum(1 for v in nd_conn.values() if v > NEIGHBOUR_MOVE_PCT)
    n_conn_moving = sum(1 for v in nd_conn.values() if abs(v) > NEIGHBOUR_MOVE_PCT)
    n_other_rising = sum(1 for v in nd_other.values() if v > NEIGHBOUR_MOVE_PCT)
    lo = stn.get("discharge_min_possible_cms") if signal == "DISCHARGE" else stn.get("level_min_possible_m")
    hi = stn.get("discharge_max_plausible_cms") if signal == "DISCHARGE" else stn.get("level_max_plausible_m")
    p99 = _p99(stn, month)
    state = {
        "station": {"id": stn["station_id"], "name": stn["name"], "regulated": bool(stn.get("regulated")) if stn.get("regulated") is not None else None,
                    "controls": stn.get("controls"), "update_cadence_min": stn.get("update_cadence_min"), "normal_flatline_steps": stn.get("normal_flatline_steps"),
                    "typical_change_fraction": stn.get("typical_change_fraction"), "notes": stn.get("notes"),
                    "thresholds_cms": {"advisory": None, "watch": None, "warning": None}},
        "detector": {"rule": rule, "signal": signal, "window_start_utc": str(now - pd.Timedelta(minutes=10)), "window_end_utc": str(now), "emitted_at_utc": str(now)},
        "reading": {"value": value, "delta_1h": None, "delta_6h": delta_6h, "rise_frac_6h": rise_frac, "z_score": None,
                    "flatline_len": int(flatline_len or 0), "gap_min": float(gap_min or 0), "breach_side": breach_side,
                    "physical_min": lo, "plausible_max": hi, "p99_this_month": p99},
        "neighbours": {"connected_same_river": {"deltas_pct": nd_conn, "n_moving": n_conn_moving, "n_rising": n_conn_rising,
                                                "note": "upstream/downstream of this station; the strongest corroboration"},
                       "other_rivers": {"deltas_pct": nd_other, "n_rising": n_other_rising,
                                        "note": "nearby stations on other rivers; several rising together = basin-wide weather"},
                       "deltas_cms": {}, "moving_threshold_pct": NEIGHBOUR_MOVE_PCT, "window": "24 h before emitted_at_utc"},
        "weather": {"rain_24h_mm": rain24, "rain_48h_mm": rain48, "gauge_trust": gauge_trust, "window": "up to emitted_at_utc"},
        "facts": FACTS,
    }
    sj = json.dumps(state, default=str)
    return SimpleNamespace(
        dynamic=True, candidate_id="custom-" + uuid.uuid4().hex[:8], station_id=stn["station_id"], station_name=stn["name"], province=stn.get("province"),
        signal=signal, rule=rule, breach_side=breach_side, ts_start_utc=now - pd.Timedelta(minutes=10), ts_end_utc=now, emitted_at_utc=now, visible_at=now,
        value=value, delta_6h=delta_6h, rise_frac_6h=rise_frac, rain_24h_mm=rain24, p99_month=p99, max_plausible=hi, min_possible=lo,
        regulated=bool(stn.get("regulated")) if stn.get("regulated") is not None else False,
        n_connected_rising=n_conn_rising, n_other_rising=n_other_rising,
        p_fault=None, p_operational=None, p_real_event=None, p_impossible=None, route="PENDING", verdict="PENDING", severity="NONE",
        confidence=None, decided_by=None, rationale="", evidence="[]", actions=[], state_json=sj, agent_state=sj, case_id=None,
        event_key="custom", primary_action="LOG")

def neighbour_ids(stn: dict, known_ids) -> list:
    """Same-river (upstream, downstream) then nearby other-river gauges, de-duplicated, limited to stations we know."""
    ids = _ids(stn.get("upstream_ids")) + _ids(stn.get("downstream_ids")) + [i for i, _ in _nearby(stn.get("nearby_ids"))]
    known = set(known_ids)
    return [i for i in dict.fromkeys(ids) if i in known]

def scenario_event(sc: dict, stn: dict, known_ids):
    """A preset from scenarios.py as a custom event, using each field's default. neighbours_pct (in %) maps onto
    neighbour_ids() in order; extra percentages or gauges are ignored."""
    v = lambda k: sc[k][0]
    nb_pct = {i: p[0] / 100.0 for i, p in zip(neighbour_ids(stn, known_ids), sc["neighbours_pct"])}
    ev = build_custom_event(stn, "DISCHARGE" if sc["measure"] == "flow" else "LEVEL", float(v("reading_now")), float(v("reading_6h_ago")),
                            float(v("rain_24h_mm")), float(v("rain_48h_mm")), float(v("rain_gauge_trust")), float(v("minutes_no_data")),
                            int(v("identical_readings")), bool(sc["single_reading_jump"]), nb_pct, sc["spotted_as"])
    ev.scenario = sc["name"]
    return ev

def decide_route(ev, gate):
    """Mirror of the SQL routing with this round's threshold."""
    if gate is None: return "INVESTIGATE"
    if ev.rule == "RAPID_RISE": return "INVESTIGATE"
    if ev.rule == "PHYSICAL_BREACH" and (ev.breach_side or "HIGH") != "LOW": return "INVESTIGATE"
    top = max([x for x in (gate["p_fault"], gate["p_operational"], gate["p_real_event"]) if x is not None] or [0])
    if top < AUTO_RESOLVE_THRESHOLD: return "INVESTIGATE"
    if ev.rule in ("PHYSICAL_BREACH", "SPIKE", "FLATLINE", "GAP") and gate["label"] == "SENSOR_FAULT": return "AUTO_RESOLVE"
    if ev.rule == "STEP" and ev.regulated and gate["label"] == "OPERATIONAL_CHANGE": return "AUTO_RESOLVE"
    return "INVESTIGATE"

def finalize(ev, gate, llm):
    """Mirror of the SQL verdict overrides, severity rules and routing table."""
    if gate:
        ev.p_fault, ev.p_operational, ev.p_real_event, ev.p_impossible = gate["p_fault"], gate["p_operational"], gate["p_real_event"], gate["p_impossible"]
    raw = (llm or {}).get("verdict") if ev.route == "INVESTIGATE" else (gate or {}).get("label")
    if ev.rule == "PHYSICAL_BREACH" and ev.breach_side == "LOW": verdict = "SENSOR_FAULT"
    elif gate and (gate.get("p_impossible") or 0) >= 0.9 and ev.min_possible is not None and ev.value <= float(ev.min_possible): verdict = "SENSOR_FAULT"
    elif raw is None: verdict = "INCONCLUSIVE"
    else: verdict = raw
    ev.verdict = verdict
    rise = ev.rise_frac_6h or 0; rain = ev.rain_24h_mm or 0
    corroborated = ev.n_connected_rising >= 1 or ev.n_other_rising >= 2
    if verdict != "NATURAL_EVENT": sev = "NONE"
    elif (rise >= 2.0 and rain >= 20) or (rise >= 1.0 and ev.max_plausible is not None and ev.value >= float(ev.max_plausible) and corroborated): sev = "DANGER_TO_LIFE"
    elif rise >= 1.0 or rain >= 20 or (ev.p99_month is not None and ev.value > ev.p99_month): sev = "SIGNIFICANT"
    else: sev = "MINOR"
    ev.severity = sev
    if verdict == "SENSOR_FAULT" and ev.rule == "GAP": acts = ["TICKET"]
    elif verdict == "SENSOR_FAULT": acts = ["QUARANTINE", "TICKET"]
    elif verdict == "OPERATIONAL_CHANGE": acts = ["LOG"]
    elif verdict == "NATURAL_EVENT": acts = ["WATCH"] if sev == "MINOR" else ["PAGE"]
    elif verdict == "INCONCLUSIVE" and rise >= 1.0 and (rain >= 10 or corroborated or (ev.p99_month is not None and ev.value > ev.p99_month)): acts = ["PAGE"]
    elif verdict == "INCONCLUSIVE" and ev.breach_side == "HIGH": acts = ["WATCH"]
    else: acts = ["LOG"]
    ev.actions = acts
    ev.primary_action = next((k for k in ("PAGE", "QUARANTINE", "WATCH", "TICKET", "LOG") if k in acts), "LOG")
    if ev.route == "INVESTIGATE" and llm:
        ev.decided_by, ev.confidence = "AGENT_SQL", llm.get("confidence")
        ev.rationale = llm.get("rationale") or ""
        ev.evidence = json.dumps(llm.get("evidence") or [])
    else:
        ev.decided_by, ev.confidence = "GATE", (gate or {}).get("confidence")
        top = max([x for x in (ev.p_fault, ev.p_operational, ev.p_real_event) if x is not None] or [0])
        ev.rationale = f"Gate auto-resolved as {ev.verdict} (p={top:.2f} ≥ {AUTO_RESOLVE_THRESHOLD}); detector rule {ev.rule} is consistent with the label."
        ev.evidence = json.dumps([{"source": "PHYSICS", "tool": "check_physical_limits", "fact": f"value {ev.value} vs physical_min {ev.min_possible} / plausible_max {ev.max_plausible}, breach_side={ev.breach_side or 'none'}"},
                                  {"source": "NEIGHBOURS", "tool": "get_neighbours", "fact": f"{ev.n_connected_rising} same-river neighbour(s) rising, {ev.n_other_rising} other-river station(s) rising (>{int(NEIGHBOUR_MOVE_PCT*100)}% in 24 h)"}])

# ----------------------------------------------------------------------------- plain-language text
def pct(x):
    return int(round((x or 0) * 100))

_SMALL = {"at", "of", "the", "near", "below", "above", "and", "on", "in", "to"}
def nice_name(name: str) -> str:
    words = str(name or "").lower().split()
    out = [w if (i and w in _SMALL) else (w.upper() if re.fullmatch(r"\d+[a-z]*", w) else w.capitalize()) for i, w in enumerate(words)]
    return " ".join(out)

_HUMAN = [(r"\brise_frac_6h\b", "6-hour rise"), (r"\bplausible_max\b", "plausible maximum"), (r"\bphysical_min\b", "physical minimum"),
          (r"\bother_rivers?\b", "other rivers"), (r"\bconnected_same_river\b", "same-river"), (r"\bn_rising\b", "stations rising"),
          (r"\bdelta_6h\b", "6-hour change"), (r"\bdelta_1h\b", "1-hour change"), (r"\brain_24h_mm\b", "24-hour rain"),
          (r"\bp99_this_month\b", "this month's 99th percentile"), (r"\bgauge_trust\b", "rain-gauge trust"), (r"\bz_score\b", "z-score"),
          (r"\bflatline_len\b", "flat-line length"), (r"\bgap_min\b", "gap (minutes)"), (r"\bbreach_side\b", "breach side"), (r"_", " ")]
def humanize(txt: str) -> str:
    t = str(txt or "")
    t = re.sub(r"(\d+\.\d{3})\d+", r"\1", t)                       # 1.5675675675 → 1.567
    for pat, rep in _HUMAN:
        t = re.sub(pat, rep, t)
    return t
