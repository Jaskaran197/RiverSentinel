"""
Media-player control for the Dev Panel: previous / play-pause / next buttons (inline SVG) over a seek bar with a draggable
playhead, a faint dot wherever a record exists and the current record highlighted. Built on st.components.v2 (inline
HTML/CSS/JS, no build step); styled to match the Dev Panel's dark monospace look.

The component sends one-shot triggers, handled in on-change callbacks (which run before the script, so the page renders
the new state in the same rerun):
  action: {"a": "prev" | "toggle" | "next", "n": nonce}
  seek:   {"ms": epoch_ms, "n": nonce}            when the user releases the playhead or uses the arrow keys
Times are shown in MDT (UTC-6, fixed), matching logic.fmt.
"""
from __future__ import annotations
from datetime import datetime, timezone

import streamlit as st

_CSS = """
:host{--fg:#d8d8d4;--mute:#8b8b86;--line:#3a3b3e;--acc:#7ee0b5;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
.pl{user-select:none;-webkit-user-select:none}
.btns{display:flex;gap:6px}
.btns button{flex:1;height:30px;display:grid;place-items:center;background:transparent;border:1px solid var(--line);border-radius:2px;
             color:var(--fg);cursor:pointer;padding:0}
.btns button:hover{border-color:var(--acc);color:var(--acc)}
.btns button:focus-visible{outline:1px dashed var(--acc);outline-offset:2px}
.btns button.main{border-color:var(--acc);color:var(--acc)}
.btns svg{width:16px;height:16px;fill:currentColor}
.sb{position:relative;padding:26px 7px 2px 7px}
.track{position:relative;height:4px;background:var(--line);cursor:pointer;touch-action:none}
.track::before{content:"";position:absolute;inset:-12px 0}                      /* bigger hit area */
.fill{position:absolute;left:0;top:0;bottom:0;background:var(--acc);opacity:.35}
.dot{position:absolute;top:50%;width:3px;height:9px;margin:-4.5px 0 0 -1.5px;background:var(--mute);opacity:.6;pointer-events:none}
.cur{position:absolute;top:50%;width:3px;height:15px;margin:-7.5px 0 0 -1.5px;background:var(--fg);pointer-events:none;display:none}
.handle{position:absolute;top:50%;width:11px;height:11px;margin:-5.5px 0 0 -5.5px;background:var(--acc);cursor:grab;outline:none;transform:rotate(45deg)}
.handle:focus-visible{box-shadow:0 0 0 3px rgba(126,224,181,.35)}
.sb.drag .handle{cursor:grabbing}
.bubble{position:absolute;top:2px;transform:translateX(-50%);white-space:nowrap;font-size:.74rem;color:var(--acc);pointer-events:none}
.ends{display:flex;justify-content:space-between;margin-top:7px;font-size:.68rem;color:var(--mute)}
"""

_ICON = {
    "prev": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11.5 6v12L3 12zM21 6v12l-8.5-6z"/></svg>',
    "next": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6v12l8.5-6zM12.5 6v12L21 12z"/></svg>',
    "play": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5v14l12-7z"/></svg>',
    "pause": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>',
}

_HTML = f"""<div class="pl">
<div class="btns"><button data-a="prev" title="Previous record" aria-label="Previous record">{_ICON["prev"]}</button>
<button data-a="toggle" class="main" title="Play" aria-label="Play">{_ICON["play"]}</button>
<button data-a="next" title="Next record" aria-label="Next record">{_ICON["next"]}</button></div>
<div class="sb"><div class="bubble"></div><div class="track"><div class="fill"></div><div class="dots"></div>
<div class="cur"></div><div class="handle" tabindex="0" role="slider" aria-label="Playback position"></div></div>
<div class="ends"><span class="e0"></span><span class="e1"></span></div></div></div>"""

