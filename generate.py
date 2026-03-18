import socket, ssl, time, yaml, json, hashlib, random, math
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── parse ────────────────────────────────────────────────────────────────────

def parse_vless(link):
    parsed = urlparse("https://" + link.strip()[8:])
    params = parse_qs(parsed.query)
    p = lambda k, d="": params.get(k, [d])[0]
    raw_name = unquote(parsed.fragment) if parsed.fragment else f"{parsed.hostname}:{parsed.port}"
    # убираем эмодзи-флаги из имени
    clean = "".join(c for c in raw_name if ord(c) < 0x1F000 or ord(c) > 0x1FFFF)
    clean = clean.strip()
    return {
        "address": parsed.hostname, "port": parsed.port or 443,
        "security": p("security", "none"), "sni": p("sni"),
        "transport": p("type", "tcp"), "name": clean, "link": link.strip(),
    }

# ── country from name ────────────────────────────────────────────────────────

COUNTRY_MAP = {
    "DE": "Germany", "NL": "Netherlands", "PL": "Poland",
    "FI": "Finland", "AT": "Austria", "CH": "Switzerland",
    "Germaniya": "Germany", "Niderlandy": "Netherlands",
    "Polsha": "Poland", "Finlyandiya": "Finland",
    "Avstriya": "Austria", "Shveycariya": "Switzerland",
}

COUNTRY_FLAGS = {
    "Germany": "DE", "Netherlands": "NL", "Poland": "PL",
    "Finland": "FI", "Austria": "AT", "Switzerland": "CH",
}

def detect_country(name):
    for key, country in COUNTRY_MAP.items():
        if key.lower() in name.lower():
            return country
    return "Unknown"

# ── check ────────────────────────────────────────────────────────────────────

def check_one(srv, timeout=5, retries=2):
    for attempt in range(retries):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            t = time.monotonic()
            s.connect((srv["address"], srv["port"]))
            if srv["security"] in ("tls", "reality"):
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ctx.set_alpn_protocols(["h2", "http/1.1"])
                tls = ctx.wrap_socket(s, server_hostname=srv["sni"] or srv["address"])
                ms = (time.monotonic() - t) * 1000
                tls.close()
            else:
                ms = (time.monotonic() - t) * 1000
                s.close()
            return {**srv, "online": True, "latency": round(ms)}
        except Exception:
            try: s.close()
            except: pass
            if attempt < retries - 1:
                time.sleep(1)
    return {**srv, "online": False, "latency": 0}

# ── run checks ───────────────────────────────────────────────────────────────

with open("config.yaml") as f:
    cfg = yaml.safe_load(f)

servers = [parse_vless(l) for l in cfg.get("servers", [])]
results = []

with ThreadPoolExecutor(max_workers=15) as pool:
    futures = {pool.submit(check_one, s, cfg.get("timeout", 5), cfg.get("retries", 2)): s for s in servers}
    for fut in as_completed(futures):
        results.append(fut.result())

# сортируем: сначала online, потом по имени
results.sort(key=lambda r: (not r["online"], r["name"]))

# ── history ──────────────────────────────────────────────────────────────────

HIST = Path("history.json")
history = {}
if HIST.exists():
    try: history = json.loads(HIST.read_text())
    except: pass

now = time.time()
for r in results:
    key = f"{r['address']}:{r['port']}"
    if key not in history:
        history[key] = []
    history[key].append({"t": now, "up": r["online"]})
    history[key] = history[key][-96:]  # 24h при 15-мин интервале

HIST.write_text(json.dumps(history))

# ── compute uptimes ─────────────────────────────────────────────────────────

for r in results:
    key = f"{r['address']}:{r['port']}"
    h = history.get(key, [])
    total = len(h)
    up = sum(1 for x in h if x["up"])
    r["uptime"] = round(up / max(total, 1) * 100, 1)

# ── group by country ────────────────────────────────────────────────────────

countries = {}
for r in results:
    c = detect_country(r["name"])
    r["country"] = c
    if c not in countries:
        countries[c] = []
    countries[c].append(r)

# ── stats ────────────────────────────────────────────────────────────────────

total = len(results)
online = sum(1 for r in results if r["online"])
offline = total - online
ts = datetime.now(timezone.utc).strftime("%H:%M UTC / %d.%m.%Y")

