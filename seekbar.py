"""
Seek bar for the Dev Panel: a draggable playhead over a fixed time range, with a faint dot wherever a record exists and the
current record highlighted. Built on st.components.v2 (inline HTML/CSS/JS, no build step).

The component sends a one-shot trigger {"ms": epoch_ms, "n": nonce} when the user releases the playhead; the caller handles
it in the on-change callback (which runs before the script, so the rest of the page renders at the new time).
Times are shown in MDT (UTC-6, fixed), matching logic.fmt.
"""
from __future__ import annotations
from datetime import datetime, timezone

import streamlit as st

_CSS = """
.sb{position:relative;padding:30px 9px 4px 9px;user-select:none;-webkit-user-select:none;font-family:inherit}
.track{position:relative;height:6px;border-radius:3px;background:#e1e0d9;cursor:pointer;touch-action:none}
.track::before{content:"";position:absolute;inset:-12px 0}                      /* bigger hit area */
.fill{position:absolute;left:0;top:0;bottom:0;border-radius:3px;background:var(--st-primary-color,#ff4b4b);opacity:.35}
.dot{position:absolute;top:50%;width:5px;height:5px;margin:-2.5px 0 0 -2.5px;border-radius:50%;background:#898781;opacity:.55;pointer-events:none}
.cur{position:absolute;top:50%;width:11px;height:11px;margin:-5.5px 0 0 -5.5px;border-radius:50%;background:#0b0b0b;
     box-shadow:0 0 0 3px #fff;pointer-events:none;display:none}
.handle{position:absolute;top:50%;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;background:var(--st-primary-color,#ff4b4b);
        box-shadow:0 1px 3px rgba(0,0,0,.25);cursor:grab;outline:none}
.handle:focus-visible{box-shadow:0 0 0 4px rgba(255,75,75,.3)}
.sb.drag .handle{cursor:grabbing}
.bubble{position:absolute;top:2px;transform:translateX(-50%);white-space:nowrap;font-size:.8rem;color:var(--st-primary-color,#ff4b4b);pointer-events:none}
.ends{display:flex;justify-content:space-between;margin-top:8px;font-size:.72rem;color:#898781}
"""

_HTML = """<div class="sb"><div class="bubble"></div><div class="track"><div class="fill"></div><div class="dots"></div>
<div class="cur"></div><div class="handle" tabindex="0" role="slider" aria-label="Playback position"></div></div>
<div class="ends"><span class="e0"></span><span class="e1"></span></div></div>"""

