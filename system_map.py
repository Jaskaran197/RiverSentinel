"""
Main-dashboard system map: a fixed-view MapLibre map (loaded from a CDN) built with st.components.v2.

- Basemap: our own monochrome style over OpenFreeMap vector tiles (OpenMapTiles schema) with waterways emphasised,
  major roads and place names for orientation, and hillshade from the AWS Terrain Tiles (Terrarium DEM).
- Stations: dark-grey dots (in the Dev Panel selection), light-grey dots (rest of the system). The current station is a
  crimson pulsing marker; its panel (station id, name, sparkline of recent flow) is bolted onto the map's right edge in a
  reserved gutter, centred on the station's height, with a dashed leader from the station to the panel's notch.
- The view fits the system's stations once and never recentres when the current station changes; it refits only when
  the system changes. Pan/zoom are disabled.

The JS is re-invoked on every rerun with new data; the map instance lives on parentElement and is only updated.
"""
from __future__ import annotations

import json

import streamlit as st

MAPLIBRE_JS = "https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js"
MAPLIBRE_CSS = "https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.css"

# monochrome placeholder palette; the relationships (water darkest, roads lightest, current station loudest) should survive restyling
C = {"bg": "#efefed", "land": "#e6e6e3", "water": "#a9a9a5", "river": "#5f5f5b", "stream": "#8d8d89", "road": "#fbfbfa",
     "road_case": "#c4c4c0", "border": "#9a9a96", "label": "#3b3b39", "label_soft": "#6b6b67", "halo": "#f4f4f2",
     "station": "#3a3a3a", "station_off": "#b3b3af", "current": "#d7263d"}

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
         "paint": {"hillshade-shadow-color": "#555552", "hillshade-highlight-color": "#ffffff", "hillshade-accent-color": "#7d7d79",
                   "hillshade-exaggeration": 0.4, "hillshade-illumination-direction": 315}},
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

GUTTER = 240          # px reserved right of the map for the bolted-on station panel (always reserved, so the map never resizes)

_CSS = """
:host{display:block}
.wrap{position:relative}
.rsmap{position:relative;border:1px solid #d9d9d5;border-radius:6px 0 0 6px;overflow:hidden;background:#efefed}
.canvas{position:absolute;inset:0}
.msg{position:absolute;inset:0;display:grid;place-items:center;color:#8b8b86;font:13px system-ui,sans-serif}
.cur{position:relative;width:14px;height:14px}
.cur .dot{position:absolute;inset:0;border-radius:50%;background:var(--cur);box-shadow:0 0 0 2px #fff}
.cur .ring{position:absolute;inset:0;border-radius:50%;border:2px solid var(--cur);animation:pulse 1.8s ease-out infinite}
.cur .ring.r2{animation-delay:.9s}
@keyframes pulse{from{transform:scale(1);opacity:.9} to{transform:scale(3.6);opacity:0}}
/* leader: a dashed rail from the station to the map's right edge, at the station's height */
.leader{position:absolute;height:0;border-top:1px dashed var(--cur);opacity:.7;pointer-events:none;display:none;z-index:2;transition:top .35s ease}
/* panel: bolted onto the map's right edge, vertically centred on the station (clamped to the map), notch at the station's height */
.panel{position:absolute;left:0;top:0;display:none;background:#fff;border:1px solid #d0d0cc;border-left:3px solid var(--cur);
       border-radius:0 4px 4px 0;padding:6px 10px 7px;white-space:nowrap;font:12px/1.35 system-ui,sans-serif;color:#1d1d1b;
       box-shadow:2px 2px 8px rgba(0,0,0,.08);transition:top .35s ease;max-width:calc(var(--gutter) - 4px);overflow:hidden;text-overflow:ellipsis}
.panel::before{content:"";position:absolute;left:-9px;top:var(--notch,50%);transform:translateY(-50%);border:6px solid transparent;border-right-color:var(--cur);transition:top .35s ease}
.panel .id{font-weight:700;letter-spacing:.03em}
.panel .nm{color:#4a4a47;overflow:hidden;text-overflow:ellipsis}
.panel svg{display:block;margin-top:4px}
.panel .val{font-size:11px;color:#4a4a47;margin-top:1px}
.maplibregl-ctrl-attrib{font-size:10px}
"""