# ── generate SVG shapes seed ─────────────────────────────────────────────────
# Каждый деплой = новый набор геометрических фигур в углах

seed = int(now) % 100000
random.seed(seed)

def rand_shape():
    shapes_pool = []
    corners = [
        ("top:0;left:0", "translate(-30%,-30%)"),
        ("top:0;right:0", "translate(30%,-30%)"),
        ("bottom:0;left:0", "translate(-30%,30%)"),
        ("bottom:0;right:0", "translate(30%,30%)"),
    ]
    for pos, translate in corners:
        kind = random.choice(["circle", "triangle", "square", "hexagon", "cross"])
        size = random.randint(120, 280)
        rot = random.randint(0, 360)
        opacity = random.uniform(0.03, 0.07)

        if kind == "circle":
            svg = f'<circle cx="{size//2}" cy="{size//2}" r="{size//2}" fill="white" fill-opacity="{opacity}"/>'
        elif kind == "triangle":
            h = int(size * 0.866)
            svg = f'<polygon points="{size//2},0 {size},{h} 0,{h}" fill="white" fill-opacity="{opacity}"/>'
        elif kind == "square":
            svg = f'<rect width="{size}" height="{size}" fill="white" fill-opacity="{opacity}" rx="4"/>'
        elif kind == "hexagon":
            pts = []
            for i in range(6):
                a = math.pi / 3 * i - math.pi / 2
                pts.append(f"{size//2 + int(size//2 * math.cos(a))},{size//2 + int(size//2 * math.sin(a))}")
            svg = f'<polygon points="{" ".join(pts)}" fill="white" fill-opacity="{opacity}"/>'
        elif kind == "cross":
            t = size // 5
            c = size // 2
            svg = (f'<rect x="{c-t//2}" y="0" width="{t}" height="{size}" fill="white" fill-opacity="{opacity}" rx="2"/>'
                   f'<rect x="0" y="{c-t//2}" width="{size}" height="{t}" fill="white" fill-opacity="{opacity}" rx="2"/>')

        shapes_pool.append(
            f'<div style="position:fixed;{pos};pointer-events:none;transform:{translate} rotate({rot}deg);z-index:0">'
            f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">{svg}</svg></div>'
        )
    return "\n".join(shapes_pool)

shapes_html = rand_shape()

# ── dot field background (CSS only) ─────────────────────────────────────────

dot_bg = (
    "background-image:radial-gradient(circle,rgba(255,255,255,.07) 1px,transparent 1px);"
    "background-size:24px 24px;"
)

# ── build server cards ───────────────────────────────────────────────────────

def upt_color(p):
    if p >= 99: return "#4ade80"
    if p >= 90: return "#a3a3a3"
    return "#f87171"

def build_card(r):
    cls = "on" if r["online"] else "off"
    lat = f"{r['latency']} ms" if r["online"] else "OFFLINE"
    col = upt_color(r["uptime"])
    return f'''<div class="card {cls}">
<div class="card-left"><div class="dot {cls}"></div></div>
<div class="card-mid">
<div class="card-name">{r["name"]}</div>
<div class="card-addr">{r["address"]}:{r["port"]}</div>
<div class="card-tags"><span class="tag">{r["security"]}</span>{f'<span class="tag">{r["transport"]}</span>' if r["transport"] not in ("tcp","") else ""}</div>
</div>
<div class="card-right">
<div class="card-lat {cls}">{lat}</div>
<div class="card-upt"><span class="upt-track"><span class="upt-fill" style="width:{r["uptime"]}%;background:{col}"></span></span>{r["uptime"]}%</div>
</div>
</div>'''

groups_html = ""
for country in sorted(countries.keys()):
    srvs = countries[country]
    flag_code = COUNTRY_FLAGS.get(country, "")
    on = sum(1 for s in srvs if s["online"])
    tot = len(srvs)
    groups_html += f'<div class="group">'
    groups_html += f'<div class="group-hdr"><span class="group-name">[{flag_code}] {country}</span><span class="group-count">{on}/{tot}</span></div>'
    for s in srvs:
        groups_html += build_card(s)
    groups_html += '</div>'

