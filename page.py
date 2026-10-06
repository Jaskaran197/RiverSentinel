#!/usr/bin/env python3
"""
RiverSentinel voice page: ElevenLabs agent calls the duty officer via Twilio, escalates on no-answer.
Stdlib only. Reads .env from this folder, its parents, or the environment.

  python page.py --setup                 # list agents + imported phone numbers, suggest .env lines
  python page.py --test                  # call CONTACT_1 with the demo flood alert (no escalation)
  python page.py --test --escalate       # call CONTACT_1; if unanswered in RING_TIMEOUT_S, call CONTACT_2
  python page.py --station "Bow River at Calgary" --severity "Danger to life" \
        --summary "Discharge rose from 70 to 1900 cubic metres per second in six hours." \
        --evidence "Rate of rise 2600 percent; upstream Cochrane rising; 240 mm rain in 48 h; flood warning active." \
        --escalate

Exit codes: 0 answered/completed, 2 no-answer on all contacts, 1 error.

Importable:  from page import page;  page(contacts, alert, escalate=True)
"""
import argparse, base64, json, os, sys, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path

EL = "https://api.elevenlabs.io"
TW = "https://api.twilio.com/2010-04-01"
TW_FINAL = {"completed", "no-answer", "busy", "failed", "canceled"}

DEMO_ALERT = {
    "severity": "Danger to life",
    "station": "Bow River at Calgary",
    "summary": ("Discharge rose from 70 to 1,900 cubic metres per second in six hours, "
                "with 240 millimetres of rain upstream and an active flood warning."),
    "evidence": ("Rate of rise 2,600 percent in six hours. Upstream gauge at Cochrane rising. "
                 "Rainfall 240 millimetres in 48 hours at Springbank. "
                 "Environment Canada flood warning issued at 2:10 UTC. "
                 "Sensor physics check passed: value is within the plausible range for this station."),
}


