"""Property tests for the station-panel stacking in system_map.py's JS (run under Node; skipped if Node is missing).
Randomised layouts must never overlap, keep the selected panel where it wants to be, and keep panels on the map when they fit."""
import json, re, shutil, subprocess

import pytest

import system_map

NODE = shutil.which("node")

HARNESS = r"""
const GAP = %d;
%s
let seed = 7; const rnd = () => (seed = (seed * 16807) %% 2147483647) / 2147483647;
const fails = []; let cases = 0;
for (let n = 0; n < 4000; n++) {
  const H = 200 + Math.floor(rnd() * 400), k = Math.floor(rnd() * 4);              // 0-3 neighbours
  const list = [{ id: "sel", y: rnd() * H, h: 80 + rnd() * 30 }];
  for (let i = 0; i < k; i++) list.push({ id: "n" + i, y: rnd() * H, h: 60 + rnd() * 30 });
  stack(list, H); cases++;
  const sel = list[0], idealTop = Math.min(Math.max(sel.y - sel.h / 2, 0), Math.max(0, H - sel.h));
  const err = (m) => fails.push({ m, H, list: list.map(({ id, y, h, top }) => ({ id, y, h, top })) });
  const byTop = [...list].sort((a, b) => a.top - b.top);
  for (let i = 1; i < byTop.length; i++) if (byTop[i].top < byTop[i - 1].top + byTop[i - 1].h + GAP - 1e-6) err("overlap");
  if (list.some((i) => i.top < -1e-6)) err("panel above the map");
  // does some split of the neighbours fit with the selected panel at its ideal spot?
  const hs = list.slice(1).map((i) => i.h + GAP), roomA = idealTop, roomB = H - idealTop - sel.h;
  let fitsAtIdeal = false;
  for (let mask = 0; mask < 1 << hs.length; mask++) {
    let a = 0, b = 0; hs.forEach((h, j) => (mask >> j) & 1 ? (a += h) : (b += h));
    if (a <= roomA + 1e-6 && b <= roomB + 1e-6) fitsAtIdeal = true;
  }
  if (fitsAtIdeal && Math.abs(sel.top - idealTop) > 1e-6) err("selected moved although the neighbours fit around its ideal spot");
  const total = list.reduce((t, i) => t + i.h, 0) + GAP * (list.length - 1);
  if (total <= H - 1 && list.some((i) => i.top + i.h > H + 1e-6)) err("panel off the map although the whole stack fits");
  // neighbours left on their natural side keep their vertical order (closer to the selected = nearer to it)
  for (const side of ["above", "below"]) {
    const nat = list.slice(1).filter((i) => side === "above" ? i.y < sel.y && i.top < sel.top : i.y >= sel.y && i.top > sel.top);
    const s1 = [...nat].sort((a, b) => a.y - b.y).map((i) => i.id).join(), s2 = [...nat].sort((a, b) => a.top - b.top).map((i) => i.id).join();
    if (s1 !== s2) err("order on the " + side + " side doesn't follow the stations' heights");
  }
}
console.log(JSON.stringify({ cases, fails: fails.slice(0, 3), nfails: fails.length }));
"""


def _stack_source() -> str:
    """arrange() + stack(): the pure layout functions, lifted out of the component's JS."""
    m = re.search(r"function arrange\(.*?\nfunction stack\(list, H\) \{.*?\n\}\n", system_map._JS, re.S)
    assert m, "arrange()/stack() not found in system_map._JS"
    return m.group(0)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_stack_properties():
    gap = int(re.search(r"GAP = (\d+)", system_map._JS).group(1))
    out = subprocess.run([NODE, "-e", HARNESS % (gap, _stack_source())], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert res["cases"] == 4000
    assert res["nfails"] == 0, json.dumps(res["fails"], indent=1)
