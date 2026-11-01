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

from tracker import collector, score


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
    con.executescript(collector.SCHEMA)
    collector._migrate(con)
    listings = _rows(con)

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

    con = sqlite3.connect(db)
    specs_map = {r[0]: json.loads(r[1] or "{}") for r in con.execute("SELECT id, specs FROM listing")}
    con.close()
    for l in listings:
        m = metric_of.get(l["target"])
        l["metric"] = specs_map[l["id"]].get(m) if m else None

    html = _render(listings, targets_)
    Path(out).write_text(html)
    return len(listings)


def _render(listings, targets):
    data = json.dumps(listings, ensure_ascii=False)
    target_opts = "".join(f"<option>{t}</option>" for t in targets)
    return HTML.replace("__DATA__", data).replace("__TARGETS__", target_opts)


HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wallapop tracker</title><style>
*,*::before,*::after{box-sizing:border-box;min-width:0}
:root{interpolate-size:allow-keywords;scrollbar-gutter:stable;text-wrap:pretty;overflow-wrap:break-word;
  font-synthesis:none;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;
  -webkit-text-size-adjust:100%;-webkit-tap-highlight-color:transparent;color-scheme:light dark;
  --bg:light-dark(oklch(.985 0 none),oklch(.16 0 none));
  --surface:light-dark(oklch(1 0 none),oklch(.21 0 none));
  --fg:light-dark(oklch(.22 0 none),oklch(.92 0 none));
  --muted:light-dark(oklch(.52 0 none),oklch(.68 0 none));
  --border:light-dark(oklch(.9 0 none),oklch(.3 0 none));
  --bargain:light-dark(oklch(.92 .11 90),oklch(.38 .09 90));
  --ok:light-dark(oklch(.9 .12 150),oklch(.35 .09 150));
  --no:light-dark(oklch(.9 .12 25),oklch(.4 .1 25));
  --warn:light-dark(oklch(.9 .13 60),oklch(.4 .1 60));
  --link:light-dark(oklch(.52 .16 250),oklch(.72 .13 250));
  --ease-out:cubic-bezier(.23,1,.32,1);
  --ease-in-out:cubic-bezier(.77,0,.175,1);
}
body{margin:0;min-height:100svh;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;padding-inline:1rem;padding-block-end:3rem}
h1{font-size:1.1rem;margin:0;text-wrap:balance}
img,svg,video{display:block;max-width:100%;height:auto}
input,textarea,select{font-size:max(16px,1rem)}
:focus-visible{outline:max(2px,.08em) solid currentColor;outline-offset:.25em}
a{color:var(--link);text-decoration:none;transition:transform 160ms var(--ease-out)}
a:active{transform:scale(.97)}
header{display:flex;align-items:baseline;gap:.75rem;flex-wrap:wrap;padding-block:1rem .5rem}
.muted{color:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:.6rem 1.25rem;align-items:center;background:var(--surface);border:1px solid var(--border);border-radius:.6rem;padding:.6rem .8rem;margin-block:0 .75rem;font-size:.82rem;color:var(--muted)}
.legend b{color:var(--fg);font-weight:600}
.swatch{display:inline-block;inline-size:.8em;block-size:.8em;border-radius:.25em;vertical-align:-.06em}
.tag{font-size:.78rem;padding:1px 7px;border-radius:999px;background:var(--border);white-space:nowrap;font-weight:600}
.bargain{background:var(--bargain);color:light-dark(oklch(.32 .09 90),oklch(.95 .08 90))}
.ok{background:var(--ok);color:light-dark(oklch(.3 .09 150),oklch(.95 .08 150))}
.no{background:var(--no);color:light-dark(oklch(.35 .1 25),oklch(.95 .08 25))}
.warn{background:var(--warn);color:light-dark(oklch(.35 .09 60),oklch(.95 .08 60))}
#filters{display:flex;flex-wrap:wrap;gap:.6rem 1rem;align-items:flex-end;background:var(--surface);border:1px solid var(--border);padding:.7rem .9rem;border-radius:.6rem;margin-block-end:1rem}
#filters label{display:flex;flex-direction:column;font-size:.78rem;color:var(--muted);gap:.2rem}
#filters label span{flex-direction:row;align-items:center;gap:.35rem;font-size:.85rem;color:var(--fg)}
input,select{font:inherit;padding:.35rem .5rem;border:1px solid var(--border);border-radius:.4rem;background:var(--surface);color:var(--fg)}
input[type=number]{inline-size:5rem}
table{border-collapse:collapse;inline-size:100%;background:var(--surface)}
th,td{border-block-end:1px solid var(--border);padding:.45rem .55rem;text-align:start;vertical-align:middle}
th{font-size:.72rem;color:var(--muted);position:sticky;inset-block-start:0;background:var(--surface);white-space:nowrap}
th.sortable{cursor:pointer;user-select:none}
th.sortable::after{content:"↕";opacity:.25;margin-inline-start:.35em}
th.sortable.asc::after{content:"▲";opacity:1}
th.sortable.desc::after{content:"▼";opacity:1}
td.num{text-align:end;font-variant-numeric:tabular-nums}
.thumb{inline-size:64px;block-size:48px;aspect-ratio:4/3;object-fit:cover;border-radius:.4rem;background:var(--border);display:block}
.seller{font-size:.8rem}.seller .muted{color:var(--muted)}
#scatter{margin-block-end:1rem;background:var(--surface);border:1px solid var(--border);padding:.6rem}
tbody tr{transition:background-color 150ms var(--ease-out)}
@media (hover:hover) and (pointer:fine){
  a:hover{text-decoration:underline}
  tbody tr:hover{background:light-dark(oklch(.97 0 none),oklch(.24 0 none))}
}
@media (prefers-reduced-motion:reduce){
  a{transition:none}
  a:active{transform:none}
  tbody tr{transition:none}
}
</style></head><body>
<header><h1>Wallapop tracker</h1><span class="muted" id="count"></span></header>
<div class="legend"><b>Legend</b>
  <span><span class="tag bargain">bargain</span> under the target threshold</span>
  <span><span class="tag ok">ok</span> / <span class="tag no">no</span> compatible with the target's requirements</span>
  <span><span class="tag warn">⚠ seller</span> risky: new account, no/few reviews, unverified, low rating</span>
  <span><span class="swatch" style="background:light-dark(oklch(.5 .15 150),oklch(.6 .15 150))"></span> price ↓ / <span class="swatch" style="background:light-dark(oklch(.5 .15 25),oklch(.6 .15 25))"></span> price ↑</span>
  <span><span class="swatch" style="background:light-dark(oklch(.7 .15 85),oklch(.65 .14 85))"></span> bargain / <span class="swatch" style="background:light-dark(oklch(.55 .15 250),oklch(.6 .13 250))"></span> not, in the chart</span>
