"""manuals-fetcher: polite downloader for public-domain, Distribution A manuals.

Reads targets from TARGETS (JSON list), writes PDFs to DATA/<category>/<slug>.pdf,
never overwrites an existing file, and keeps DATA/manifest.csv + manifest.json current.
State (fetch log, quarantine) lives in DATA/.state/.
"""
import csv, hashlib, html, json, os, re, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pypdf import PdfReader

DATA = os.environ.get("DATA", "/data")
TARGETS = os.environ.get("TARGETS", "/config/targets.json")
DELAY = float(os.environ.get("DELAY_SECONDS", "4"))
RETRY_SKIPPED = os.environ.get("RETRY_SKIPPED", "0") == "1"
CHECK_EDITIONS = os.environ.get("CHECK_EDITIONS", "1") == "1"
UA = "Mozilla/5.0 (compatible; manuals-archiver/1.0)"
ALLOWED_HOSTS = ("armypubs.army.mil", "marines.mil", "fema.gov", "ready.gov", "dtic.mil", "archive.org")
# FEMA/ready.gov public guides carry no DoD distribution statement. Approved by Kevin 2026-11-16:
# on these hosts only, a target flagged "public_web" is accepted when no restriction marking is found.
PUBLIC_WEB_HOSTS = ("fema.gov", "ready.gov")
BLOCKED_HOSTS = ("trex-arms.com",)
CATEGORIES = ("radio-signal", "emergency-comms", "land-navigation", "survival", "first-aid", "cold-weather")
DESC = {
    "radio-signal": "Military reference on radio, signal and communications techniques, useful background for off-grid and emergency radio operation.",
    "emergency-comms": "Guidance on emergency response, civil support and supporting services during disasters.",
    "land-navigation": "Instruction on map reading, terrain association, compass use and moving on foot.",
    "survival": "Field survival techniques covering shelter, water, food, signaling and travel in difficult environments.",
    "first-aid": "Field first aid, casualty care and sanitation procedures for austere conditions.",
    "cold-weather": "Techniques for operating and surviving in cold weather, snow and mountainous terrain.",
}
CSV_COLS = ["file", "pdf_file", "title", "publication_number", "pubno", "date", "issuer", "category",
            "source_url", "sha256", "distribution", "pages", "description"]
STATE = os.path.join(DATA, ".state")
QUAR = os.path.join(STATE, "quarantine")
LOG = os.path.join(STATE, "fetch_log.jsonl")

_last = [0.0]


def host_ok(url):
    h = (urllib.parse.urlparse(url).hostname or "").lower()
    if any(h == b or h.endswith("." + b) for b in BLOCKED_HOSTS):
        return False
    return any(h == a or h.endswith("." + a) for a in ALLOWED_HOSTS)


def get(url):
    if not host_ok(url):
        raise ValueError(f"source not on approved list: {url}")
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                final = r.geturl()
                if not host_ok(final):
                    raise ValueError(f"redirected off approved sources: {final}")
                return r.headers.get("Content-Type", ""), r.read(), final
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 3:
                raise
        except urllib.error.URLError as e:
            # transient DNS / connection failures: back off and retry (one request at a time)
            if attempt == 3 or "CERTIFICATE_VERIFY_FAILED" in str(e):
                raise
        finally:
            _last[0] = time.time()
        time.sleep(DELAY * (attempt + 2) * 2)


def dist_check(path):
    """Return (status, evidence). status: A | restricted | unclear."""
    try:
        rd = PdfReader(path)
        first = " ".join((rd.pages[i].extract_text() or "") for i in range(min(8, len(rd.pages))))
        allt = " ".join((p.extract_text() or "") for p in rd.pages)
    except Exception as e:
        return "unclear", f"pdf parse error: {e}"
    low = re.sub(r"\s+", " ", first).lower()
    full = re.sub(r"\s+", " ", allt).lower()
    bad_pat = r"(distribution authorized to [^.;]{0,120}|for official use only \(fouo\)|//fouo|cui//|distribution statement [b-f]\b|noforn)"
    good = re.search(r"approved for public release[;:,]? ?distribution is unlimited", low)
    bad_any = re.search(r"distribution authorized to [^.;]{0,120}", full)
    bad_first = re.search(bad_pat, low)
    if good and not bad_any and not bad_first:
        return "A", "Distribution A: approved for public release; distribution is unlimited"
    if bad_first and not good:
        return "restricted", bad_first.group(0)[:160]
    if good and (bad_any or bad_first):
        return "unclear", "both a public-release statement and a restriction block are present"
    if len(low.strip()) < 200:
        return "unclear", "no extractable text in first pages"
    return "unclear", "no distribution statement found in first 8 pages"


def slug_for(t):
    pubno = t.get("pubno") or t["pub"]
    yr = re.match(r".*\((\d{4})\)$", t["pub"])
    return re.sub(r"[^a-z0-9]+", "-", pubno.lower()).strip("-") + (("-" + yr.group(1)) if yr else "")


def norm_date(d):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else (d or "")


