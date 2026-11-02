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
  font-size:17px;
  --bg:light-dark(oklch(.985 0 none),oklch(.15 0 none));
  --surface:light-dark(oklch(1 0 none),oklch(.2 0 none));
  --surface-2:light-dark(oklch(.975 0 none),oklch(.185 0 none));
  --fg:light-dark(oklch(.22 0 none),oklch(.94 0 none));
  --muted:light-dark(oklch(.5 0 none),oklch(.72 0 none));
  --border:light-dark(oklch(.9 0 none),oklch(.32 0 none));
  --bargain:light-dark(oklch(.92 .11 90),oklch(.38 .09 90));
  --ok:light-dark(oklch(.9 .12 150),oklch(.35 .09 150));
  --no:light-dark(oklch(.9 .12 25),oklch(.4 .1 25));
  --warn:light-dark(oklch(.9 .13 60),oklch(.4 .1 60));
  --link:light-dark(oklch(.52 .16 250),oklch(.74 .13 250));
  --ease-out:cubic-bezier(.23,1,.32,1);
  --ease-in-out:cubic-bezier(.77,0,.175,1);
}
[data-theme="light"]{color-scheme:light}
[data-theme="dark"]{color-scheme:dark}
.hidden{display:none}
body{margin:0;min-height:100svh;background:var(--bg);color:var(--fg);
  font:100%/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  text-rendering:optimizeLegibility}
h1{font-size:1.2rem;font-weight:700;letter-spacing:-.01em;line-height:1.2;margin:0}
img,svg,video{display:block;max-width:100%;height:auto}
input,textarea,select{font-size:max(16px,1rem);font-family:inherit}
:focus-visible{outline:max(2px,.08em) solid currentColor;outline-offset:.25em}
a{color:var(--link);text-decoration:none;transition:transform 160ms var(--ease-out)}
a:active{transform:scale(.97)}
.muted{color:var(--muted)}
#chrome{position:sticky;top:0;z-index:20;padding-inline:1rem;padding-block:.65rem .55rem;
  background:light-dark(oklch(.985 0 none/.82),oklch(.15 0 none/.82));
  backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
  border-block-end:1px solid var(--border)}
