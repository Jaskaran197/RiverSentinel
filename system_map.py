"""
Main-dashboard system map: a fixed-view MapLibre map (loaded from a CDN) built with st.components.v2.

- Basemap: our own monochrome style over OpenFreeMap vector tiles (OpenMapTiles schema) with waterways emphasised,
  major roads and place names for orientation, and hillshade from the AWS Terrain Tiles (Terrarium DEM).
- Stations: dark-grey dots (in the Dev Panel selection), light-grey dots (rest of the system). The current station is a
  crimson pulsing marker and its nearest neighbours (nearby_ids) are azure. Each gets a panel (badge, station id, name, sparkline of
  recent flow) inside the map's right edge, in a band the fit keeps clear of stations: the selected panel is centred on its station,
  neighbours stack above/below it without overlap, closest vertically nearest, each joined to its station by a dashed connector.
- The view fits the system's stations once and never recentres when the current station changes; it refits only when
  the system changes. Pan/zoom are disabled.

The JS is re-invoked on every rerun with new data; the map instance lives on parentElement and is only updated.
"""
from __future__ import annotations

import json

import streamlit as st

MAPLIBRE_JS = "https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js"
MAPLIBRE_CSS = "https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.css"

# "Watermark" palette: a paper-toned duotone with the water washed cobalt. Relationships to keep when restyling: water darker than land,
# rivers the strongest lines, roads lightest; stations ink, out-of-selection stations faded, current = signal (vermilion), neighbours = cobalt.
C = {"bg": "#EAE6DC", "land": "#E1DCCF", "water": "#C3CBEA", "river": "#3D52B5", "stream": "#8F9AC4", "road": "#F8F6F0",
     "road_case": "#CCC5B5", "border": "#A29B8B", "label": "#1F2024", "label_soft": "#6A665C", "halo": "#F2EFE8",
     "station": "#0E0F12", "station_off": "#B5AE9F", "current": "#FF4A1C", "neighbour": "#2440F0"}

