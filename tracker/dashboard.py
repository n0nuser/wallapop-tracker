"""Static dashboard: generate one self-contained dashboard.html from the SQLite DB.

Reads active listings, specs, price history, seller reputation and images, scores
bargains with tracker.score, and writes a single HTML file (filters run live in-page,
no server). Image thumbnails use the local cache (images/<id>.jpg) when present and
fall back to the listing's first image URL.
"""
import datetime as dt
import json
import sqlite3
import tomllib
from pathlib import Path

from tracker import score


def _rows(con):
    """All active listings joined with their seller, in the shape the dashboard renders."""
    today = (con.execute("SELECT max(substr(first_seen,1,10)) FROM listing").fetchone()[0] or "")
    q = """SELECT l.id, l.target, l.title, l.url, l.price_eur, l.first_seen, l.specs, l.images,
                  s.name AS s_name, s.rating_average, s.reviews, s.verified, s.registered_at,
                  (SELECT json_group_array(json_array(run_at, price_eur))
                     FROM (SELECT run_at, price_eur FROM observation
                           WHERE listing_id = l.id ORDER BY run_at)) AS hist
             FROM listing l LEFT JOIN seller s ON s.id = l.seller_id
            WHERE l.status = 'active' ORDER BY l.price_eur"""
    out = []
    for r in con.execute(q):
        sp = json.loads(r["specs"] or "{}")
        ps = [p for _, p in json.loads(r["hist"] or "[]") if p is not None]
        out.append({
            "id": r["id"], "target": r["target"], "title": r["title"], "url": r["url"],
            "price": r["price_eur"], "first_seen": r["first_seen"] or "",
            "condition": sp.get("condition"), "compatible": sp.get("compatible"),
            "relevant": sp.get("relevant"), "model": sp.get("model"), "brand": sp.get("brand"),
            "metric": None, "metric_name": None,
            "images": json.loads(r["images"]) if r["images"] else [],
            "seller": None,
            "hist": r["hist"] and json.loads(r["hist"]) or [],
            "new_today": (r["first_seen"] or "")[:10] == today,
            "price_dropped": len(ps) > 1 and ps[-1] < max(ps[:-1]),
            "bargain": False, "lowball": None,
        })
        if r["s_name"]:
            age = None
            if r["registered_at"]:
                try:
                    age = round((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(r["registered_at"])).days / 365.25, 1)
                except ValueError:
                    age = None
            out[-1]["seller"] = {
                "name": r["s_name"], "rating": r["rating_average"], "reviews": r["reviews"],
                "verified": bool(r["verified"]), "age": age,
            }
    return out


def generate(db="tracker.db", targets="targets.toml", out="dashboard.html", images_dir="images"):
    cfg = tomllib.loads(Path(targets).read_text())
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript("CREATE TABLE IF NOT EXISTS listing (id TEXT PRIMARY KEY, target TEXT, title TEXT, "
                      "description TEXT, url TEXT, first_seen TEXT, last_seen TEXT, created_at INTEGER, "
                      "price_eur REAL, status TEXT, seller_id TEXT, shipping INTEGER, location TEXT, "
                      "distance_km REAL, specs TEXT, images TEXT, last_checked TEXT);")
    listings = _rows(con)

    # bargain + metric per target
    metric_of, bargain_ids, lowball = {}, set(), {}
    for name, tcfg in cfg.get("targets", {}).items():
        rows = score.active_listings(con, name)
        sc = score.score_target(tcfg, rows)
        for l in sc["listings"]:
            if l["is_bargain"]:
                bargain_ids.add(l["id"])
                lowball[l["id"]] = l["lowball_offer"]
        b = tcfg.get("bargain", {})
        st = b.get("stat")
        metric_of[name] = st if st and st != "p25" else None
    con.close()

    targets_ = sorted({l["target"] for l in listings})
    imgdir = Path(images_dir)
    for l in listings:
        l["metric_name"] = metric_of.get(l["target"])
        l["bargain"] = l["id"] in bargain_ids
        l["lowball"] = lowball.get(l["id"])
        local = imgdir / f"{l['id']}.jpg"
        l["thumb"] = str(local) if local.exists() else (l["images"][0] if l["images"] else None)

    # metric values (a target's derived field, e.g. eur_per_tb) from specs
    con = sqlite3.connect(db)
    specs_map = {r[0]: json.loads(r[1] or "{}") for r in con.execute("SELECT id, specs FROM listing")}
    con.close()
    for l in listings:
        m = metric_of.get(l["target"])
        l["metric"] = specs_map[l["id"]].get(m) if m else None

    html = _render(listings, targets_, metric_of)
    Path(out).write_text(html)
    return len(listings)