header{display:flex;align-items:center;gap:.75rem;flex-wrap:wrap;margin-block-end:.4rem}
.grow{flex:1}
.btn{font:inherit;font-size:.88rem;padding:.28rem .6rem;border:1px solid var(--border);border-radius:.45rem;background:var(--surface);color:var(--fg);cursor:pointer;transition:transform 160ms var(--ease-out)}
.btn:active{transform:scale(.97)}
.cols{position:relative}
.cols>summary{cursor:pointer;user-select:none;font-size:.88rem;font-weight:600;color:var(--muted);list-style:none}
.cols>summary::-webkit-details-marker{display:none}
.cols-pop{position:absolute;inset-block-start:calc(100% + .4rem);inset-inline-end:0;background:var(--surface);border:1px solid var(--border);border-radius:.5rem;padding:.5rem .6rem;display:flex;flex-direction:column;gap:.3rem;box-shadow:0 6px 20px rgb(0 0 0/.16);z-index:40;min-inline-size:9.5rem}
.cols-pop label{display:flex;align-items:center;gap:.45rem;font-size:.9rem;cursor:pointer;white-space:nowrap}
.legend{border:1px solid var(--border);border-radius:.6rem;background:var(--surface);padding:.5rem .7rem;margin-block-end:.55rem;font-size:.88rem;color:var(--muted)}
.legend summary{cursor:pointer;font-weight:600;color:var(--fg);user-select:none}
.legend-items{display:flex;flex-wrap:wrap;gap:.5rem 1.25rem;align-items:center;padding-block-start:.45rem}
.swatch{display:inline-block;inline-size:.8em;block-size:.8em;border-radius:.25em;vertical-align:-.06em}
.tag{font-size:.82rem;padding:2px 8px;border-radius:999px;background:var(--border);white-space:nowrap;font-weight:600}
.bargain{background:var(--bargain);color:light-dark(oklch(.3 .09 90),oklch(.96 .08 90))}
.ok{background:var(--ok);color:light-dark(oklch(.28 .09 150),oklch(.96 .08 150))}
.no{background:var(--no);color:light-dark(oklch(.32 .1 25),oklch(.96 .08 25))}
.warn{background:var(--warn);color:light-dark(oklch(.32 .09 60),oklch(.96 .08 60))}
.warn-tip{position:relative;display:inline-block;cursor:help}
.warn-tip::after{content:attr(data-tip);position:absolute;inset-block-end:125%;inset-inline-start:0;background:var(--fg);color:var(--bg);padding:.35rem .55rem;border-radius:.45rem;font-size:.8rem;font-weight:500;line-height:1.35;white-space:nowrap;opacity:0;pointer-events:none;transform:translateY(4px);transition:opacity 150ms var(--ease-out),transform 150ms var(--ease-out);z-index:30;box-shadow:0 2px 10px rgb(0 0 0/.18)}
.warn-tip:hover::after,.warn-tip:focus-visible::after{opacity:1;transform:translateY(0)}
#filters{display:flex;flex-wrap:wrap;gap:.5rem .9rem;align-items:flex-end}
#filters label{display:flex;flex-direction:column;font-size:.78rem;color:var(--muted);gap:.2rem}
#filters label span{flex-direction:row;align-items:center;gap:.35rem;font-size:.9rem;color:var(--fg)}
input,select{font:inherit;padding:.35rem .55rem;border:1px solid var(--border);border-radius:.45rem;background:var(--surface);color:var(--fg)}
input:focus,select:focus{border-color:var(--link)}
input[type=number]{inline-size:5.2rem}
input[type=checkbox]{inline-size:1em;block-size:1em}
.chart{margin-inline:1rem;margin-block:.8rem;background:var(--surface);border:1px solid var(--border);border-radius:.6rem;padding:.55rem .7rem}
.chart>summary{cursor:pointer;user-select:none;font-size:.88rem;font-weight:600;color:var(--muted)}
.chart[open]>summary{margin-block-end:.45rem}
#scatter svg{inline-size:100%;block-size:auto}
#scatter svg text{fill:var(--muted);font-size:11px}
#scatter svg line{stroke:var(--border)}
#scatter svg line.axis{stroke:var(--muted)}
table{border-collapse:collapse;inline-size:100%;background:var(--surface)}
th,td{border-block-end:1px solid var(--border);padding:.5rem .6rem;text-align:start;vertical-align:middle}
th{font-size:.72rem;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.05em;position:sticky;top:var(--chrome-h,0);background:var(--surface);white-space:nowrap;z-index:10}
th.sortable{cursor:pointer;user-select:none}
th.sortable::after{content:"↕";opacity:.25;margin-inline-start:.35em}
th.sortable.asc::after{content:"▲";opacity:1}
th.sortable.desc::after{content:"▼";opacity:1}
tbody tr:nth-child(even){background:var(--surface-2)}
tbody tr{transition:background-color 150ms var(--ease-out)}
tbody tr.bargain,tbody tr.bargain:nth-child(even){background:color-mix(in oklch,var(--bargain) 35%,transparent)}
td.num{text-align:end;font-variant-numeric:tabular-nums}
td[data-col=thumb]{inline-size:100px;padding-inline-end:0}
.thumb{inline-size:88px;min-inline-size:88px;block-size:66px;aspect-ratio:4/3;object-fit:cover;border-radius:.45rem;background:var(--border);display:block}
.seller{font-size:.9rem}.seller .muted{color:var(--muted)}
.empty{text-align:center;padding:3rem 1rem;color:var(--muted)}
.empty b{display:block;font-size:1.1rem;color:var(--fg);margin-block-end:.3rem}
@media (hover:hover) and (pointer:fine){
  a:hover{text-decoration:underline}
  tbody tr:hover{background:light-dark(oklch(.965 0 none),oklch(.25 0 none))}
}
@media (max-width:600px){[data-col=target],[data-col=condition],[data-col=compat],[data-col=history],[data-col=seen]{display:none}.thumb{inline-size:64px;min-inline-size:64px;block-size:48px}td[data-col=thumb]{inline-size:72px}}
@media (prefers-reduced-motion:reduce){
  a{transition:none}a:active{transform:none}tbody tr{transition:none}.warn-tip::after{transition:none}
}
@media (prefers-reduced-transparency:reduce){
  #chrome{background:var(--surface);backdrop-filter:none;-webkit-backdrop-filter:none}
}
</style></head><body>
<div id="chrome">
  <header>
    <h1>Wallapop tracker</h1>
    <span class="muted" id="count"></span>
    <span class="grow"></span>
    <details class="cols" id="cols">
      <summary>Columns</summary>
      <div class="cols-pop">
        <label><input type="checkbox" data-col="thumb" checked> image</label>
        <label><input type="checkbox" data-col="price" checked> price</label>
        <label><input type="checkbox" data-col="target" checked> target</label>
        <label><input type="checkbox" data-col="metric" checked> €/unit</label>
        <label><input type="checkbox" data-col="condition" checked> condition</label>
        <label><input type="checkbox" data-col="compat" checked> compat</label>
        <label><input type="checkbox" data-col="seller" checked> seller</label>
        <label><input type="checkbox" data-col="history" checked> history</label>
        <label><input type="checkbox" data-col="seen" checked> seen</label>
      </div>
    </details>
    <button id="theme" class="btn" type="button" title="Cycle theme: auto / light / dark">Auto</button>
  </header>
  <details class="legend">
    <summary>Legend</summary>
    <div class="legend-items">
      <span><span class="tag bargain">bargain</span> under the target threshold</span>
      <span><span class="tag ok">ok</span> / <span class="tag no">no</span> compatible with the target's requirements</span>
      <span><span class="tag warn">⚠ seller</span> risky: new account, no/few reviews, unverified, low rating</span>
      <span><span class="swatch" style="background:light-dark(oklch(.5 .15 150),oklch(.6 .15 150))"></span> price ↓ / <span class="swatch" style="background:light-dark(oklch(.5 .15 25),oklch(.6 .15 25))"></span> price ↑</span>
      <span><span class="swatch" style="background:light-dark(oklch(.7 .15 85),oklch(.65 .14 85))"></span> bargain / <span class="swatch" style="background:light-dark(oklch(.55 .15 250),oklch(.6 .13 250))"></span> not, in the chart</span>
    </div>
  </details>
  <form id="filters" onsubmit="return false">
    <label>target<select id="target"><option value="">all</option>__TARGETS__</select></label>
    <label>text<input id="text" placeholder="title / brand / model"></label>
    <label>compat<select id="compat"><option value="">all</option><option value="ok">compatible</option><option value="no">not</option></select></label>
    <label>price ≥<input id="pmin" type="number" min="0"></label>
    <label>price ≤<input id="pmax" type="number" min="0"></label>
    <label>€/unit ≤<input id="umax" type="number" min="0" step="0.1"></label>
    <label>min rating<input id="rmin" type="number" min="0" max="5" step="0.1" value="0"></label>
    <label><span><input type="checkbox" id="bargain"> bargain</span></label>
    <label><span><input type="checkbox" id="relevant" checked> relevant only</span></label>
    <label><span><input type="checkbox" id="newtoday"> new</span></label>
    <label><span><input type="checkbox" id="dropped"> dropped</span></label>
  </form>