_JS = """
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const PLAY = %s, PAUSE = %s;
function fmt(ms) {                                   // "Sep 05, 6:05 AM" in MDT (UTC-6), same as logic.fmt
  const d = new Date(ms - 6 * 3600e3), h = d.getUTCHours(), m = String(d.getUTCMinutes()).padStart(2, "0");
  return `${MON[d.getUTCMonth()]} ${String(d.getUTCDate()).padStart(2, "0")}, ${h %% 12 || 12}:${m} ${h < 12 ? "AM" : "PM"}`;
}
function day(ms) { const d = new Date(ms - 6 * 3600e3); return `${MON[d.getUTCMonth()]} ${d.getUTCDate()}`; }

export default function (component) {
  const { data, setTriggerValue, parentElement } = component;
  const root = parentElement.querySelector(".pl"), sb = root.querySelector(".sb");
  let s = parentElement.__player;
  if (!s) {                                          // first mount: wire events once; later calls only update
    s = parentElement.__player = { dragging: false, dotsKey: "" };
    const track = root.querySelector(".track"), handle = root.querySelector(".handle"), toggle = root.querySelector('[data-a="toggle"]');
    root.querySelectorAll(".btns button").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.a === "toggle") s.paintToggle(!s.data.playing);       // optimistic; the rerun confirms it
      setTriggerValue("action", { a: b.dataset.a, n: Date.now() });
    }));
    s.paintToggle = (playing) => {
      toggle.innerHTML = playing ? PAUSE : PLAY;
      toggle.title = playing ? "Pause" : "Play"; toggle.setAttribute("aria-label", toggle.title);
    };
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
      s.dragging = true; sb.classList.add("drag"); track.setPointerCapture(e.pointerId); place(s.data.t0 + frac(e.clientX) * (s.data.t1 - s.data.t0));
    });
    track.addEventListener("pointermove", (e) => { if (s.dragging) place(s.data.t0 + frac(e.clientX) * (s.data.t1 - s.data.t0)); });
    track.addEventListener("pointerup", (e) => { if (!s.dragging) return; s.dragging = false; sb.classList.remove("drag"); release(e.clientX); });
    track.addEventListener("pointercancel", () => { s.dragging = false; sb.classList.remove("drag"); place(s.data.t); });
    handle.addEventListener("keydown", (e) => {     // arrows: previous/next record; shift+arrows: one hour
      const d = s.data; let ms = null;
      if (e.shiftKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) ms = d.t + (e.key === "ArrowLeft" ? -1 : 1) * 3600e3;
      else if (e.key === "ArrowLeft") { const p = d.dots.filter((v) => v + 60e3 < d.t); ms = p.length ? p[p.length - 1] + 60e3 : d.t0; }
      else if (e.key === "ArrowRight") { const n = d.dots.find((v) => v + 60e3 > d.t); ms = n !== undefined ? n + 60e3 : d.t1; }
      if (ms !== null) { e.preventDefault(); place(ms); commit(Math.min(d.t1, Math.max(d.t0, ms))); }
    });
    function place(ms) {
      const d = s.data, f = Math.min(1, Math.max(0, (ms - d.t0) / (d.t1 - d.t0))), pc = (f * 100) + "%%";
      root.querySelector(".handle").style.left = pc; root.querySelector(".fill").style.width = pc;
      const b = root.querySelector(".bubble"); b.textContent = fmt(ms);
      b.style.left = `clamp(52px, ${pc}, calc(100%% - 52px))`;
      root.querySelector(".handle").setAttribute("aria-valuetext", fmt(ms));
    }
    s.place = place;
  }
  s.data = data;
  s.paintToggle(data.playing);
  const key = data.dots.length + ":" + data.dots[0] + ":" + data.dots[data.dots.length - 1];
  if (key !== s.dotsKey) {                           // redraw dots only when the record set changes
    s.dotsKey = key;
    root.querySelector(".dots").innerHTML = data.dots.map((v) => `<div class="dot" style="left:${(v - data.t0) / (data.t1 - data.t0) * 100}%%"></div>`).join("");
  }
  const cur = root.querySelector(".cur");
  if (data.cur !== null && data.cur !== undefined) { cur.style.display = "block"; cur.style.left = ((data.cur - data.t0) / (data.t1 - data.t0) * 100) + "%%"; }
  else cur.style.display = "none";
  root.querySelector(".e0").textContent = day(data.t0); root.querySelector(".e1").textContent = day(data.t1);
  if (!s.dragging) s.place(data.t);                  // never yank the handle out from under the user's finger
}
""" % (repr(_ICON["play"]), repr(_ICON["pause"]))

_component = st.components.v2.component("rs_player", html=_HTML, css=_CSS, js=_JS)


def _ms(t) -> int:
    return int(t.timestamp() * 1000)


def player(t0: datetime, t1: datetime, t: datetime, dots, cur, playing: bool, *, key: str, on_seek, on_action, step_min: int = 5):
    """Mount the player. dots: iterable of record times; cur: the current record's time or None.
    Callbacks run before the script reruns: on_seek(new_t: datetime) after a drag or arrow key; on_action("prev" | "toggle" | "next")."""
    def _trigger(name):
        v = st.session_state[key]
        return v.get(name) if hasattr(v, "get") else getattr(v, name, None)
    def _seek():
        s = _trigger("seek")
        if s and s.get("ms") is not None:
            on_seek(datetime.fromtimestamp(s["ms"] / 1000, tz=timezone.utc))
    def _action():
        a = _trigger("action")
        if a and a.get("a"):
            on_action(a["a"])
    data = {"t0": _ms(t0), "t1": _ms(t1), "t": _ms(t), "step": step_min * 60_000, "playing": bool(playing),
            "dots": sorted({_ms(d) for d in dots}), "cur": _ms(cur) if cur is not None else None}
    return _component(key=key, data=data, on_seek_change=_seek, on_action_change=_action)