_JS = """
const JS_URL = %s, STYLE = %s, PAD = 44;
function loadLib() {                                   // one <script> for the whole page; MapLibre's UMD build sets window.maplibregl
  if (window.maplibregl) return Promise.resolve(window.maplibregl);
  if (!window.__rsMapLib) window.__rsMapLib = new Promise((ok, err) => {
    const sc = document.createElement("script"); sc.src = JS_URL; sc.onload = () => ok(window.maplibregl); sc.onerror = err;
    document.head.appendChild(sc);
  });
  return window.__rsMapLib;
}
function esc(t) { return String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }
function spark(vals, color) {                          // inline SVG polyline, 132 x 30
  if (!vals || vals.length < 2) return "";
  const w = 132, h = 30, lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - 2 - (v - lo) / span * (h - 4)).toFixed(1)}`).join(" ");
  const last = pts.split(" ").pop().split(",");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round"/>` +
         `<circle cx="${last[0]}" cy="${last[1]}" r="2.4" fill="${color}"/></svg>`;
}
function fc(stations) {
  return { type: "FeatureCollection", features: stations.map((p) => ({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
           properties: { id: p.id, on: p.on ? 1 : 0 } })) };
}
function place(s) {                                    // panel + leader follow the station's screen position
  const { map, data } = s, c = data.current, panel = s.panel, leader = s.leader;
  if (!c) { panel.style.display = leader.style.display = "none"; return; }
  const W = s.mapEl.clientWidth, H = s.mapEl.clientHeight, p = map.project([c.lon, c.lat]);
  panel.style.display = leader.style.display = "block";
  const h = panel.offsetHeight, top = Math.min(Math.max(p.y - h / 2, 0), H - h);
  panel.style.left = (W + 1) + "px";                   // flush against the map's right border
  panel.style.top = top + "px";
  panel.style.setProperty("--notch", Math.min(Math.max(p.y - top, 8), h - 8) + "px");
  leader.style.left = (p.x + 12) + "px"; leader.style.width = Math.max(0, W - p.x - 12) + "px"; leader.style.top = p.y + "px";
}
function apply(s) {
  const { map, data, lib } = s;
  if (s.system !== data.system) { map.fitBounds(data.bounds, { padding: PAD, duration: 0 }); s.system = data.system; }   // the only recentre
  map.getSource("stations").setData(fc(data.stations));
  const c = data.current;
  s.wrap.style.setProperty("--cur", data.colors.current);
  if (!c) { if (s.marker) { s.marker.remove(); s.marker = null; } place(s); return; }
  if (!s.marker) {
    const el = document.createElement("div"); el.className = "cur";
    el.innerHTML = '<div class="ring"></div><div class="ring r2"></div><div class="dot"></div>';
    s.marker = new lib.Marker({ element: el, anchor: "center" });
  }
  s.marker.setLngLat([c.lon, c.lat]).addTo(map);
  s.panel.innerHTML = `<div class="id">${esc(c.id)}</div><div class="nm" title="${esc(c.name)}">${esc(c.name)}</div>` +
    spark(c.spark, data.colors.current) + (c.spark_label ? `<div class="val">${esc(c.spark_label)}</div>` : "");
  place(s);
}
export default function (component) {
  const { data, parentElement } = component;
  const wrap = parentElement.querySelector(".wrap"), mapEl = wrap.querySelector(".rsmap");
  mapEl.style.height = data.height + "px";
  mapEl.style.width = `calc(100%% - ${data.gutter}px)`;
  wrap.style.setProperty("--gutter", data.gutter + "px");
  let s = parentElement.__rsmap;
  if (!s) {
    s = parentElement.__rsmap = { ready: false, system: null, wrap, mapEl, panel: wrap.querySelector(".panel"), leader: wrap.querySelector(".leader") };
    loadLib().then((lib) => {
      s.lib = lib;
      const map = new lib.Map({ container: mapEl.querySelector(".canvas"), style: STYLE, bounds: s.data.bounds,
                                fitBoundsOptions: { padding: PAD }, interactive: false, attributionControl: { compact: true } });
      s.map = map; s.system = s.data.system;
      map.on("resize", () => s.ready && place(s));
      map.on("load", () => {
        map.addSource("stations", { type: "geojson", data: fc([]) });
        map.addLayer({ id: "stations", type: "circle", source: "stations", paint: {
          "circle-radius": ["case", ["==", ["get", "on"], 1], 5, 4],
          "circle-color": ["case", ["==", ["get", "on"], 1], s.data.colors.station, s.data.colors.station_off],
          "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5 } });
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
         '<div class="msg">Loading map…</div><div class="leader"></div></div><div class="panel"></div></div>')

_component = st.components.v2.component("rs_station_map", html=_HTML, css=_CSS, js=_JS)


def system_map(system: str, stations, current: dict | None, *, key: str, height: int = 380):
    """stations: iterable of dicts {id, name, lat, lon, on} for the whole system (on = in the Dev Panel selection).
    current: {id, name, lat, lon, spark: [values], spark_label: str} or None. The view is fitted to all `stations`."""
    stations = list(stations)
    lons, lats = [p["lon"] for p in stations], [p["lat"] for p in stations]
    bounds = [[min(lons), min(lats)], [max(lons), max(lats)]] if stations else [[-180, -60], [180, 75]]
    return _component(key=key, data={"system": system, "bounds": bounds, "stations": stations, "current": current,
                                     "height": height, "gutter": GUTTER, "colors": {k: C[k] for k in ("station", "station_off", "current")}})