def _render(listings, targets, metric_of):
    data = json.dumps(listings, ensure_ascii=False)
    target_opts = "".join(f"<option>{t}</option>" for t in targets)
    return HTML.replace("__DATA__", data).replace("__TARGETS__", target_opts)


HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wallapop tracker</title><style>
 body{font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px;background:#f6f6f4;color:#1a1a1a}
 header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
 h1{font-size:18px;margin:0}
 #filters{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;margin:14px 0;background:#fff;border:1px solid #eee;padding:10px;border-radius:8px}
 #filters label{display:flex;flex-direction:column;font-size:11px;color:#555;gap:2px}
 input,select{font:inherit;padding:5px 7px;border:1px solid #ccc;border-radius:5px;background:#fff}
 input[type=number]{width:70px}
 table{border-collapse:collapse;width:100%;background:#fff}
 th,td{border-bottom:1px solid #eee;padding:7px 9px;text-align:left;vertical-align:middle}
 th{font-size:11px;color:#555;position:sticky;top:0;background:#fff;white-space:nowrap}
 td.num{text-align:right;font-variant-numeric:tabular-nums}
 .thumb{width:64px;height:48px;object-fit:cover;border-radius:5px;background:#eee;display:block}
 .tag{font-size:11px;padding:1px 7px;border-radius:8px;background:#e8e8e4;white-space:nowrap}
 .bargain{background:#fff0c2}.ok{background:#d6f5d6}.no{background:#fdd}.warn{background:#ffe0c2}
 a{color:#0b57d0;text-decoration:none}
 .seller{font-size:12px;color:#333}.seller .muted{color:#999}
 .warn{font-weight:600}
 .muted{color:#999}
 #scatter{margin:12px 0;background:#fff;border:1px solid #eee;padding:8px}
</style></head><body>
<header><h1>Wallapop tracker</h1><span class="muted" id="count"></span>
<span class="muted">· image cache / data from the last run</span></header>
<form id="filters" onsubmit="return false">
  <label>target<select id="target"><option value="">all</option>__TARGETS__</select></label>
  <label>text<input id="text" placeholder="title / brand / model"></label>
  <label>compat<select id="compat"><option value="">all</option><option value="ok">compatible</option><option value="no">not</option></select></label>
  <label>price ≤<input id="pmax" type="number" min="0"></label>
  <label>€/unit ≤<input id="umax" type="number" min="0" step="0.1"></label>
  <label>min rating<input id="rmin" type="number" min="0" max="5" step="0.1" value="0"></label>
  <label><span><input type="checkbox" id="bargain"> bargain</span></label>
  <label><span><input type="checkbox" id="newtoday"> new</span></label>
  <label><span><input type="checkbox" id="dropped"> dropped</span></label>
</form>
<div id="scatter"></div>
<table><thead><tr><th></th><th>title</th><th class="num">price</th><th>target</th>
<th class="num">€/unit</th><th>condition</th><th>compat</th><th>seller</th><th>history</th><th>seen</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
const DATA = __DATA__;
const $ = id => document.getElementById(id);
const state = () => ({target:$("target").value, text:$("text").value.trim().toLowerCase(),
  compat:$("compat").value, pmax:$("pmax").value, umax:$("umax").value, rmin:$("rmin").value,
  bargain:$("bargain").checked, newtoday:$("newtoday").checked, dropped:$("dropped").checked});
function matches(l,f){
  if(f.target && l.target!==f.target) return false;
  const t=(l.title+" "+(l.brand||"")+" "+(l.model||"")).toLowerCase();
  if(f.text && !t.includes(f.text)) return false;
  if(f.compat==="ok" && l.compatible!==true) return false;
  if(f.compat==="no" && l.compatible!==false) return false;
  if(f.pmax!=="" && (l.price==null || l.price>+f.pmax)) return false;
  if(f.umax!=="" && (l.metric==null || l.metric>+f.umax)) return false;
  if(f.rmin!=="" && (l.seller==null || l.seller.rating==null || l.seller.rating<+f.rmin)) return false;
  if(f.bargain && !l.bargain) return false;
  if(f.newtoday && !l.new_today) return false;
  if(f.dropped && !l.price_dropped) return false;
  return true;
}
const esc = s => String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function spark(h){
  const ps=(h||[]).map(x=>x[1]).filter(p=>p!=null);
  if(ps.length<2) return '<span class="muted">—</span>';
  const w=64,h=20,p=2,mn=Math.min(...ps),mx=Math.max(...ps),r=(mx-mn)||1;
  const pt=(i,v)=>`${(i/(ps.length-1))*w},${h-p-((v-mn)/r)*(h-2*p)}`;
  const pts=ps.map((v,i)=>pt(i,v)).join(" ");
  const last=ps[ps.length-1], c=last<ps[0]?"#1a7f1a":"#c00";
  return `<svg width="${w}" height="${h}"><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.5"/><circle cx="${w}" cy="${h-p-((last-mn)/r)*(h-2*p)}" r="1.6" fill="${c}"/></svg>`;
}
function seller(l){
  const s=l.seller;
  if(!s) return '<span class="muted">—</span>';
  const bad = (s.reviews==null||s.reviews<3) || (s.rating==null||s.rating<4) || !s.verified || (s.age!=null&&s.age<1);
  const stars = s.rating==null ? "?" : s.rating.toFixed(1);
  return `<span class="seller ${bad?'warn':''}">${bad?'⚠ ':''}${esc(s.name)} <span class="muted">${stars}★ · ${s.reviews??0}rv${s.verified?' · ✓':''}${s.age!=null?' · '+s.age+'y':''}</span></span>`;
}
const tag=(t,c)=>t==null?'':`<span class="tag ${c||''}">${esc(t)}</span>`;
function row(l){
  const img = l.thumb ? `<img class="thumb" src="${esc(l.thumb)}" loading="lazy">` : '<span class="thumb"></span>';
  const metric = l.metric==null ? '' : l.metric.toFixed(1);
  return `<tr>
    <td>${img}</td>
    <td><a href="${esc(l.url)}" target="_blank">${esc(l.title)}</a> ${tag(l.bargain?"bargain":"","bargain")}</td>
    <td class="num">${l.price==null?"":l.price}</td>
    <td>${esc(l.target)}</td>
    <td class="num">${metric}</td>
    <td>${tag(l.condition||"")}</td>
    <td>${l.compatible===true?tag("ok","ok"):l.compatible===false?tag("no","no"):tag("?","")}</td>
    <td>${seller(l)}</td>
    <td>${spark(l.hist)}</td>
    <td class="muted">${l.new_today?"new":l.price_dropped?"dropped":"·"}</td></tr>`;
}
function scatter(list){
  const pts=list.filter(l=>l.metric!=null&&l.price!=null);
  const W=640,H=260,pl=40,pr=10,pt=12,pb=26;
  if(!pts.length){$("scatter").innerHTML="";return;}
  const xmax=Math.max(...pts.map(p=>p.price))*1.05||1, ymax=Math.max(...pts.map(p=>p.metric))*1.1||1;
  const X=p=>pl+(p/xmax)*(W-pl-pr), Y=p=>H-pb-(p/ymax)*(H-pt-pb);
  const dots=pts.map(p=>{const c=p.bargain?"#f0a500":"#4a7fd0";
    return `<circle cx="${X(p.price)}" cy="${Y(p.metric)}" r="5" fill="${c}"><title>${esc(p.title)} — €${p.price}, ${p.metric.toFixed(1)}/u</title></circle>`;}).join("");
  $("scatter").innerHTML=`<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
    <line x1="${pl}" y1="${H-pb}" x2="${W-pr}" y2="${H-pb}" stroke="#ccc"/>
    <line x1="${pl}" y1="${pt}" x2="${pl}" y2="${H-pb}" stroke="#ccc"/>
    <text x="${pl}" y="${H-8}" font-size="11">€0</text>
    <text x="${W-pr-30}" y="${H-8}" font-size="11">€${xmax.toFixed(0)}</text>
    <text x="2" y="${pt+4}" font-size="11">€${ymax.toFixed(0)}/u</text>${dots}</svg>`;
}
function render(){
  const f=state(), list=DATA.filter(l=>matches(l,f));
  $("rows").innerHTML=list.map(row).join("");
  $("count").textContent=`${list.length} of ${DATA.length} listings`;
  scatter(list);
}
[...document.querySelectorAll("#filters input,#filters select")].forEach(el=>el.addEventListener("input",render));
render();
</script></body></html>"""