STYLE = {
    "version": 8,
    "glyphs": "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    "sources": {
        "omt": {"type": "vector", "url": "https://tiles.openfreemap.org/planet",
                "attribution": '<a href="https://openfreemap.org">OpenFreeMap</a> © <a href="https://www.openmaptiles.org/">OpenMapTiles</a> '
                               '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'},
        "dem": {"type": "raster-dem", "encoding": "terrarium", "tileSize": 256, "maxzoom": 13,
                "tiles": ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
                "attribution": '<a href="https://registry.opendata.aws/terrain-tiles/">AWS Terrain Tiles</a>'},
    },
    "layers": [
        {"id": "bg", "type": "background", "paint": {"background-color": C["bg"]}},
        {"id": "landcover", "type": "fill", "source": "omt", "source-layer": "landcover",
         "filter": ["in", ["get", "class"], ["literal", ["wood", "forest", "grass", "ice"]]],
         "paint": {"fill-color": C["land"], "fill-opacity": 0.6}},
        {"id": "hillshade", "type": "hillshade", "source": "dem",
         "paint": {"hillshade-shadow-color": "#6F6757", "hillshade-highlight-color": "#FFFDF6", "hillshade-accent-color": "#8C8472",
                   "hillshade-exaggeration": 0.38, "hillshade-illumination-direction": 315}},
        {"id": "water", "type": "fill", "source": "omt", "source-layer": "water", "paint": {"fill-color": C["water"]}},
        {"id": "waterway-stream", "type": "line", "source": "omt", "source-layer": "waterway",
         "filter": ["!=", ["get", "class"], "river"], "minzoom": 8,
         "paint": {"line-color": C["stream"], "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.4, 12, 1.2]}},
        {"id": "waterway-river", "type": "line", "source": "omt", "source-layer": "waterway",
         "filter": ["==", ["get", "class"], "river"], "layout": {"line-cap": "round", "line-join": "round"},
         "paint": {"line-color": C["river"], "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.8, 8, 1.8, 12, 3.5]}},
        {"id": "boundary", "type": "line", "source": "omt", "source-layer": "boundary",
         "filter": ["all", ["<=", ["coalesce", ["get", "admin_level"], 99], 4], ["!=", ["coalesce", ["get", "maritime"], 0], 1]],
         "paint": {"line-color": C["border"], "line-width": 1, "line-dasharray": [3, 2]}},
        {"id": "road-case", "type": "line", "source": "omt", "source-layer": "transportation",
         "filter": ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary"]]], "layout": {"line-join": "round"},
         "paint": {"line-color": C["road_case"], "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1.2, 9, 3, 12, 6]}},
        {"id": "road", "type": "line", "source": "omt", "source-layer": "transportation",
         "filter": ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary"]]], "layout": {"line-join": "round"},
         "paint": {"line-color": C["road"], "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.5, 9, 1.6, 12, 4]}},
        {"id": "river-label", "type": "symbol", "source": "omt", "source-layer": "waterway", "minzoom": 7,
         "filter": ["all", ["==", ["get", "class"], "river"], ["has", "name"]],
         "layout": {"symbol-placement": "line", "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
                    "text-font": ["Noto Sans Italic"], "text-size": 10, "text-letter-spacing": 0.05},
         "paint": {"text-color": C["river"], "text-halo-color": C["halo"], "text-halo-width": 1.2}},
        {"id": "place-town", "type": "symbol", "source": "omt", "source-layer": "place", "minzoom": 7,
         "filter": ["==", ["get", "class"], "town"],
         "layout": {"text-field": ["coalesce", ["get", "name:en"], ["get", "name"]], "text-font": ["Noto Sans Regular"], "text-size": 10},
         "paint": {"text-color": C["label_soft"], "text-halo-color": C["halo"], "text-halo-width": 1.2}},
        {"id": "place-city", "type": "symbol", "source": "omt", "source-layer": "place",
         "filter": ["==", ["get", "class"], "city"],
         "layout": {"text-field": ["coalesce", ["get", "name:en"], ["get", "name"]], "text-font": ["Noto Sans Bold"], "text-size": 12,
                    "text-transform": "uppercase", "text-letter-spacing": 0.08},
         "paint": {"text-color": C["label"], "text-halo-color": C["halo"], "text-halo-width": 1.5}},
    ],
}

GUTTER = 240          # px on the map's right kept clear of stations (fit padding) for the station panels, which sit inside the map

_CSS = """
:host{display:block}
.wrap{position:relative}
.rsmap{position:relative;border:1px solid #0E0F12;border-radius:3px;overflow:hidden;background:#EAE6DC}
.canvas{position:absolute;inset:0}
.msg{position:absolute;inset:0;display:grid;place-items:center;color:#8A877E;font:500 11px/1 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase}
.cur{position:relative;width:14px;height:14px}
.cur .dot{position:absolute;inset:0;border-radius:50%;background:var(--cur);box-shadow:0 0 0 2px #FBFAF6,0 0 0 3px rgba(14,15,18,.25)}
.cur .ring{position:absolute;inset:0;border-radius:50%;border:2px solid var(--cur);animation:pulse 1.8s ease-out infinite}
.cur .ring.r2{animation-delay:.9s}
@keyframes pulse{from{transform:scale(1);opacity:.9} to{transform:scale(3.6);opacity:0}}
/* connectors: station -> map edge -> panel notch (straight when the panel sits level with its station, elbowed when displaced) */
svg.links{position:absolute;left:0;top:0;overflow:visible;pointer-events:none;z-index:3}
svg.links path{fill:none;stroke-width:1.2;stroke-dasharray:2 3;opacity:.9;transition:d .35s ease}
/* panels: bolted onto the map's right edge, stacked without overlap; notch at the station's height */
.panel{position:absolute;top:0;background:rgba(251,250,246,.97);border:1px solid #CBC5B7;border-left:3px solid var(--c);border-radius:0 2px 2px 0;
       padding:5px 10px 6px;white-space:nowrap;font:12px/1.3 'Inter Tight',system-ui,sans-serif;color:#0E0F12;box-shadow:0 6px 16px rgba(14,15,18,.12);
       transition:top .35s ease;max-width:calc(var(--gutter) - 16px);z-index:4}
.panel::before{content:"";position:absolute;left:-9px;top:var(--notch,50%);transform:translateY(-50%);border:6px solid transparent;border-right-color:var(--c);transition:top .35s ease}
.panel.nb{padding:3px 9px 4px;font-size:11.5px;line-height:1.25}
.panel.nb .val{font-size:9.5px}
.panel .hd{display:flex;align-items:center;gap:6px}
.panel .id{font:600 11px/1.2 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.04em}
.panel .badge{background:var(--c);color:#FBFAF6;font:600 8.5px/1.5 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;padding:0 5px;border-radius:2px}
.panel .nm{color:#55565C;overflow:hidden;text-overflow:ellipsis}
.panel svg{display:block;margin-top:3px}
.panel .val{font:10px/1.3 'JetBrains Mono',ui-monospace,monospace;color:#55565C;margin-top:1px}
.maplibregl-ctrl-attrib{font-size:10px}
"""

_JS = """
const JS_URL = %s, STYLE = %s, PAD = 44, GAP = 6, EDGE = 8;   // EDGE: panels' inset from the map's top/right/bottom
function loadLib() {                                   // one <script> for the whole page; MapLibre's UMD build sets window.maplibregl
  if (window.maplibregl) return Promise.resolve(window.maplibregl);
  if (!window.__rsMapLib) window.__rsMapLib = new Promise((ok, err) => {
    const sc = document.createElement("script"); sc.src = JS_URL; sc.onload = () => ok(window.maplibregl); sc.onerror = err;
    document.head.appendChild(sc);
  });
  return window.__rsMapLib;
}
function esc(t) { return String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }
function spark(vals, color, h = 30) {                  // inline SVG polyline, 132 x h
  if (!vals || vals.length < 2) return "";
  const w = 132, lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - 2 - (v - lo) / span * (h - 4)).toFixed(1)}`).join(" ");
  const last = pts.split(" ").pop().split(",");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round"/>` +
         `<circle cx="${last[0]}" cy="${last[1]}" r="2.4" fill="${color}"/></svg>`;
}
function fc(stations, nb) {
  return { type: "FeatureCollection", features: stations.map((p) => ({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
           properties: { id: p.id, role: nb.has(p.id) ? "nb" : p.on ? "on" : "off" } })) };
}
function items(data) {                                 // the current station first, then its neighbours
  const c = data.current;
  return c ? [{ ...c, role: "sel", color: data.colors.current }, ...(c.neighbours || []).map((n) => ({ ...n, role: "nb", color: data.colors.neighbour }))] : [];
}
function panelHtml(it) {
  const badge = it.role === "sel" ? "selected" : "neighbour" + (it.km != null ? ` · ${it.km < 10 ? it.km.toFixed(1) : Math.round(it.km)} km` : "");
  return `<div class="hd"><span class="id">${esc(it.id)}</span><span class="badge">${badge}</span></div>` +
         `<div class="nm" title="${esc(it.name)}">${esc(it.name)}</div>` + spark(it.spark, it.color, it.role === "sel" ? 24 : 14) +
         (it.spark_label ? `<div class="val">${esc(it.spark_label)}</div>` : "");
}
function arrange(sel, nb, selTop, H) {
  // Best split of the neighbours into "above"/"below" the selected panel placed at selTop: the stacks above must fit under the
  // map's top; the best split keeps everything on the map (overflow = px past the bottom), then keeps each panel closest to its
  // own station's height. Within a side, panels nearest the selected one belong to the stations closest to it vertically; a
  // panel moved to the "wrong" side goes last.
  const need = (a) => a.reduce((t, i) => t + i.h + GAP, 0);
  let best = null;
  for (let mask = 0; mask < 1 << nb.length; mask++) {
    const above = nb.filter((_, j) => (mask >> j) & 1), below = nb.filter((_, j) => !((mask >> j) & 1));
    if (need(above) > selTop + 1e-9) continue;          // nothing may leave the top of the map
    const rank = (up) => (p, q) => (((p.y < sel.y) === up ? 0 : 1) - ((q.y < sel.y) === up ? 0 : 1)) || Math.abs(p.y - sel.y) - Math.abs(q.y - sel.y);
    above.sort(rank(true)); below.sort(rank(false));
    const tops = new Map();
    let limit = selTop - GAP;
    above.forEach((it, k) => { const t = Math.max(Math.min(it.y - it.h / 2, limit - it.h), need(above.slice(k + 1))); tops.set(it, t); limit = t - GAP; });
    let lim = selTop + sel.h + GAP;
    const floor = Math.max(H, lim + need(below) - GAP);
    below.forEach((it, k) => { const t = Math.min(Math.max(it.y - it.h / 2, lim), floor - need(below.slice(k + 1)) - it.h); tops.set(it, t); lim = t + it.h + GAP; });
    const overflow = floor - H, cost = nb.reduce((c, it) => c + Math.abs(tops.get(it) + it.h / 2 - it.y), 0);
    if (!best || overflow < best.overflow - 1e-9 || (Math.abs(overflow - best.overflow) <= 1e-9 && cost < best.cost)) best = { overflow, cost, tops };
  }
  return best;
}
function stack(list, H) {
  // The selected panel has priority: centred on its station (clamped to the map) whenever some split of the neighbours fits.
  // Only if none fits does it shift, by the fewest pixels that let everything fit (or, if nothing can, as little overflow as possible).
  const sel = list[0], nb = list.slice(1);
  const ideal = Math.min(Math.max(sel.y - sel.h / 2, 0), Math.max(0, H - sel.h));
  let selTop = ideal, best = arrange(sel, nb, ideal, H);
  if (best.overflow > 1e-9) {
    for (let d = 1; d <= H; d++) {
      for (const t of [ideal - d, ideal + d]) {
        if (t < 0 || t > Math.max(0, H - sel.h)) continue;
        const b = arrange(sel, nb, t, H);
        if (b.overflow < best.overflow - 1e-9) { best = b; selTop = t; }
      }
      if (best.overflow <= 1e-9) break;
    }
  }
  sel.top = selTop;
  nb.forEach((it) => { it.top = best.tops.get(it); });
}
function fitPad(data) { return { top: PAD, bottom: PAD, left: PAD, right: data.gutter + PAD }; }   // stations fill the map's left part
function layout(s) {
  const { map, data } = s, W = s.mapEl.clientWidth, H = s.mapEl.clientHeight - 2 * EDGE, list = items(data), keep = new Set(list.map((i) => i.id));
  const X = W - data.gutter + EDGE;                    // panels' left edge, inside the map
  for (const [id, el] of s.panels) if (!keep.has(id)) { el.remove(); s.panels.delete(id); }
  for (const it of list) {
    let el = s.panels.get(it.id), fresh = !el;
    if (fresh) { el = document.createElement("div"); s.wrap.appendChild(el); s.panels.set(it.id, el); }
    el.className = "panel" + (it.role === "nb" ? " nb" : "");   // neighbours are compact so four panels fit the map's height
    el.style.left = X + "px"; el.style.setProperty("--c", it.color); el.innerHTML = panelHtml(it);
    const p = map.project([it.lon, it.lat]);
    Object.assign(it, { el, fresh, h: el.offsetHeight, x: p.x, y: p.y - EDGE });   // y in the panel band's coordinates
  }
  let bottom = s.mapEl.clientHeight, paths = "";
  if (list.length) {
    stack(list, H);
    const order = [...list].sort((a, b) => a.top - b.top);
    for (const it of list) {
      const bend = X - 22 - 8 * order.indexOf(it);   // staggered so displaced connectors don't merge before the panels
      const notch = Math.min(Math.max(it.y - it.top, 8), it.h - 8), top = it.top + EDGE;
      if (it.fresh) { it.el.style.transition = "none"; it.el.style.top = top + "px"; void it.el.offsetHeight; it.el.style.transition = ""; }
      else it.el.style.top = top + "px";
      it.el.style.setProperty("--notch", notch + "px");
      const r = it.role === "sel" ? 12 : 8, ny = top + notch, sy = it.y + EDGE;
      paths += `<path stroke="${it.color}" d="M ${(it.x + r).toFixed(1)} ${sy.toFixed(1)} H ${Math.max(bend, it.x + r)} L ${X - 9} ${ny.toFixed(1)}"/>`;
      bottom = Math.max(bottom, top + it.h);
    }
  }
  s.links.setAttribute("width", W); s.links.setAttribute("height", bottom); s.links.innerHTML = paths;
  s.wrap.style.minHeight = (bottom + 2) + "px";       // panels pushed past the map stay inside the component
}
function apply(s) {
  const { map, data, lib } = s;
  if (s.system !== data.system) { map.fitBounds(data.bounds, { padding: fitPad(data), duration: 0 }); s.system = data.system; }   // the only recentre
  const c = data.current;
  map.getSource("stations").setData(fc(data.stations, new Set(c ? (c.neighbours || []).map((n) => n.id) : [])));
  s.wrap.style.setProperty("--cur", data.colors.current);
  if (!c) { if (s.marker) { s.marker.remove(); s.marker = null; } }
  else {
    if (!s.marker) {
      const el = document.createElement("div"); el.className = "cur";
      el.innerHTML = '<div class="ring"></div><div class="ring r2"></div><div class="dot"></div>';
      s.marker = new lib.Marker({ element: el, anchor: "center" });
    }
    s.marker.setLngLat([c.lon, c.lat]).addTo(map);
  }
  layout(s);
}
export default function (component) {
  const { data, parentElement } = component;
  const wrap = parentElement.querySelector(".wrap"), mapEl = wrap.querySelector(".rsmap");
  mapEl.style.height = data.height + "px";
  mapEl.style.width = "100%%";
  wrap.style.setProperty("--gutter", data.gutter + "px");
  let s = parentElement.__rsmap;
  if (!s) {
    s = parentElement.__rsmap = { ready: false, system: null, wrap, mapEl, panels: new Map(), links: wrap.querySelector("svg.links") };
    loadLib().then((lib) => {
      s.lib = lib;
      const map = new lib.Map({ container: mapEl.querySelector(".canvas"), style: STYLE, bounds: s.data.bounds,
                                fitBoundsOptions: { padding: fitPad(s.data) }, interactive: false, attributionControl: { compact: true } });
      s.map = map; s.system = s.data.system;
      map.on("resize", () => s.ready && layout(s));
      map.on("load", () => {
        const col = s.data.colors;
        map.addSource("stations", { type: "geojson", data: fc([], new Set()) });
        map.addLayer({ id: "stations", type: "circle", source: "stations", paint: {
          "circle-radius": ["match", ["get", "role"], "nb", 6, "on", 5, 4],
          "circle-color": ["match", ["get", "role"], "nb", col.neighbour, "on", col.station, col.station_off],
          "circle-stroke-color": "#FBFAF6", "circle-stroke-width": ["match", ["get", "role"], "nb", 2, 1.5] } });
        mapEl.querySelector(".msg").remove();
        mapEl.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");   // start collapsed (the "i" opens it)
        s.ready = true; apply(s);
      });
    }).catch(() => { mapEl.querySelector(".msg").textContent = "Map library failed to load (offline?)"; });
  }
  s.data = data;
  if (s.ready) apply(s);
}
""" % (json.dumps(MAPLIBRE_JS), json.dumps(STYLE))

_HTML = (f'<link rel="stylesheet" href="{MAPLIBRE_CSS}"><div class="wrap"><div class="rsmap"><div class="canvas"></div>'
         '<div class="msg">Loading map…</div></div><svg class="links"></svg></div>')

_component = st.components.v2.component("rs_station_map", html=_HTML, css=_CSS, js=_JS)


def system_map(system: str, stations, current: dict | None, *, key: str, height: int = 380):
    """stations: iterable of dicts {id, name, lat, lon, on} for the whole system (on = in the Dev Panel selection).
    current: {id, name, lat, lon, spark: [values], spark_label: str, neighbours: [{id, name, lat, lon, km, spark, spark_label}]} or None.
    The view is fitted to all `stations`."""
    stations = list(stations)
    lons, lats = [p["lon"] for p in stations], [p["lat"] for p in stations]
    bounds = [[min(lons), min(lats)], [max(lons), max(lats)]] if stations else [[-180, -60], [180, 75]]
    return _component(key=key, data={"system": system, "bounds": bounds, "stations": stations, "current": current,
                                     "height": height, "gutter": GUTTER, "colors": {k: C[k] for k in ("station", "station_off", "current", "neighbour")}})
