"""library-search: read-only LAN web app over DATA/manifest.csv. Python stdlib only."""
import csv, html, json, os, posixpath, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.path.realpath(os.environ.get("DATA", "/data"))
PORT = int(os.environ.get("PORT", "8080"))
CATS = ["radio-signal", "emergency-comms", "land-navigation", "survival", "first-aid", "cold-weather"]


def load():
    try:
        with open(os.path.join(DATA, "manifest.csv"), encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Manuals Library</title>
<style>
:root{--bg:#fafaf7;--fg:#1d1f1c;--muted:#6b6f68;--line:#dcdcd4;--accent:#2f5d50;--chip:#eef1ec}
@media (prefers-color-scheme:dark){:root{--bg:#161816;--fg:#e8e9e4;--muted:#9a9e96;--line:#2e322d;--accent:#7fb8a4;--chip:#232723}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:20px 16px}h1{font-size:22px;margin:0 0 4px}p.sub{color:var(--muted);margin:0 0 16px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}input{flex:1;min-width:200px;padding:9px 11px;border:1px solid var(--line);border-radius:6px;background:transparent;color:var(--fg);font:inherit}
button.c{padding:6px 10px;border:1px solid var(--line);border-radius:999px;background:var(--chip);color:var(--fg);cursor:pointer;font:inherit;font-size:13px}
button.c.on{background:var(--accent);border-color:var(--accent);color:var(--bg)}
.item{border-top:1px solid var(--line);padding:12px 0}.item:first-child{border-top:0}
.t{font-weight:600}.meta{color:var(--muted);font-size:13px}.d{margin:4px 0}a{color:var(--accent)}
#count{color:var(--muted);font-size:13px;margin-bottom:6px}
</style></head><body><main>
<h1>Manuals Library</h1><p class="sub">Public-domain, Distribution A manuals. Read-only.</p>
<div class="bar"><input id="q" placeholder="Search title, number, description" autofocus></div>
<div class="bar" id="cats"></div><div id="count"></div><div id="list"></div>
<script>
const CATS=__CATS__;let rows=[],cat="";
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function chips(){const el=document.getElementById("cats");el.innerHTML="";["",...CATS].forEach(c=>{const b=document.createElement("button");
b.className="c"+(c===cat?" on":"");b.textContent=c||"all";b.onclick=()=>{cat=c;chips();draw()};el.appendChild(b)})}
function draw(){const q=document.getElementById("q").value.toLowerCase().trim();
const f=rows.filter(r=>(!cat||r.category===cat)&&(!q||[r.title,r.publication_number,r.description,r.category].join(" ").toLowerCase().includes(q)));
document.getElementById("count").textContent=f.length+" of "+rows.length;
document.getElementById("list").innerHTML=f.map(r=>`<div class="item"><div class="t">${esc(r.publication_number)}: ${esc(r.title)}</div>
<div class="meta">${esc(r.date)} &middot; ${esc(r.issuer)} &middot; ${esc(r.category)} &middot; ${esc(r.pages)} pages</div>
<div class="d">${esc(r.description)}</div>
<div class="meta"><a href="/files/${encodeURI(r.file)}" target="_blank" rel="noopener">View PDF</a> &middot;
<a href="/files/${encodeURI(r.file)}?download=1">Download</a> &middot; <a href="${esc(r.source_url)}" rel="noopener noreferrer">Source</a></div></div>`).join("")}
fetch("/api/manifest").then(r=>r.json()).then(d=>{rows=d;chips();draw()});
document.getElementById("q").addEventListener("input",draw);
</script></main></body></html>"""


class H(BaseHTTPRequestHandler):
    server_version = "library-search"

    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            return self._send(200, "text/html; charset=utf-8", PAGE.replace("__CATS__", json.dumps(CATS)).encode())
        if u.path == "/healthz":
            return self._send(200, "text/plain", b"ok")
        if u.path == "/api/manifest":
            return self._send(200, "application/json", json.dumps(load()).encode())
        if u.path == "/manifest.csv":
            p = os.path.join(DATA, "manifest.csv")
            return self._send(200, "text/csv; charset=utf-8", open(p, "rb").read()) if os.path.exists(p) else self._send(404, "text/plain", b"no manifest")
        if u.path.startswith("/files/"):
            rel = posixpath.normpath(urllib.parse.unquote(u.path[len("/files/"):]))
            allowed = {r["file"] for r in load()}
            if rel not in allowed:
                return self._send(404, "text/plain", b"not found")
            p = os.path.realpath(os.path.join(DATA, rel))
            if not p.startswith(DATA + os.sep) or not os.path.isfile(p):
                return self._send(404, "text/plain", b"not found")
            disp = "attachment" if "download=1" in (u.query or "") else "inline"
            name = os.path.basename(p)
            size = os.path.getsize(p)
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'{disp}; filename="{name}"')
            self.end_headers()
            if self.command != "HEAD":
                with open(p, "rb") as f:
                    while chunk := f.read(1 << 20):
                        self.wfile.write(chunk)
            return
        return self._send(404, "text/plain", b"not found")

    def log_message(self, fmt, *args):
        print("%s %s" % (self.address_string(), fmt % args), flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