# ---------- env ----------
def load_env():
    here = Path(__file__).resolve().parent
    for p in (here / ".env", here.parent / ".env", here.parent.parent / ".env", Path.cwd() / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break


def need(*keys):
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        sys.exit(f"[env] missing: {', '.join(missing)}  (run --setup to discover ElevenLabs ids)")


# ---------- http ----------
def http(method, url, headers=None, json_body=None, form=None, timeout=30):
    data = None
    h = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode(); h["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"message": raw}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        sys.exit(f"[net] cannot reach {urllib.parse.urlparse(url).netloc}: {e}")


def el_headers():
    return {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}


def tw_headers():
    sid, tok = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    return {"Authorization": "Basic " + base64.b64encode(f"{sid}:{tok}".encode()).decode()}


def mask(num):
    return num[:-4].replace("+", "+") [:3] + "***" + num[-4:] if num else num


# ---------- setup / discovery ----------
def setup():
    need("ELEVENLABS_API_KEY")
    code, agents = http("GET", f"{EL}/v1/convai/agents?page_size=30", el_headers())
    if code != 200:
        sys.exit(f"[el] agents list FAIL {code}: {agents}")
    print("[el] agents:")
    for a in agents.get("agents", []):
        print(f"      {a.get('agent_id')}  {a.get('name')}")
    code, nums = http("GET", f"{EL}/v1/convai/phone-numbers", el_headers())
    if code != 200:
        sys.exit(f"[el] phone-numbers list FAIL {code}: {nums}")
    nums = nums if isinstance(nums, list) else nums.get("phone_numbers", [])
    print("[el] imported phone numbers:")
    for n in nums:
        agent = (n.get("assigned_agent") or {}).get("agent_id") or (n.get("assigned_agent") or {}).get("agent_name")
        print(f"      {n.get('phone_number_id')}  {n.get('phone_number')}  provider={n.get('provider')}  label={n.get('label')}  agent={agent}")
    frm = os.environ.get("TWILIO_FROM_NUMBER")
    match = [n for n in nums if n.get("phone_number") == frm]
    print("\n[setup] add to .env:")
    if not os.environ.get("ELEVENLABS_AGENT_ID") and agents.get("agents"):
        print(f"ELEVENLABS_AGENT_ID={agents['agents'][0]['agent_id']}")
    if match:
        print(f"ELEVENLABS_PHONE_NUMBER_ID={match[0]['phone_number_id']}")
    elif nums:
        print(f"ELEVENLABS_PHONE_NUMBER_ID={nums[0]['phone_number_id']}   # (did not match TWILIO_FROM_NUMBER={frm}; check)")
    else:
        print("# No phone number imported yet: ElevenLabs → Agents → Phone numbers → Import → Twilio (number, Account SID, Auth Token)")
    print("RING_TIMEOUT_S=30")


def resolve_phone_number_id():
    pid = os.environ.get("ELEVENLABS_PHONE_NUMBER_ID")
    if pid:
        return pid
    code, nums = http("GET", f"{EL}/v1/convai/phone-numbers", el_headers())
    nums = nums if isinstance(nums, list) else nums.get("phone_numbers", [])
    frm = os.environ.get("TWILIO_FROM_NUMBER")
    for n in nums:
        if n.get("phone_number") == frm:
            print(f"[el] auto-resolved phone_number_id for {frm}")
            return n["phone_number_id"]
    if len(nums) == 1:
        print(f"[el] using the only imported number {nums[0].get('phone_number')}")
        return nums[0]["phone_number_id"]
    sys.exit("[el] set ELEVENLABS_PHONE_NUMBER_ID in .env (run --setup to list)")


# ---------- the call ----------
def place_agent_call(to_number, alert, phone_number_id):
    body = {
        "agent_id": os.environ["ELEVENLABS_AGENT_ID"],
        "agent_phone_number_id": phone_number_id,
        "to_number": to_number,
        "conversation_initiation_client_data": {
            "dynamic_variables": {
                "severity": alert["severity"],
                "station": alert["station"],
                "summary": alert["summary"],
                "evidence": alert["evidence"],
            }
        },
    }
    code, resp = http("POST", f"{EL}/v1/convai/twilio/outbound-call", el_headers(), json_body=body)
    if code != 200 or not resp.get("success", True):
        return None, None, f"{code}: {resp.get('detail') or resp.get('message') or resp}"
    return resp.get("callSid"), resp.get("conversation_id"), None


def twilio_wait_final(call_sid, ring_timeout_s, max_wait_s=180):
    """Poll Twilio for the call's lifecycle. Returns (final_status, answered: bool)."""
    t0 = time.time(); status = "queued"; answered = False
    while time.time() - t0 < max_wait_s:
        code, c = http("GET", f"{TW}/Accounts/{os.environ['TWILIO_ACCOUNT_SID']}/Calls/{call_sid}.json", tw_headers())
        if code == 200:
            status = c.get("status", status)
        print(f"      {int(time.time()-t0):3d}s  twilio status={status}")
        if status == "in-progress":
            answered = True
        if status in TW_FINAL:
            break
        # ring timeout: if still ringing past the window, hang up so we can escalate quickly
        if not answered and status in ("queued", "ringing", "initiated") and time.time() - t0 > ring_timeout_s:
            http("POST", f"{TW}/Accounts/{os.environ['TWILIO_ACCOUNT_SID']}/Calls/{call_sid}.json",
                 tw_headers(), form={"Status": "completed"})
            print(f"      ring timeout {ring_timeout_s}s reached → cancelled → treating as no-answer")
            return "no-answer", False
        time.sleep(3)
    # voicemail shows up as 'completed' with answered=True quickly; we can't distinguish reliably without AMD
    return status, answered or status == "completed"


def el_conversation_summary(conversation_id):
    if not conversation_id:
        return {}
    code, c = http("GET", f"{EL}/v1/convai/conversations/{conversation_id}", el_headers())
    if code != 200:
        return {}
    return {"status": c.get("status"), "duration_s": (c.get("metadata") or {}).get("call_duration_secs")}


def page(contacts, alert, *, escalate=True, ring_timeout_s=None):
    """Call contacts in order until one answers. Returns list of attempt dicts (what gold.pages stores)."""
    need("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")
    ring_timeout_s = ring_timeout_s or int(os.environ.get("RING_TIMEOUT_S", "30"))
    pid = resolve_phone_number_id()
    attempts = []
    for i, to in enumerate(contacts):
        print(f"[page] contact {i+1}/{len(contacts)} {mask(to)}  severity='{alert['severity']}' station='{alert['station']}'")
        call_sid, conv_id, err = place_agent_call(to, alert, pid)
        if err:
            print(f"[page] FAIL placing call: {err}")
            attempts.append({"contact_order": i + 1, "to": mask(to), "status": "FAILED", "error": err})
            if not escalate:
                break
            continue
        print(f"[page] placed  callSid={call_sid}  conversation_id={conv_id}")
        status, answered = twilio_wait_final(call_sid, ring_timeout_s)
        conv = el_conversation_summary(conv_id)
        rec = {"contact_order": i + 1, "to": mask(to), "call_sid": call_sid, "conversation_id": conv_id,
               "twilio_status": status, "answered": answered, "el": conv}
        if answered:
            rec["status"] = "ANSWERED"; attempts.append(rec)
            print(f"[page] ANSWERED by contact {i+1} (agent conversation {conv.get('status')}, {conv.get('duration_s')}s)")
            return attempts
        rec["status"] = "NO_ANSWER" if i == len(contacts) - 1 or not escalate else "ESCALATED"
        attempts.append(rec)
        print(f"[page] no answer from contact {i+1} ({status})" + ("  → escalating" if escalate and i < len(contacts) - 1 else ""))
        if not escalate:
            break
    print("[page] nobody answered")
    return attempts


# ---------- cli ----------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--test", action="store_true", help="use the demo flood alert")
    ap.add_argument("--escalate", action="store_true", help="call CONTACT_2 if CONTACT_1 does not answer")
    ap.add_argument("--to", help="override first contact number (E.164)")
    ap.add_argument("--station"); ap.add_argument("--severity"); ap.add_argument("--summary"); ap.add_argument("--evidence")
    ap.add_argument("--ring-timeout", type=int, default=None)
    a = ap.parse_args()
    load_env()
    if a.setup:
        setup(); sys.exit(0)

    alert = dict(DEMO_ALERT)
    for k in ("station", "severity", "summary", "evidence"):
        if getattr(a, k):
            alert[k] = getattr(a, k)
    if not a.test and not any(getattr(a, k) for k in ("station", "severity", "summary", "evidence")):
        ap.error("give --test or an alert (--station/--severity/--summary/--evidence)")

    contacts = [a.to or os.environ.get("CONTACT_1")]
    if a.escalate:
        c2 = os.environ.get("CONTACT_2")
        if not c2:
            sys.exit("[env] --escalate needs CONTACT_2 in .env")
        contacts.append(c2)
    contacts = [c for c in contacts if c]
    if not contacts:
        sys.exit("[env] set CONTACT_1 in .env or pass --to")

    result = page(contacts, alert, escalate=a.escalate, ring_timeout_s=a.ring_timeout)
    print(json.dumps(result, indent=2))
    sys.exit(0 if any(r.get("status") == "ANSWERED" for r in result) else 2)