</div>
<details class="chart" id="chart">
  <summary>Chart — price vs €/unit</summary>
  <div id="scatter"></div>
</details>
<table><thead><tr>
  <th data-col="thumb"></th><th class="sortable" data-sort="title">title</th><th class="num sortable" data-sort="price" data-col="price">price</th><th class="sortable" data-sort="target" data-col="target">target</th>
  <th class="num sortable" data-sort="metric" data-col="metric">€/unit</th><th class="sortable" data-sort="condition" data-col="condition">condition</th><th data-col="compat">compat</th><th class="sortable" data-sort="seller" data-col="seller">seller</th><th data-col="history">history</th><th data-col="seen">seen</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
const DATA = __DATA__;
const $ = id => document.getElementById(id);
const state = () => ({target:$("target").value, text:$("text").value.trim().toLowerCase(),
  compat:$("compat").value, pmin:$("pmin").value, pmax:$("pmax").value, umax:$("umax").value, rmin:$("rmin").value,
  bargain:$("bargain").checked, relevant:$("relevant").checked, newtoday:$("newtoday").checked, dropped:$("dropped").checked});
function matches(l,f){
  if(f.target && l.target!==f.target) return false;
  const t=(l.title+" "+(l.brand||"")+" "+(l.model||"")).toLowerCase();
  if(f.text && !t.includes(f.text)) return false;
  if(f.compat==="ok" && l.compatible!==true) return false;
  if(f.compat==="no" && l.compatible!==false) return false;
  if(f.pmin!=="" && (l.price==null || l.price<+f.pmin)) return false;
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
const eur = n => n==null ? "" : "€"+n.toLocaleString("es-ES");
function spark(hist){
  const ps=(hist||[]).map(x=>x[1]).filter(p=>p!=null);
  if(ps.length<2) return '<span class="muted">—</span>';
  const w=64,h=20,p=2,mn=Math.min(...ps),mx=Math.max(...ps),r=(mx-mn)||1;
  const pt=(i,v)=>`${(i/(ps.length-1))*w},${h-p-((v-mn)/r)*(h-2*p)}`;
  const pts=ps.map((v,i)=>pt(i,v)).join(" ");
  const last=ps[ps.length-1], c=last<ps[0]?"#1a9a4a":"#d13737";
  return `<svg width="${w}" height="${h}"><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.5"/><circle cx="${w}" cy="${h-p-((last-mn)/r)*(h-2*p)}" r="1.6" fill="${c}"/></svg>`;
}
const cost=l=>l.condition==="broken"||!(l.price>0)?Infinity:(l.metric??l.price);
function risk(l){
  const s=l.seller;
  if(!s) return 1;
  const bad=(s.reviews==null||s.reviews<3)||(s.rating==null||s.rating<4)||!s.verified||(s.age!=null&&s.age<1);
  return bad?0:2;
}
function seller(l){
  const s=l.seller;
  if(!s) return '<span class="muted">—</span>';
  const reasons=[];
  if(s.reviews==null) reasons.push("no reviews");
  else if(s.reviews<3) reasons.push("only "+s.reviews+" review"+(s.reviews===1?"":"s"));
  if(s.rating==null) reasons.push("no rating");
  else if(s.rating<4) reasons.push("rating "+s.rating.toFixed(1));
  if(!s.verified) reasons.push("unverified");
  if(s.age!=null && s.age<1) reasons.push("new account ("+s.age+"y)");
  const stars = s.rating==null ? "?" : s.rating.toFixed(1);
  const tip = reasons.length ? `<span class="tag warn warn-tip" data-tip="${esc(reasons.join(" · "))}" aria-label="risky seller: ${esc(reasons.join(", "))}">⚠</span> ` : "";
  return `<span class="seller">${tip}${esc(s.name)} <span class="muted">${stars}★ · ${s.reviews??0}rv${s.verified?' · ✓':''}${s.age!=null?' · '+s.age+'y':''}</span></span>`;
}
const tag=(t,c)=>t==null||t===''?'':`<span class="tag ${c||''}">${esc(t)}</span>`;
const cell=(c,h,cls="")=>`<td data-col="${c}" class="${[cls,hiddenCols.has(c)?"hidden":""].filter(Boolean).join(" ")}">${h}</td>`;
function row(l){
  const img = l.thumb ? `<img class="thumb" src="${esc(l.thumb)}" loading="lazy">` : '';
  const metric = l.metric==null ? '' : l.metric.toFixed(1);
  return `<tr class="${l.bargain?'bargain':''}">
    ${cell("thumb", img)}
    <td><a href="${esc(l.url)}" target="_blank">${esc(l.title)}</a> ${tag(l.bargain?"bargain":"","bargain")}</td>
    ${cell("price", eur(l.price), "num")}
    ${cell("target", esc(l.target))}
    ${cell("metric", metric, "num")}
    ${cell("condition", tag(l.condition||""))}
    ${cell("compat", l.compatible===true?tag("ok","ok"):l.compatible===false?tag("no","no"):tag("?",""))}
    ${cell("seller", seller(l))}
    ${cell("history", spark(l.hist))}
    ${cell("seen", l.new_today?"new":l.price_dropped?"dropped":"·", "muted")}</tr>`;
}
function scatter(list){
  const pts=list.filter(l=>l.metric!=null&&l.price!=null);
  const box=$("scatter");
  if(!pts.length){box.innerHTML='<span class="muted">no €/unit data to plot</span>';return;}
  const W=640,H=240,pl=44,pr=12,pt=10,pb=28;
  const xmax=Math.max(...pts.map(p=>p.price))*1.05||1, ymax=Math.max(...pts.map(p=>p.metric))*1.1||1;
  const X=p=>pl+(p/xmax)*(W-pl-pr), Y=p=>H-pb-(p/ymax)*(H-pt-pb);
  const grid=(()=>{let g="";for(let i=1;i<4;i++){g+=`<line x1="${pl+(W-pl-pr)*i/4}" y1="${pt}" x2="${pl+(W-pl-pr)*i/4}" y2="${H-pb}"/>`;}for(let i=1;i<4;i++){g+=`<line x1="${pl}" y1="${pt+(H-pt-pb)*i/4}" x2="${W-pr}" y2="${pt+(H-pt-pb)*i/4}"/>`;}return g;})();
  const dots=pts.map(p=>{const c=p.bargain?"#e8a400":"#4a7fd0";
    return `<circle cx="${X(p.price)}" cy="${Y(p.metric)}" r="5" fill="${c}"><title>${esc(p.title)} — €${p.price}, ${p.metric.toFixed(1)}/u</title></circle>`;}).join("");
  box.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    ${grid}
    <line class="axis" x1="${pl}" y1="${H-pb}" x2="${W-pr}" y2="${H-pb}"/>
    <line class="axis" x1="${pl}" y1="${pt}" x2="${pl}" y2="${H-pb}"/>
    <text x="${pl}" y="${H-10}">€0</text>
    <text x="${W-pr-42}" y="${H-10}">€${xmax.toFixed(0)}</text>
    <text x="2" y="${pt+10}">€${ymax.toFixed(0)}/u</text>${dots}</svg>`;
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
function render(){
  const f=state();
  let list=DATA.filter(l=>matches(l,f));
  if(sortKey){
    const k=sortKey;
    list=[...list].sort((a,b)=>{const va=val(a,k),vb=val(b,k);return va===vb?0:(va<vb?-1:1)*sortDir;});
  }else{
    list=[...list].sort((a,b)=>(b.bargain-a.bargain)||(risk(b)-risk(a))||(cost(a)-cost(b)));
  }
  $("rows").innerHTML=list.length?list.map(row).join(""):'<tr><td colspan="10"><div class="empty"><b>No listings match</b>Try clearing or loosening the filters.</div></td></tr>';
  $("count").textContent=`${list.length} of ${DATA.length} listings`;
  scatter(list);
  document.querySelectorAll("th[data-sort]").forEach(th=>{
    th.classList.toggle("asc", th.dataset.sort===sortKey && sortDir===1);
    th.classList.toggle("desc", th.dataset.sort===sortKey && sortDir===-1);
  });
}
const hiddenCols = new Set(JSON.parse(localStorage.getItem("wpt-cols")||"[]"));
if(DATA.every(l=>l.compatible==null)){hiddenCols.add("compat");document.querySelector("th[data-col=compat]").classList.add("hidden");}
document.querySelectorAll("[data-col]").forEach(cb=>{ if(cb.type==="checkbox") cb.checked=!hiddenCols.has(cb.dataset.col); });
document.querySelectorAll(".cols-pop input[data-col]").forEach(cb=>cb.addEventListener("change",()=>{
  if(cb.checked) hiddenCols.delete(cb.dataset.col); else hiddenCols.add(cb.dataset.col);
  localStorage.setItem("wpt-cols", JSON.stringify([...hiddenCols]));
  document.querySelectorAll("th[data-col]").forEach(th=>th.classList.toggle("hidden", hiddenCols.has(th.dataset.col)));
  render();
}));
document.querySelectorAll("th[data-sort]").forEach(th=>th.addEventListener("click",()=>{
  const k=th.dataset.sort;
  if(sortKey===k){ if(sortDir===1) sortDir=-1; else {sortKey=null; sortDir=1;} } else {sortKey=k; sortDir=1;}
  render();
}));
[...document.querySelectorAll("#filters input,#filters select")].forEach(el=>el.addEventListener("input",render));
const THEME="wpt-theme";
const themeBtn=$("theme");
const themeLabel={system:"Auto",light:"Light",dark:"Dark"};
function applyTheme(m){
  if(m==="light") document.documentElement.dataset.theme="light";
  else if(m==="dark") document.documentElement.dataset.theme="dark";
  else delete document.documentElement.dataset.theme;
  themeBtn.textContent=themeLabel[m];
  localStorage.setItem(THEME,m);
}
let themeMode=localStorage.getItem(THEME)||"system";
applyTheme(themeMode);
themeBtn.addEventListener("click",()=>{
  themeMode = themeMode==="system"?"light":themeMode==="light"?"dark":"system";
  applyTheme(themeMode);
});
const chromeEl=document.getElementById("chrome");
function syncChrome(){document.documentElement.style.setProperty("--chrome-h",(chromeEl?chromeEl.offsetHeight:0)+"px");}
if(chromeEl){syncChrome();new ResizeObserver(syncChrome).observe(chromeEl);}
render();
</script></body></html>"""
