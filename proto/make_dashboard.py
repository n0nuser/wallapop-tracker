"""Static dashboard prototype (approach B, chosen in ADR 0003).

Generates one `dashboard.html` from the SQLite DB using only the stdlib (`sqlite3` + a
string template). Open the file in a browser; every filter runs live in-page. No server,
no dependency.

Run:  python3 proto/make_dashboard.py   (reads `tracker.db`, writes `proto/dashboard.html`)

Targets and their specs are read from the DB, not hardcoded. This is the seed for the
real dashboard (T6); the bargain flag is a placeholder to swap for `tracker/score.py`.
"""
import json
import sqlite3
from pathlib import Path

DB = Path("tracker.db")
OUT = Path("proto/dashboard.html")

con = sqlite3.connect(DB)
rows = con.execute(
    """SELECT l.id, l.target, l.title, l.url, l.price_eur, l.first_seen, l.specs,
              (SELECT json_group_array(json_array(run_at, price_eur))
                 FROM (SELECT run_at, price_eur FROM observation
                       WHERE listing_id = l.id ORDER BY run_at)) AS hist
         FROM listing l WHERE l.status = 'active'
        ORDER BY l.target, l.price_eur"""
).fetchall()

today = max(r[5][:10] for r in rows)
targets = sorted({r[1] for r in rows})
listings = []
for id_, target, title, url, price, first_seen, specs, hist in rows:
    s = json.loads(specs or "{}")
    ps = [p for _, p in json.loads(hist or "[]") if p is not None]
    listings.append({
        "id": id_, "target": target, "title": title, "url": url, "price": price,
        "condition": s.get("condition"), "compatible": s.get("compatible"),
        "hist": hist and json.loads(hist) or [],
        "new_today": first_seen[:10] == today,
        "price_dropped": len(ps) > 1 and ps[-1] < max(ps[:-1]),
        "bargain": False,
    })

# Placeholder bargain rule (swap for tracker/score.py): cheapest quartile per target.
by_target = {}
for l in listings:
    by_target.setdefault(l["target"], []).append(l)
for t, ls in by_target.items():
    prices = sorted(l["price"] for l in ls if l["price"] is not None)
    cut = prices[len(prices) // 4] if prices else None
    for l in ls:
        l["bargain"] = cut is not None and l["price"] is not None and l["price"] <= cut

TARGET_OPTS = "".join(f"<option>{t}</option>" for t in targets)

HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wallapop dashboard</title><style>
 body{font:14px/1.4 system-ui,sans-serif;margin:0;padding:16px;background:#f6f6f4;color:#1a1a1a}
 header{display:flex;align-items:baseline;gap:12px}
 #filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:12px 0}
 #filters label{display:flex;flex-direction:column;font-size:12px;color:#555}
 input,select{font:inherit;padding:4px 6px;border:1px solid #ccc;border-radius:4px;background:#fff}
 table{border-collapse:collapse;width:100%;background:#fff}
 th,td{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;vertical-align:middle}
 th{font-size:12px;color:#555;position:sticky;top:0;background:#fff}
 td.num{text-align:right;font-variant-numeric:tabular-nums}
 .tag{font-size:11px;padding:1px 6px;border-radius:8px;background:#e8e8e4}
 .ok{background:#d6f5d6}.no{background:#fdd}.bargain{background:#fff0c2}
 a{color:#0b57d0;text-decoration:none}
 .muted{color:#999}
</style></head><body>
<header><h1>Wallapop dashboard</h1><span class="muted" id="count"></span></header>
<form id="filters" onsubmit="return false">
  <label>target<select id="target"><option value="">all</option>__TARGETS__</select></label>
  <label>text<input id="text" placeholder="title search"></label>
  <label>compat<select id="compat"><option value="">all</option><option value="ok">compatible</option><option value="no">not</option></select></label>
  <label>price &le;<input id="pmax" type="number" min="0" placeholder="max"></label>
  <label><span><input type="checkbox" id="bargain"> bargain only</span></label>
  <label><span><input type="checkbox" id="newtoday"> new today</span></label>
  <label><span><input type="checkbox" id="dropped"> price dropped</span></label>
</form>
<table><thead><tr><th>target</th><th>title</th><th class="num">price</th>
<th>condition</th><th>compat</th><th>history</th><th>seen</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
const DATA = __DATA__;
const $ = id => document.getElementById(id);
function state(){
  return {target:$("target").value, text:$("text").value.trim().toLowerCase(),
    compat:$("compat").value, pmax:$("pmax").value,
    bargain:$("bargain").checked, newtoday:$("newtoday").checked, dropped:$("dropped").checked};
}
function matches(l, f){
  if (f.target && l.target !== f.target) return false;
  if (f.text && !(l.title||"").toLowerCase().includes(f.text)) return false;
  if (f.compat==="ok" && l.compatible !== true) return false;
  if (f.compat==="no" && l.compatible !== false) return false;
  if (f.pmax!=="" && (l.price==null || l.price > +f.pmax)) return false;
  if (f.bargain && !l.bargain) return false;
  if (f.newtoday && !l.new_today) return false;
  if (f.dropped && !l.price_dropped) return false;
  return true;
}
function spark(hist){
  const ps = (hist||[]).map(h=>h[1]).filter(p=>p!=null);
  if (ps.length < 2) return '<span class="muted">&mdash;</span>';
  const w=64,h=20,p=2,min=Math.min(...ps),max=Math.max(...ps),r=(max-min)||1;
  const pt = (i,v)=>`${(i/(ps.length-1))*w},${h-p-((v-min)/r)*(h-2*p)}`;
  const pts = ps.map((v,i)=>pt(i,v)).join(" ");
  const last = ps[ps.length-1], down = last < ps[0], c = down ? "#1a7f1a" : "#c00";
  return `<svg width="${w}" height="${h}"><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.5"/>
    <circle cx="${w}" cy="${h-p-((last-min)/r)*(h-2*p)}" r="1.6" fill="${c}"/></svg>`;
}
const tag = (t,c)=>(t==null?'':`<span class="tag ${c||''}">${t}</span>`);
function row(l){
  return `<tr><td>${l.target}</td>
    <td><a href="${l.url}" target="_blank">${l.title}</a> ${tag(l.bargain?"bargain":"","bargain")}</td>
    <td class="num">${l.price==null?"":l.price}</td>
    <td>${tag(l.condition||"")}</td>
    <td>${l.compatible===true?tag("ok","ok"):l.compatible===false?tag("no","no"):tag("?","")}</td>
    <td>${spark(l.hist)}</td>
    <td class="muted">${l.new_today?"today":l.price_dropped?"dropped":"&middot;"}</td></tr>`;
}
function render(){
  const f = state(), list = DATA.filter(l=>matches(l,f));
  $("rows").innerHTML = list.map(row).join("");
  $("count").textContent = `${list.length} of ${DATA.length} listings`;
}
[...document.querySelectorAll("#filters input,#filters select")].forEach(el=>el.addEventListener("input",render));
render();
</script></body></html>"""

Path(OUT).write_text(HTML.replace("__DATA__", json.dumps(listings)).replace("__TARGETS__", TARGET_OPTS))
print(f"wrote {OUT} ({len(listings)} listings)")
