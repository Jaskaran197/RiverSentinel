#!/usr/bin/env python3
"""
Databricks go/no-go for the local demo. Run after filling .env:

  python scripts/test_databricks.py            # connection, tables, one live ai_decide, one live ai_query (with timings)
  python scripts/test_databricks.py --no-ai    # skip the model calls

Reads DATABRICKS_HOST, DATABRICKS_WAREHOUSE_HTTP_PATH, DATABRICKS_TOKEN (and optional CATALOG, LLM_MODEL) from .env in this
folder, the parent folder, or the environment.
"""
import argparse, json, os, sys, time
from pathlib import Path


def load_env():
    here = Path(__file__).resolve().parent
    for p in (here / ".env", here.parent / ".env", here.parent.parent / ".env", Path.cwd() / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            print(f"[env] loaded {p}")
            return
    print("[env] no .env found; using the process environment")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true")
    a = ap.parse_args()
    load_env()
    missing = [k for k in ("DATABRICKS_HOST", "DATABRICKS_WAREHOUSE_HTTP_PATH", "DATABRICKS_TOKEN") if not os.environ.get(k)]
    if missing:
        sys.exit(f"[env] missing: {', '.join(missing)} — see SETUP_LOCAL.md step 3")
    catalog = os.environ.get("CATALOG", "river")
    model = os.environ.get("LLM_MODEL", "databricks-meta-llama-3-3-70b-instruct")

    try:
        from databricks import sql
    except ImportError:
        sys.exit("[pip] databricks-sql-connector not installed: pip install -r requirements.txt")

    host = os.environ["DATABRICKS_HOST"].replace("https://", "").rstrip("/")
    t0 = time.perf_counter()
    try:
        c = sql.connect(server_hostname=host, http_path=os.environ["DATABRICKS_WAREHOUSE_HTTP_PATH"], access_token=os.environ["DATABRICKS_TOKEN"])
    except Exception as e:
        sys.exit(f"[conn] FAIL: {str(e)[:300]}\n  → host wrong? token expired? warehouse HTTP path wrong? (SQL Warehouses → Connection details)")
    print(f"[conn] OK  {host}  ({(time.perf_counter()-t0)*1000:.0f} ms, includes warehouse wake-up if it was asleep)")

    def one(sql_text, params=None):
        t = time.perf_counter()
        with c.cursor() as cur:
            cur.execute(sql_text, params) if params else cur.execute(sql_text)
            row = cur.fetchone()
        return (row[0] if row else None), (time.perf_counter() - t) * 1000

    who, _ = one("SELECT current_user()")
    print(f"[auth] signed in as {who}")

    for t in (f"{catalog}.silver.station_context", f"{catalog}.silver.candidates", f"{catalog}.silver.gate_decisions",
              f"{catalog}.silver.readings", f"{catalog}.gold.verdicts", f"{catalog}.gold.actions"):
        try:
            n, ms = one(f"SELECT count(*) FROM {t}")
            print(f"[table] {t:<40} {n:>8} rows  ({ms:.0f} ms)")
        except Exception as e:
            print(f"[table] {t:<40} MISSING or no access: {str(e)[:80]}")

    if a.no_ai:
        return
    # a real candidate's stored state, so the live calls use exactly the pipeline's input
    try:
        state, _ = one(f"SELECT state_json FROM {catalog}.silver.gate_decisions WHERE case_id = 'E1' LIMIT 1")
    except Exception:
        state = None
    if not state:
        state = json.dumps({"station": {"name": "Bow River at Calgary", "regulated": True}, "detector": {"rule": "PHYSICAL_BREACH"},
                            "reading": {"value": 0.0, "delta_6h": -70.7, "physical_min": 5.0, "plausible_max": 3000.0},
                            "neighbours": {"connected_same_river": {"n_rising": 0}}, "weather": {"rain_24h_mm": 0.0}})
        print("[ai] no stored E1 state found; using a small synthetic one")
    questions = json.dumps({"label": {"type": "choice", "instructions": "What best explains this anomaly?",
                                      "criteria": {"SENSOR_FAULT": "the instrument or telemetry is wrong",
                                                   "OPERATIONAL_CHANGE": "a dam, canal or operator action changed the flow",
                                                   "NATURAL_EVENT": "rain, snowmelt or a real hydrological change"}},
                            "impossible": {"type": "noul", "instructions": "The reading is at or below the physical minimum for this station."}})
    try:
        # twice: the first call pays a one-off warm-up; the second is what the demo will see
        r, ms1 = one("SELECT ai_decide(:state, :q, map('version','1.0'))", {"state": state, "q": questions})
        r, ms2 = one("SELECT ai_decide(:state, :q, map('version','1.0'))", {"state": state, "q": questions})
        if isinstance(r, (bytes, bytearray)): r = r.decode()
        if isinstance(r, str): r = json.loads(r)
        ans = (r.get("response") or {}).get("answers") or {}
        print(f"[ai_decide] OK  first {ms1:.0f} ms, warm {ms2:.0f} ms  label={ (ans.get('label') or {}).get('choice') }  probs={ (ans.get('label') or {}).get('probabilities') }")
    except Exception as e:
        print(f"[ai_decide] FAIL: {str(e)[:200]}\n  → not enabled on this workspace? (Previews) The app falls back to stored results.")
    try:
        r, ms = one("SELECT ai_query(:m, :p, responseFormat => '{\"type\":\"json_schema\",\"json_schema\":{\"name\":\"v\",\"strict\":true,\"schema\":{\"type\":\"object\",\"properties\":{\"verdict\":{\"type\":\"string\"},\"rationale\":{\"type\":\"string\"}},\"required\":[\"verdict\",\"rationale\"]}}}')",
                    {"m": model, "p": "In one sentence, classify this river-gauge anomaly as SENSOR_FAULT, OPERATIONAL_CHANGE or NATURAL_EVENT and say why. STATE: " + state})
        print(f"[ai_query]  OK  {ms:.0f} ms  model={model}  → {str(r)[:160]}")
    except Exception as e:
        print(f"[ai_query]  FAIL: {str(e)[:200]}\n  → check the model name (Serving → endpoints) or Foundation Model API access; set LLM_MODEL in .env")
    c.close()
    print("\n[ok] Databricks is ready for the live demo. Keep the warehouse warm before presenting (any query within ~10 min).")


if __name__ == "__main__":
    main()
