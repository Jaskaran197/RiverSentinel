import json

import pandas as pd
import pytest

import logic
from logic import (_ids, _nearby, _p99, build_custom_event, decide_route, derive, finalize, fmt, humanize, infer_rule,
                   neighbour_pcts, nice_name, pct)


def gate(label, p, p_impossible=0.0):
    probs = {"SENSOR_FAULT": 0.0, "OPERATIONAL_CHANGE": 0.0, "NATURAL_EVENT": 0.0, label: p}
    return {"p_fault": probs["SENSOR_FAULT"], "p_operational": probs["OPERATIONAL_CHANGE"], "p_real_event": probs["NATURAL_EVENT"],
            "label": label, "p_impossible": p_impossible, "severity_score": None}


def event(station, value=100.0, prev6=100.0, rain24=0.0, gap_min=0.0, flatline_len=0, spike=False, nb_pct=None, rule="auto"):
    return build_custom_event(station, "DISCHARGE", value, prev6, rain24, rain24, 1.0, gap_min, flatline_len, spike, nb_pct or {}, rule)


# ----------------------------------------------------------------------------- infer_rule
@pytest.mark.parametrize("kw, expected", [
    ({"value": -1.0, "gap_min": 30}, ("GAP", None)),                 # a gap wins over everything
    ({"value": 0.0}, ("PHYSICAL_BREACH", "LOW")),
    ({"value": 1600.0}, ("PHYSICAL_BREACH", "HIGH")),
    ({"value": 100.0, "spike": True}, ("SPIKE", None)),
    ({"value": 100.0, "flatline_len": 19}, ("FLATLINE", None)),     # > 3 x normal_flatline_steps (6)
    ({"value": 100.0, "flatline_len": 18}, ("STEP", None)),
    ({"value": 150.0, "rise_frac": 0.5}, ("RAPID_RISE", None)),
    ({"value": 120.0, "rise_frac": 0.2}, ("STEP", None)),
])
def test_infer_rule(station, kw, expected):
    args = {"value": 100.0, "prev6": 100.0, "rise_frac": None, "gap_min": 0, "flatline_len": 0, "spike": False} | kw
    assert infer_rule(args["value"], args["prev6"], args["rise_frac"], args["gap_min"], args["flatline_len"], args["spike"], station, "DISCHARGE") == expected


def test_infer_rule_uses_level_bounds_for_level(station):
    assert infer_rule(0.4, 1.0, None, 0, 0, False, station, "LEVEL") == ("PHYSICAL_BREACH", "LOW")
    assert infer_rule(3.0, 3.0, None, 0, 0, False, station, "LEVEL") == ("STEP", None)


# ----------------------------------------------------------------------------- build_custom_event
def test_custom_event_splits_neighbours(station):
    ev = event(station, value=300.0, prev6=100.0, nb_pct={"05BH005": 0.5, "05BM002": -0.3, "05BJ010": 0.25, "05XX999": 0.4})
    assert ev.rule == "RAPID_RISE" and ev.rise_frac_6h == pytest.approx(2.0)
    assert ev.n_connected_rising == 1 and ev.n_other_rising == 2
    state = json.loads(ev.state_json)
    assert set(state["neighbours"]["connected_same_river"]["deltas_pct"]) == {"05BH005", "05BM002"}
    assert state["neighbours"]["connected_same_river"]["n_moving"] == 2
    assert state["detector"]["rule"] == "RAPID_RISE" and state["reading"]["plausible_max"] == 1500.0
    assert ev.route == ev.verdict == "PENDING"


def test_custom_event_rule_override(station):
    assert event(station, value=300.0, prev6=100.0, rule="SPIKE").rule == "SPIKE"


# ----------------------------------------------------------------------------- decide_route
def test_route_without_gate_investigates(station):
    assert decide_route(event(station), None) == "INVESTIGATE"


def test_route_rapid_rise_and_high_breach_always_investigate(station):
    assert decide_route(event(station, value=300.0, prev6=100.0), gate("NATURAL_EVENT", 0.99)) == "INVESTIGATE"
    assert decide_route(event(station, value=1600.0), gate("SENSOR_FAULT", 0.99)) == "INVESTIGATE"