# ── assemble HTML ────────────────────────────────────────────────────────────

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{cfg.get("title","Status")}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{min-height:100vh}}
body{{
  background:#0a0a0a;color:#d4d4d4;
  font-family:Consolas,"Courier New",monospace;
  {dot_bg}
}}
.wrap{{max-width:860px;margin:0 auto;padding:24px 16px;position:relative;z-index:1}}

/* header */
.hdr{{text-align:center;padding:32px 0 24px;border-bottom:1px solid #1a1a1a;margin-bottom:24px}}
.hdr-title{{font-size:1.4rem;letter-spacing:.15em;text-transform:uppercase;color:#e5e5e5;margin-bottom:4px}}
.hdr-sub{{font-size:.75rem;color:#525252;letter-spacing:.1em;text-transform:uppercase}}
.hdr-stats{{display:flex;justify-content:center;gap:24px;margin-top:16px;flex-wrap:wrap}}
.hdr-stat{{font-size:.8rem;letter-spacing:.05em}}
.hdr-stat.on{{color:#4ade80}}.hdr-stat.off{{color:#f87171}}.hdr-stat.all{{color:#737373}}

/* groups */
.group{{margin-bottom:20px}}
.group-hdr{{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #1a1a1a;margin-bottom:8px}}
.group-name{{font-size:.8rem;color:#737373;letter-spacing:.08em;text-transform:uppercase}}
.group-count{{font-size:.75rem;color:#525252}}

/* cards */
.card{{
  display:grid;grid-template-columns:20px 1fr auto;gap:12px;align-items:center;
  padding:12px 14px;margin-bottom:4px;
  border:1px solid #171717;border-radius:4px;
  transition:border-color .15s;
}}
.card:hover{{border-color:#262626}}
.card.off{{opacity:.55}}
.dot{{width:8px;height:8px;border-radius:50%}}
.dot.on{{background:#4ade80;box-shadow:0 0 6px #4ade8066}}
.dot.off{{background:#f87171;box-shadow:0 0 6px #f8717166}}
.card-name{{font-size:.85rem;color:#d4d4d4}}
.card-addr{{font-size:.7rem;color:#404040;font-family:Consolas,monospace}}
.card-tags{{display:flex;gap:4px;margin-top:3px}}
.tag{{font-size:.6rem;padding:1px 5px;border:1px solid #1f1f1f;border-radius:2px;color:#525252}}
.card-right{{text-align:right;min-width:80px}}
.card-lat{{font-size:.9rem;font-weight:700}}
.card-lat.on{{color:#4ade80}}.card-lat.off{{color:#f87171}}
.card-upt{{font-size:.65rem;color:#404040;margin-top:2px}}
.upt-track{{display:inline-block;width:48px;height:3px;background:#171717;border-radius:1px;overflow:hidden;vertical-align:middle;margin-right:4px}}
.upt-fill{{display:block;height:100%;border-radius:1px}}

/* footer */
.foot{{text-align:center;padding:24px 0;border-top:1px solid #1a1a1a;margin-top:24px;font-size:.65rem;color:#333;letter-spacing:.05em}}

@media(max-width:500px){{
  .card{{grid-template-columns:16px 1fr;gap:8px}}
  .card-right{{grid-column:1/-1;text-align:left;display:flex;gap:12px;align-items:center}}
  .hdr-title{{font-size:1.1rem}}
}}
</style>
</head>
<body>

{shapes_html}

<div class="wrap">
<div class="hdr">
  <div class="hdr-title">{cfg.get("title","Status")}</div>
  <div class="hdr-sub">{cfg.get("subtitle","server status")}</div>
  <div class="hdr-stats">
    <span class="hdr-stat all">[{total} total]</span>
    <span class="hdr-stat on">[{online} online]</span>
    <span class="hdr-stat off">[{offline} offline]</span>
  </div>
</div>

{groups_html}

<div class="foot">
  updated {ts} // checks every {cfg.get("check_interval",15)} min // @LLxickVPN
</div>
</div>

</body>
</html>'''

Path("public").mkdir(exist_ok=True)
Path("public/index.html").write_text(html, encoding="utf-8")

print(f"\n{'='*50}")
print(f"  {online}/{total} servers online")
print(f"  Generated public/index.html")
print(f"{'='*50}")