</div>
<form id="filters" onsubmit="return false">
  <label>target<select id="target"><option value="">all</option>__TARGETS__</select></label>
  <label>text<input id="text" placeholder="title / brand / model"></label>
  <label>compat<select id="compat"><option value="">all</option><option value="ok">compatible</option><option value="no">not</option></select></label>
  <label>price ≤<input id="pmax" type="number" min="0"></label>
  <label>€/unit ≤<input id="umax" type="number" min="0" step="0.1"></label>
  <label>min rating<input id="rmin" type="number" min="0" max="5" step="0.1" value="0"></label>
  <label><span><input type="checkbox" id="bargain"> bargain</span></label>
  <label><span><input type="checkbox" id="relevant" checked> relevant only</span></label>
  <label><span><input type="checkbox" id="newtoday"> new</span></label>
  <label><span><input type="checkbox" id="dropped"> dropped</span></label>
</form>
<div id="scatter"></div>
<table><thead><tr><th></th><th class="sortable" data-sort="title">title</th><th class="num sortable" data-sort="price">price</th><th class="sortable" data-sort="target">target</th>
<th class="num sortable" data-sort="metric">€/unit</th><th class="sortable" data-sort="condition">condition</th><th>compat</th><th class="sortable" data-sort="seller">seller</th><th>history</th><th>seen</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
const DATA = __DATA__;
const $ = id => document.getElementById(id);
const state = () => ({target:$("target").value, text:$("text").value.trim().toLowerCase(),
  compat:$("compat").value, pmax:$("pmax").value, umax:$("umax").value, rmin:$("rmin").value,
  bargain:$("bargain").checked, relevant:$("relevant").checked, newtoday:$("newtoday").checked, dropped:$("dropped").checked});
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
  if(f.relevant && l.relevant===false) return false;
  if(f.newtoday && !l.new_today) return false;
  if(f.dropped && !l.price_dropped) return false;
  return true;
}
const esc = s => String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function spark(hist){
  const ps=(hist||[]).map(x=>x[1]).filter(p=>p!=null);
  if(ps.length<2) return '<span class="muted">—</span>';
  const w=64,h=20,p=2,mn=Math.min(...ps),mx=Math.max(...ps),r=(mx-mn)||1;
  const pt=(i,v)=>`${(i/(ps.length-1))*w},${h-p-((v-mn)/r)*(h-2*p)}`;
  const pts=ps.map((v,i)=>pt(i,v)).join(" ");
  const last=ps[ps.length-1], c=last<ps[0]?"#1a9a4a":"#d13737";
  return `<svg width="${w}" height="${h}"><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.5"/><circle cx="${w}" cy="${h-p-((last-mn)/r)*(h-2*p)}" r="1.6" fill="${c}"/></svg>`;
}
function seller(l){
  const s=l.seller;
  if(!s) return '<span class="muted">—</span>';
  const bad = (s.reviews==null||s.reviews<3) || (s.rating==null||s.rating<4) || !s.verified || (s.age!=null&&s.age<1);
  const stars = s.rating==null ? "?" : s.rating.toFixed(1);
  return `<span class="seller">${bad?'<span class="tag warn">⚠</span> ':''}${esc(s.name)} <span class="muted">${stars}★ · ${s.reviews??0}rv${s.verified?' · ✓':''}${s.age!=null?' · '+s.age+'y':''}</span></span>`;
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
  const dots=pts.map(p=>{const c=p.bargain?"#e8a400":"#4a7fd0";
    return `<circle cx="${X(p.price)}" cy="${Y(p.metric)}" r="5" fill="${c}"><title>${esc(p.title)} — €${p.price}, ${p.metric.toFixed(1)}/u</title></circle>`;}).join("");
  $("scatter").innerHTML=`<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
    <line x1="${pl}" y1="${H-pb}" x2="${W-pr}" y2="${H-pb}" stroke="#888"/>
    <line x1="${pl}" y1="${pt}" x2="${pl}" y2="${H-pb}" stroke="#888"/>
    <text x="${pl}" y="${H-8}" font-size="11">€0</text>
    <text x="${W-pr-30}" y="${H-8}" font-size="11">€${xmax.toFixed(0)}</text>
    <text x="2" y="${pt+4}" font-size="11">€${ymax.toFixed(0)}/u</text>${dots}</svg>`;
}
function render(){
  const f=state();
  let list=DATA.filter(l=>matches(l,f));
  if(sortKey){
    const k=sortKey;
    list=[...list].sort((a,b)=>{const va=val(a,k),vb=val(b,k);return va===vb?0:(va<vb?-1:1)*sortDir;});
  }
  $("rows").innerHTML=list.length?list.map(row).join(""):'<tr><td colspan="10" class="muted">no listings match</td></tr>';
  $("count").textContent=`${list.length} of ${DATA.length} listings`;
  scatter(list);
  document.querySelectorAll("th[data-sort]").forEach(th=>{
    th.classList.toggle("asc", th.dataset.sort===sortKey && sortDir===1);
    th.classList.toggle("desc", th.dataset.sort===sortKey && sortDir===-1);
  });
}
let sortKey=null, sortDir=1;
function val(l,k){
  if(k==="price") return l.price ?? -Infinity;
  if(k==="metric") return l.metric ?? -Infinity;
  if(k==="seller") return l.seller ? (l.seller.rating ?? -Infinity) : -Infinity;
  if(k==="title") return (l.title||"").toLowerCase();
  if(k==="target") return l.target||"";
  if(k==="condition") return l.condition||"";
  return "";
}
document.querySelectorAll("th[data-sort]").forEach(th=>th.addEventListener("click",()=>{
  const k=th.dataset.sort;
  if(sortKey===k) sortDir=-sortDir; else {sortKey=k; sortDir=1;}
  render();
}));
[...document.querySelectorAll("#filters input,#filters select")].forEach(el=>el.addEventListener("input",render));
render();
</script></body></html>"""