def test_route_auto_resolves_confident_fault(station):
    ev = event(station, spike=True)
    assert decide_route(ev, gate("SENSOR_FAULT", 0.9)) == "AUTO_RESOLVE"
    assert decide_route(ev, gate("SENSOR_FAULT", 0.8)) == "INVESTIGATE"          # below the 0.85 cut-off
    assert decide_route(ev, gate("NATURAL_EVENT", 0.9)) == "INVESTIGATE"         # label inconsistent with the rule


def test_route_threshold_is_read_at_call_time(station, monkeypatch):
    monkeypatch.setattr(logic, "AUTO_RESOLVE_THRESHOLD", 0.75)
    assert decide_route(event(station, spike=True), gate("SENSOR_FAULT", 0.8)) == "AUTO_RESOLVE"


def test_route_step_auto_resolves_only_when_regulated(station):
    assert decide_route(event(station, value=120.0), gate("OPERATIONAL_CHANGE", 0.9)) == "AUTO_RESOLVE"
    assert decide_route(event(station | {"regulated": False}, value=120.0), gate("OPERATIONAL_CHANGE", 0.9)) == "INVESTIGATE"


# ----------------------------------------------------------------------------- finalize
def decided(ev, g, llm=None):
    ev.route = decide_route(ev, g)
    finalize(ev, g, llm)
    return ev


def test_low_breach_is_always_a_fault(station):
    ev = decided(event(station, value=0.0), gate("NATURAL_EVENT", 0.9), {"verdict": "NATURAL_EVENT", "confidence": 0.9})
    assert ev.verdict == "SENSOR_FAULT" and ev.severity == "NONE"
    assert ev.actions == ["QUARANTINE", "TICKET"] and ev.primary_action == "QUARANTINE"


def test_gap_fault_only_tickets(station):
    ev = decided(event(station, gap_min=45), gate("SENSOR_FAULT", 0.95))
    assert ev.route == "AUTO_RESOLVE" and ev.verdict == "SENSOR_FAULT" and ev.actions == ["TICKET"]
    assert ev.decided_by == "GATE" and len(json.loads(ev.evidence)) == 2


def test_operational_change_is_logged(station):
    ev = decided(event(station, value=120.0), gate("OPERATIONAL_CHANGE", 0.9))
    assert ev.verdict == "OPERATIONAL_CHANGE" and ev.actions == ["LOG"]


@pytest.mark.parametrize("value, prev6, rain, nb, severity, actions", [
    (310.0, 100.0, 25.0, {}, "DANGER_TO_LIFE", ["PAGE"]),                          # rise >= 200% with >= 20 mm rain
    (1600.0, 700.0, 0.0, {"05BH005": 0.5}, "DANGER_TO_LIFE", ["PAGE"]),           # above plausible max, doubled, corroborated
    (1600.0, 700.0, 0.0, {}, "SIGNIFICANT", ["PAGE"]),                             # same, uncorroborated
    (160.0, 100.0, 0.0, {}, "MINOR", ["WATCH"]),
    (160.0, 100.0, 20.0, {}, "SIGNIFICANT", ["PAGE"]),
])
def test_natural_event_severity_and_actions(station, value, prev6, rain, nb, severity, actions):
    llm = {"verdict": "NATURAL_EVENT", "confidence": 0.8, "rationale": "rising with rain", "evidence": [{"source": "SERIES", "tool": "t", "fact": "f"}]}
    ev = decided(event(station, value=value, prev6=prev6, rain24=rain, nb_pct=nb), gate("NATURAL_EVENT", 0.9), llm)
    assert ev.route == "INVESTIGATE" and ev.verdict == "NATURAL_EVENT"
    assert (ev.severity, ev.actions) == (severity, actions)
    assert ev.decided_by == "AGENT_SQL" and ev.confidence == 0.8 and ev.rationale == "rising with rain"


def test_inconclusive_pages_on_fast_rise_with_rain(station):
    ev = decided(event(station, value=210.0, prev6=100.0, rain24=10.0), gate("NATURAL_EVENT", 0.6), {"verdict": "INCONCLUSIVE"})
    assert ev.verdict == "INCONCLUSIVE" and ev.actions == ["PAGE"]