def load_manifest():
    p = os.path.join(DATA, "manifest.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return {"items": [], "skipped": []}


def atomic_write(path, text):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def save_manifest(m):
    m["items"].sort(key=lambda x: (x["category"], x["publication_number"]))
    m["count"] = len(m["items"])
    m["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_write(os.path.join(DATA, "manifest.json"), json.dumps(m, indent=1))
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLS, extrasaction="ignore")
    w.writeheader()
    w.writerows(m["items"])
    atomic_write(os.path.join(DATA, "manifest.csv"), buf.getvalue())


def log(rec):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(rec.get("status"), rec.get("pub"), rec.get("reason", ""), flush=True)


def main():
    os.makedirs(QUAR, exist_ok=True)
    os.makedirs(os.path.join(DATA, "epub"), exist_ok=True)
    targets = json.load(open(TARGETS, encoding="utf-8"))
    m = load_manifest()
    have = {i["file"]: i for i in m["items"]}
    prior_skips = {s["publication"] for s in m.get("skipped", [])}
    skipped = {s["publication"]: s for s in m.get("skipped", [])}
    notices = []
    added = 0
    for t in targets:
        pub, cat = t["pub"], t["category"]
        if cat not in CATEGORIES:
            log({"pub": pub, "status": "error", "reason": f"bad category {cat}"}); continue
        rel = f"{cat}/{slug_for(t)}.pdf"
        path = os.path.join(DATA, rel)
        rec = {"pub": pub, "time": datetime.now(timezone.utc).isoformat()}
        try:
            if os.path.exists(path):
                if CHECK_EDITIONS and t.get("armypubs_id") and rel in have:
                    _, body, _ = get(f"https://armypubs.army.mil/ProductMaps/PubForm/Details.aspx?PUB_ID={t['armypubs_id']}")
                    d = re.search(r"Pub/Form Date</td><td>([^<]+)<", body.decode("utf-8", "ignore"))
                    if d and norm_date(d.group(1).strip()) != have[rel]["date"]:
                        notices.append(f"{pub}: armypubs shows a {norm_date(d.group(1).strip())} edition; "
                                       f"kept existing {have[rel]['date']} file (no overwrite)")
                continue
            if pub in prior_skips and not RETRY_SKIPPED:
                continue
            url, date = t.get("url"), t.get("date", "")
            if t.get("armypubs_id"):
                _, body, _ = get(f"https://armypubs.army.mil/ProductMaps/PubForm/Details.aspx?PUB_ID={t['armypubs_id']}")
                s = body.decode("utf-8", "ignore")
                mm = re.search(r"""href=['"]([^'"]*DR_pubs[^'"]+\.pdf)['"]""", s, re.I)
                d = re.search(r"Pub/Form Date</td><td>([^<]+)<", s)
                if d:
                    date = d.group(1).strip()
                if not mm:
                    raise LookupError("skip:no public PDF link on details page")
                url = urllib.parse.urljoin("https://armypubs.army.mil/epubs/", mm.group(1).replace(" ", "%20"))
                if re.search(r"/DR_pubs/DR_[b-f]/", url, re.I):
                    raise LookupError("skip:restricted: PDF is in an Army limited-distribution (CAC-only) path")
            rec["source_url"] = url
            ct, body, final = get(url)
            if body[:5] != b"%PDF-":
                raise LookupError(f"skip:not a PDF (content-type {ct})")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=STATE, prefix=".dl-", suffix=".pdf")
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            status, why = dist_check(tmp)
            fhost = (urllib.parse.urlparse(final).hostname or "").lower()
            if (status == "unclear" and t.get("public_web") and why.startswith("no distribution statement")
                    and any(fhost == h or fhost.endswith("." + h) for h in PUBLIC_WEB_HOSTS)):
                status, why = "A", f"Public FEMA publication posted for general distribution on {fhost}; no restriction markings found"
            if status != "A":
                qp = os.path.join(QUAR, os.path.basename(rel))
                if os.path.exists(qp):
                    os.remove(tmp)
                else:
                    os.replace(tmp, qp)
                raise LookupError(f"skip:distribution {status}: {why}")
            try:
                os.link(tmp, path)  # atomic create; fails if the file already exists (never overwrite)
            except FileExistsError:
                os.remove(tmp)
                continue
            os.remove(tmp)
            try:
                pages = len(PdfReader(path).pages)
            except Exception:
                pages = ""
            pubno = t.get("pubno") or pub
            date = norm_date(date)
            item = dict(file=rel, pdf_file=rel, title=t["title"], publication_number=pubno, pubno=pubno,
                        date=date, issuer=t.get("issuer", "U.S. Army"), category=cat, source_url=url,
                        sha256=hashlib.sha256(body).hexdigest(), distribution=why, pages=pages,
                        description=f"{pubno} ({date[:4]}), {t['title']}. " + DESC[cat],
                        source_site=urllib.parse.urlparse(url).hostname, bytes=len(body))
            m["items"] = [i for i in m["items"] if i["file"] != rel] + [item]
            have[rel] = item
            skipped.pop(pub, None)
            added += 1
            rec.update(status="ok", file=rel)
            log(rec)
            save_manifest(m)  # persist after each success
        except LookupError as e:
            reason = str(e)[5:] if str(e).startswith("skip:") else str(e)
            skipped[pub] = dict(publication=pub, title=t.get("title", ""), source_url=rec.get("source_url", t.get("url", "")),
                                reason=reason)
            rec.update(status="skipped", reason=reason)
            log(rec)
        except Exception as e:
            rec.update(status="error", reason=repr(e)[:200])
            log(rec)
    m["skipped"] = sorted(skipped.values(), key=lambda s: s["publication"])
    m["notices"] = notices
    save_manifest(m)
    print(f"done: {added} added, {len(m['items'])} in library, {len(m['skipped'])} skipped, {len(notices)} edition notices", flush=True)


if __name__ == "__main__":
    main()
