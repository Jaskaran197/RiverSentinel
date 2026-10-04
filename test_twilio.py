#!/usr/bin/env python3
"""
Twilio go/no-go test (stdlib only, no pip install needed).

  python scripts/test_twilio.py                      # 1) validate credentials + number, no call
  python scripts/test_twilio.py --call +14035551234  # 2) ring that phone, poll until a final status
  python scripts/test_twilio.py --call +14035551234 --no-answer-test
                                                     # 3) same, but DON'T pick up: verifies we see 'no-answer'

Reads TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER from a .env in this folder,
the parent folder, or the environment. Never prints the auth token.
"""
import argparse, base64, json, os, sys, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path

API = "https://api.twilio.com/2010-04-01"
FINAL = {"completed", "no-answer", "busy", "failed", "canceled"}


def load_env():
    here = Path(__file__).resolve().parent
    for p in (here / ".env", here.parent / ".env", here.parent.parent / ".env", Path.cwd() / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            print(f"[env] loaded {p}")
            break
    missing = [k for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER") if not os.environ.get(k)]
    if missing:
        sys.exit(f"[env] missing: {', '.join(missing)}")


def req(method, path, data=None):
    sid, tok = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    url = f"{API}/Accounts/{sid}{path}"
    body = urllib.parse.urlencode(data).encode() if data else None
    r = urllib.request.Request(url, data=body, method=method)
    r.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{tok}".encode()).decode())
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"message": str(e)}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        sys.exit(f"[net] cannot reach api.twilio.com: {e}  -> run this from a normal terminal/Wi-Fi, not a locked-down VM")


def check_account():
    code, acct = req("GET", ".json")
    if code != 200:
        sys.exit(f"[auth] FAIL {code}: {acct.get('message')}  -> check TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN")
    print(f"[auth] OK  account '{acct.get('friendly_name')}'  type={acct.get('type')}  status={acct.get('status')}")
    trial = acct.get("type") == "Trial"
    if trial:
        print("[auth] TRIAL account: can only call Verified Caller IDs and plays a trial announcement first.")

    code, bal = req("GET", "/Balance.json")
    if code == 200:
        print(f"[bal]  balance {bal.get('balance')} {bal.get('currency')}")

    frm = os.environ["TWILIO_FROM_NUMBER"]
    code, nums = req("GET", "/IncomingPhoneNumbers.json?PageSize=50")
    owned = [n for n in nums.get("incoming_phone_numbers", [])] if code == 200 else []
    mine = [n for n in owned if n.get("phone_number") == frm]
    if not owned:
        print("[num]  WARN: no phone numbers on this account yet (buy one with Voice capability).")
    elif not mine:
        print(f"[num]  FAIL: TWILIO_FROM_NUMBER {frm} is not one of your numbers: {[n['phone_number'] for n in owned]}")
    else:
        caps = mine[0].get("capabilities", {})
        print(f"[num]  OK  {frm}  voice={caps.get('voice')}  sms={caps.get('sms')}")
        if not caps.get("voice"):
            print("[num]  FAIL: number has no voice capability.")

    code, ver = req("GET", "/OutgoingCallerIds.json?PageSize=50")
    if code == 200:
        ids = [c.get("phone_number") for c in ver.get("outgoing_caller_ids", [])]
        print(f"[ids]  verified caller IDs: {ids or 'none'}" + ("  <- on a trial, only these can be called" if trial else ""))
    return trial


def place_call(to, expect_no_answer):
    twiml = ("<Response><Say voice=\"alice\">This is River Sentinel. Test call successful. "
             "Danger to life alert at Bow River at Calgary.</Say></Response>")
    code, call = req("POST", "/Calls.json", {
        "To": to, "From": os.environ["TWILIO_FROM_NUMBER"], "Twiml": twiml, "Timeout": "25",
    })
    if code not in (200, 201):
        msg = call.get("message", "")
        hint = ""
        if "21219" in str(call.get("code")) or "not a valid" in msg or "unverified" in msg.lower():
            hint = "  -> trial account: verify this number under Phone Numbers → Verified Caller IDs"
        if "21215" in str(call.get("code")) or "permission" in msg.lower():
            hint = "  -> enable Canada under Voice → Settings → Geo permissions"
        sys.exit(f"[call] FAIL {code} (twilio code {call.get('code')}): {msg}{hint}")
    sid = call["sid"]
    print(f"[call] placed  CallSid={sid}  status={call['status']}" + ("  (do NOT answer)" if expect_no_answer else "  (answer it)"))
    t0 = time.time()
    status = call["status"]
    while status not in FINAL and time.time() - t0 < 90:
        time.sleep(3)
        code, c = req("GET", f"/Calls/{sid}.json")
        status = c.get("status", status)
        print(f"[call] {int(time.time()-t0):3d}s  status={status}")
    print(f"[call] FINAL status={status}")
    if expect_no_answer:
        ok = status in ("no-answer", "busy")
        print("[esc]  " + ("PASS: 'no-answer' detected -> escalation trigger works" if ok
                           else f"FAIL: expected no-answer/busy, got '{status}' (did someone pick up, or did voicemail answer? "
                                "Voicemail counts as 'completed' — use Timeout shorter than the voicemail delay)"))
    else:
        print("[esc]  " + ("PASS: call completed" if status == "completed" else f"check: final status '{status}'"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--call", metavar="+1XXXXXXXXXX", help="phone number to ring (E.164)")
    ap.add_argument("--no-answer-test", action="store_true", help="let it ring out; verify we observe 'no-answer'")
    a = ap.parse_args()
    load_env()
    trial = check_account()
    if a.call:
        place_call(a.call, a.no_answer_test)
    else:
        print("\nNext: python scripts/test_twilio.py --call +1403XXXXXXX   (then again with --no-answer-test)")