def test_inconclusive_high_breach_is_watched(station):
    ev = decided(event(station, value=1600.0, prev6=1600.0), gate("NATURAL_EVENT", 0.6), {"verdict": "INCONCLUSIVE"})
    assert ev.actions == ["WATCH"]                                               # high but not rising: no call


def test_missing_llm_verdict_is_inconclusive(station):
    ev = decided(event(station, value=120.0), gate("NATURAL_EVENT", 0.5), None)
    assert ev.verdict == "INCONCLUSIVE" and ev.actions == ["LOG"] and ev.decided_by == "GATE"


# ----------------------------------------------------------------------------- derive
def test_derive():
    raw = pd.DataFrame({
        "candidate_id": ["a", "b"], "station_id": ["S1", "S2"], "case_id": [None, "K1"],
        "ts_start_utc": ["2026-09-05T13:00:00Z", "2026-09-01T00:00:00Z"],
        "ts_end_utc": ["2026-09-05T13:10:00Z", "2026-09-01T00:10:00Z"],
        "emitted_at_utc": [None, "2026-09-01T00:15:00Z"],
        "actions": [["TICKET", "QUARANTINE", "TICKET"], None], "verdict": ["SENSOR_FAULT", None], "severity": [None, "MINOR"],
    })
    df = derive(raw)
    assert list(df.candidate_id) == ["b", "a"]                                   # sorted by visible_at
    b, a = df.iloc[0], df.iloc[1]
    assert a.visible_at == pd.Timestamp("2026-09-05T13:10:00Z")                  # falls back to ts_end_utc
    assert a.actions == ["QUARANTINE", "TICKET"] and a.primary_action == "QUARANTINE"
    assert b.actions == [] and b.primary_action == "LOG"
    assert a.event_key == "S1_20260905_b2" and b.event_key == "K1"
    assert b.verdict == "INCONCLUSIVE" and a.severity == "NONE"


# ----------------------------------------------------------------------------- parsing stored JSON
@pytest.mark.parametrize("txt, expected", [
    (None, []), ('["A", "B"]', ["A", "B"]), ('[{"id": "A"}, "B"]', ["A", "B"]), ("A, B C", ["A", "B", "C"]), ("[A,B]", ["A", "B"]),
])
def test_ids(txt, expected):
    assert _ids(txt) == expected


def test_nearby():
    assert _nearby('[{"id": "A", "km": "4.5"}, {"id": "B"}, "junk"]') == [("A", 4.5), ("B", 0.0)]
    assert _nearby("not json") == [] and _nearby(None) == []


def test_neighbour_pcts():
    state = {"neighbours": {"connected_same_river": {"deltas_pct": {"A": 0.5, "B": None}}, "other_rivers": {"deltas_pct": {"C": "-0.25"}}}}
    assert neighbour_pcts(json.dumps(state)) == {"A": 0.5, "C": -0.25}
    assert neighbour_pcts(None) == {} and neighbour_pcts("{bad") == {}


def test_p99():
    assert _p99({"p99_cms": '{"9": 300, "10": 200}'}, 9) == 300.0
    assert _p99({"p99_cms": '{"10": 200}'}, 9) == 200.0                         # falls back to the first month
    assert _p99({"p99_cms": "250"}, 9) == 250.0
    assert _p99({"p99_cms": "nope"}, 9) is None


# ----------------------------------------------------------------------------- plain-language text
def test_fmt_is_mdt():
    assert fmt(pd.Timestamp("2026-09-05T12:05:00Z")) == "Sep 05, 6:05 AM"


def test_nice_name():
    assert nice_name("BOW RIVER AT CALGARY") == "Bow River at Calgary"
    assert nice_name("the elbow below 10b weir") == "The Elbow below 10B Weir"
    assert nice_name(None) == ""


def test_humanize():
    assert humanize("rise_frac_6h of 1.5675675 above plausible_max") == "6-hour rise of 1.567 above plausible maximum"
    assert humanize("some_other_field") == "some other field"


def test_pct():
    assert (pct(0.876), pct(None), pct(1)) == (88, 0, 100)