_JS = """
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function fmt(ms) {                                   // "Sep 05, 6:05 AM" in MDT (UTC-6), same as logic.fmt
  const d = new Date(ms - 6 * 3600e3), h = d.getUTCHours(), m = String(d.getUTCMinutes()).padStart(2, "0");
  return `${MON[d.getUTCMonth()]} ${String(d.getUTCDate()).padStart(2, "0")}, ${h % 12 || 12}:${m} ${h < 12 ? "AM" : "PM"}`;
}
function day(ms) { const d = new Date(ms - 6 * 3600e3); return `${MON[d.getUTCMonth()]} ${d.getUTCDate()}`; }

export default function (component) {
  const { data, setTriggerValue, parentElement } = component;
  const root = parentElement.querySelector(".sb");
  let s = parentElement.__seek;
  if (!s) {                                          // first mount: wire events once; later calls only update
    s = parentElement.__seek = { dragging: false, dotsKey: "" };
    const track = root.querySelector(".track"), handle = root.querySelector(".handle");
    const frac = (x) => { const r = track.getBoundingClientRect(); return Math.min(1, Math.max(0, (x - r.left) / r.width)); };
    const commit = (ms) => setTriggerValue("seek", { ms: Math.round(ms), n: Date.now() });
    const release = (x) => {
      const d = s.data, r = track.getBoundingClientRect(), f = frac(x);
      let ms = d.t0 + f * (d.t1 - d.t0);
      let best = null, bestPx = 7;                   // snap to a record within 7 px: land just after it, so it is the current one
      for (const v of d.dots) { const px = Math.abs((v - ms) / (d.t1 - d.t0) * r.width); if (px < bestPx) { bestPx = px; best = v; } }
      ms = best !== null ? best + 60e3 : Math.round(ms / d.step) * d.step;
      commit(Math.min(d.t1, Math.max(d.t0, ms)));
    };
    track.addEventListener("pointerdown", (e) => {
      s.dragging = true; root.classList.add("drag"); track.setPointerCapture(e.pointerId); place(s.data.t0 + frac(e.clientX) * (s.data.t1 - s.data.t0));
    });
    track.addEventListener("pointermove", (e) => { if (s.dragging) place(s.data.t0 + frac(e.clientX) * (s.data.t1 - s.data.t0)); });
    track.addEventListener("pointerup", (e) => { if (!s.dragging) return; s.dragging = false; root.classList.remove("drag"); release(e.clientX); });
    track.addEventListener("pointercancel", () => { s.dragging = false; root.classList.remove("drag"); place(s.data.t); });
    handle.addEventListener("keydown", (e) => {     // arrows: previous/next record; shift+arrows: one hour
      const d = s.data; let ms = null;
      if (e.shiftKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) ms = d.t + (e.key === "ArrowLeft" ? -1 : 1) * 3600e3;
      else if (e.key === "ArrowLeft") { const p = d.dots.filter((v) => v + 60e3 < d.t); ms = p.length ? p[p.length - 1] + 60e3 : d.t0; }
      else if (e.key === "ArrowRight") { const n = d.dots.find((v) => v + 60e3 > d.t); ms = n !== undefined ? n + 60e3 : d.t1; }
      if (ms !== null) { e.preventDefault(); place(ms); commit(Math.min(d.t1, Math.max(d.t0, ms))); }
    });
    function place(ms) {
      const d = s.data, f = Math.min(1, Math.max(0, (ms - d.t0) / (d.t1 - d.t0))), pc = (f * 100) + "%";
      root.querySelector(".handle").style.left = pc; root.querySelector(".fill").style.width = pc;
      const b = root.querySelector(".bubble"); b.textContent = fmt(ms);
      b.style.left = `clamp(48px, ${pc}, calc(100% - 48px))`;
      root.querySelector(".handle").setAttribute("aria-valuetext", fmt(ms));
    }
    s.place = place;
  }
  s.data = data;
  const key = data.dots.length + ":" + data.dots[0] + ":" + data.dots[data.dots.length - 1];
  if (key !== s.dotsKey) {                           // redraw dots only when the record set changes
    s.dotsKey = key;
    root.querySelector(".dots").innerHTML = data.dots.map((v) => `<div class="dot" style="left:${(v - data.t0) / (data.t1 - data.t0) * 100}%"></div>`).join("");
  }
  const cur = root.querySelector(".cur");
  if (data.cur !== null && data.cur !== undefined) { cur.style.display = "block"; cur.style.left = ((data.cur - data.t0) / (data.t1 - data.t0) * 100) + "%"; }
  else cur.style.display = "none";
  root.querySelector(".e0").textContent = day(data.t0); root.querySelector(".e1").textContent = day(data.t1);
  if (!s.dragging) s.place(data.t);                  // never yank the handle out from under the user's finger
}
"""

_component = st.components.v2.component("rs_seekbar", html=_HTML, css=_CSS, js=_JS)


def _ms(t) -> int:
    return int(t.timestamp() * 1000)


def seek_bar(t0: datetime, t1: datetime, t: datetime, dots, cur, *, key: str, on_seek, step_min: int = 5):
    """Mount the seek bar. dots: iterable of record times; cur: the current record's time or None.
    on_seek(new_t: datetime) is called (before the script reruns) when the user releases the playhead."""
    def _changed():
        v = st.session_state[key]
        s = v.get("seek") if hasattr(v, "get") else getattr(v, "seek", None)
        if s and s.get("ms") is not None:
            on_seek(datetime.fromtimestamp(s["ms"] / 1000, tz=timezone.utc))
    data = {"t0": _ms(t0), "t1": _ms(t1), "t": _ms(t), "step": step_min * 60_000,
            "dots": sorted({_ms(d) for d in dots}), "cur": _ms(cur) if cur is not None else None}
    return _component(key=key, data=data, on_seek_change=_changed)
