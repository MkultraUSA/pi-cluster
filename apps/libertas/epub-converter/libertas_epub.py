#!/usr/bin/env python3
"""Libertas Library: official manual PDF -> OCR -> branded EPUB.

Usage:
  python3 libertas_epub.py INPUT.pdf OUTDIR [--title T] [--pubno P] [--date D]
                           [--source-url URL] [--brand brand.json] [--no-ocr]
  python3 libertas_epub.py --manifest manifest.csv --pdf-dir DIR OUTDIR

Needs: ocrmypdf + tesseract-ocr, pandoc, python3 PyMuPDF (Debian: python3-fitz).
Every step is skipped if its output already exists, so a batch can be re-run safely.
"""
import argparse, csv, html, json, os, re, shutil, statistics, subprocess, sys, zipfile
from collections import Counter
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:
    import fitz

HERE = Path(__file__).resolve().parent
DEFAULT_BRAND = {
    "site": "libertas.mobi",
    "library_name": "Libertas Library",
    "support_heading": "Questions? Call Kevin, a real person.",
    "support_body": "Not an AI, not a call center. Kevin builds and supports off-grid "
                    "communication kits for rural properties and will walk you through "
                    "radios, antennas, power and programming.",
    "phone": "",
    "email": "",
    "url": "https://libertas.mobi",
    "kits_url": "https://libertas.mobi/kits",
    "support_url": "https://libertas.mobi/support/",
    "accent": [0.55, 0.12, 0.10],
}

def log(msg):
    print(f"[libertas] {msg}", flush=True)

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "manual"

# ---------------------------------------------------------------- OCR
def needs_ocr(pdf):
    doc = fitz.open(pdf)
    n = len(doc)
    sample = range(0, n, max(1, n // 20))
    return any(len(doc[i].get_text().strip()) < 40 and doc[i].get_images()
               for i in range(n))

def run_ocr(src, dst):
    if dst.exists():
        return dst
    if not needs_ocr(src):
        log("text layer present, OCR skipped")
        shutil.copy(src, dst)
        return dst
    log("running ocrmypdf (scanned pages found)")
    ocr = os.environ.get("OCRMYPDF", "ocrmypdf").split()
    cmd = ocr + ["--skip-text", "--rotate-pages", "--deskew", "--optimize", "1",
           "--jobs", str(os.cpu_count() or 2), str(src), str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode not in (0, 6):  # 6 = already has text
        # deskew is incompatible with --skip-text on some versions: retry plainly
        r = subprocess.run(ocr + ["--skip-text", str(src), str(dst)],
                           capture_output=True, text=True)
        if r.returncode not in (0, 6):
            raise RuntimeError("ocrmypdf failed: " + r.stderr[-800:])
    if not dst.exists():
        shutil.copy(src, dst)
    return dst

# ---------------------------------------------------------------- chapters
CHAP_RE = re.compile(r"^\s*(chapter|appendix|annex|part|section)\s+([0-9IVXLC]+|[A-Z])\b[\s.:-]*(.*)$", re.I)

def find_chapters(doc):
    """Return [(title, start_page_index)] sorted by page."""
    toc = doc.get_toc(simple=True)
    if toc:
        top = min(t[0] for t in toc)
        ch = [(t[1].strip(), t[2] - 1) for t in toc if t[0] == top and t[2] >= 1]
        # one huge top-level entry (e.g. the whole book) -> use the next level
        if len(ch) < 3:
            lvl2 = [(t[1].strip(), t[2] - 1) for t in toc if t[0] == top + 1 and t[2] >= 1]
            if len(lvl2) > len(ch):
                ch = lvl2
        if len(ch) >= 2:
            return dedupe(ch)
    # fall back to "CHAPTER n" lines near the top of a page
    ch = []
    for i, page in enumerate(doc):
        lines = [l.strip() for l in page.get_text().splitlines() if l.strip()][:6]
        for j, l in enumerate(lines):
            m = CHAP_RE.match(l)
            if m and len(l) < 90:
                title = l if m.group(3) else (l + (" " + lines[j + 1] if j + 1 < len(lines) else ""))
                ch.append((title.title() if title.isupper() else title, i))
                break
    if len(ch) >= 2:
        return dedupe(ch)
    step = 15
    return [(f"Pages {i+1}-{min(i+step, len(doc))}", i) for i in range(0, len(doc), step)]

def dedupe(ch):
    seen, out = set(), []
    for t, p in sorted(ch, key=lambda x: x[1]):
        if p in seen:
            continue
        seen.add(p)
        out.append((t, p))
    if out and out[0][1] > 0:
        out.insert(0, ("Front Matter", 0))
    return out

# ---------------------------------------------------------------- page text
def repeating_lines(doc):
    """Running headers/footers: short lines that appear on many pages."""
    c = Counter()
    for page in doc:
        h = page.rect.height
        for b in page.get_text("blocks"):
            if b[1] < h * 0.08 or b[3] > h * 0.92:
                key = re.sub(r"\d+", "#", b[4].strip())
                if 0 < len(key) < 120:
                    c[key] += 1
    limit = max(3, len(doc) * 0.25)
    return {k for k, v in c.items() if v >= limit}

def body_size(doc):
    sizes = []
    for page in list(doc)[: min(len(doc), 40)]:
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    if s["text"].strip():
                        sizes.append(round(s["size"], 1))
    return statistics.median(sizes) if sizes else 10

def page_html(doc, idx, rep, bsize, imgdir, rel):
    page = doc[idx]
    h = page.rect.height
    out = []
    for b in page.get_text("dict", sort=True)["blocks"]:
        if b["type"] == 1:  # raster image
            w, hh = b.get("width", 0), b.get("height", 0)
            bb = fitz.Rect(b["bbox"])
            if bb.get_area() > page.rect.get_area() * 0.8:
                # full-page scan: OCR text carries it; keep a small picture only if
                # the page is mostly figure (little recognised text)
                if len(page.get_text().strip()) < 600:
                    name = f"scan{idx+1:04d}.jpg"
                    page.get_pixmap(dpi=100).save(str(imgdir / name), jpg_quality=65)
                    out.append(f'<figure class="pageimg"><img src="{rel}/{name}" alt="Original page {idx+1}"/>'
                               f"<figcaption>Original page {idx+1}</figcaption></figure>")
                continue
            if w >= 120 and hh >= 120 and b.get("image"):
                ext = b.get("ext", "png")
                if ext not in ("png", "jpeg", "jpg"):
                    continue
                name = f"p{idx+1:04d}_{len(out)}.{ext}"
                (imgdir / name).write_bytes(b["image"])
                out.append(f'<figure><img src="{rel}/{name}" alt="Figure, page {idx+1}"/></figure>')
            continue
        lines, size = [], 0
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"])
            if t.strip():
                lines.append(t.strip())
                size = max(size, max(s["size"] for s in l["spans"]))
        if not lines:
            continue
        raw = "\n".join(lines)
        if (b["bbox"][1] < h * 0.08 or b["bbox"][3] > h * 0.92) and \
                re.sub(r"\d+", "#", raw.strip()) in rep:
            continue
        if re.fullmatch(r"[\divxlc\-–]+", raw.strip(), re.I):  # bare page number
            continue
        text = " ".join(lines)
        text = re.sub(r"(\w)- (\w)", r"\1\2", text)  # de-hyphenate line breaks
        esc = html.escape(text)
        if size >= bsize * 1.25 and len(text) < 120:
            out.append(f"<h3>{esc}</h3>")
        elif size >= bsize * 1.1 and len(text) < 120:
            out.append(f"<h4>{esc}</h4>")
        else:
            out.append(f"<p>{esc}</p>")
    # vector figures (diagrams, charts) do not survive text extraction: keep a picture of the page
    try:
        drawings = len(page.get_drawings())
    except Exception:
        drawings = 0
    textlen = len(page.get_text().strip())
    if drawings > 40 or (textlen < 200 and drawings > 5):
        name = f"page{idx+1:04d}.jpg"
        page.get_pixmap(dpi=110).save(str(imgdir / name), jpg_quality=70)
        out.append(f'<figure class="pageimg"><img src="{rel}/{name}" alt="Original page {idx+1}"/>'
                   f"<figcaption>Original page {idx+1}</figcaption></figure>")
    return "\n".join(out)

# ---------------------------------------------------------------- cover
def make_cover(doc, meta, brand, path):
    W, H = 1200, 1800
    acc = tuple(brand["accent"])
    cov = fitz.open()
    pg = cov.new_page(width=W, height=H)
    pg.draw_rect(pg.rect, color=None, fill=(0.96, 0.95, 0.92))
    # thumbnail of the original first page
    pix = doc[0].get_pixmap(dpi=100)
    box = fitz.Rect(150, 520, W - 150, H - 230)
    pg.insert_image(box, pixmap=pix, keep_proportion=True)
    pg.draw_rect(fitz.Rect(0, 0, W, 470), color=None, fill=acc)
    pg.insert_textbox(fitz.Rect(70, 50, W - 70, 120), brand["library_name"].upper(),
                      fontsize=34, fontname="helv", color=(1, 1, 1))
    title = meta["title"]
    fs = 66 if len(title) < 40 else 52 if len(title) < 70 else 40
    pg.insert_textbox(fitz.Rect(70, 130, W - 70, 400), title, fontsize=fs,
                      fontname="hebo", color=(1, 1, 1))
    sub = " · ".join(x for x in (meta.get("pubno"), meta.get("date")) if x)
    if sub:
        pg.insert_textbox(fitz.Rect(70, 400, W - 70, 460), sub, fontsize=30,
                          fontname="helv", color=(1, 0.9, 0.85))
    pg.draw_rect(fitz.Rect(0, H - 180, W, H), color=None, fill=(0.12, 0.12, 0.12))
    pg.insert_textbox(fitz.Rect(70, H - 165, W - 70, H - 85), brand["site"],
                      fontsize=44, fontname="hebo", color=(1, 1, 1), align=1)
    pg.insert_textbox(fitz.Rect(70, H - 80, W - 70, H - 20),
                      "Free public-domain edition · Real human support",
                      fontsize=26, fontname="helv", color=(0.85, 0.85, 0.85), align=1)
    pg.get_pixmap(dpi=72).save(str(path))

# ---------------------------------------------------------------- build
CSS = """body{font-family:serif;line-height:1.45}
h1{font-family:sans-serif;page-break-before:always;color:#8c1f1a}
h3,h4{font-family:sans-serif}
figure{margin:1em 0;text-align:center}
figure img{max-width:100%}
figcaption{font-size:.8em;color:#666}
.support{border:3px solid #8c1f1a;padding:1em;margin-top:2em}
.support h2{color:#8c1f1a;margin-top:0}
.notice{font-size:.85em;color:#444}
"""

def front_matter(meta, brand):
    rows = [("Title", meta["title"]), ("Publication", meta.get("pubno")),
            ("Date", meta.get("date")), ("Issued by", meta.get("issuer")),
            ("Official source", meta.get("source_url"))]
    tr = "".join(f"<tr><th>{k}</th><td>{html.escape(v)}</td></tr>" for k, v in rows if v)
    return f"""<h1>About This Edition</h1>
<table>{tr}</table>
<p class="notice">This is a work of the United States Government, approved for public
release, and is in the public domain. {html.escape(brand['library_name'])} reformatted it as a
reflowable e-book from the official PDF. Text was recovered automatically (OCR where the
original was scanned), so tables and figures may not match the original exactly; original
page images are included where a page holds diagrams. Always defer to the official
publication. This edition is not produced or endorsed by the Department of Defense,
any military service or FEMA.</p>"""

def support_page(brand):
    contact = []
    if brand.get("phone"):
        contact.append(f'<p><strong>Call:</strong> {html.escape(brand["phone"])}</p>')
    if brand.get("email"):
        contact.append(f'<p><strong>Email:</strong> {html.escape(brand["email"])}</p>')
    contact.append(f'<p><strong>Web:</strong> <a href="{brand["url"]}">{html.escape(brand["site"])}</a></p>')
    return f"""<h1>Need Help Putting This to Use?</h1>
<div class="support">
<h2>{html.escape(brand['support_heading'])}</h2>
<p>{html.escape(brand['support_body'])}</p>
<p><strong>Get help:</strong> <a href="{brand['support_url']}">{html.escape(brand['support_url'].replace('https://','').rstrip('/'))}</a></p>
{''.join(contact)}
<p>Prebuilt, preconfigured off-grid comms kits: <a href="{brand['kits_url']}">{html.escape(brand['kits_url'].replace('https://',''))}</a></p>
<p>More free manuals: <a href="{brand['url']}/library">{html.escape(brand['site'])}/library</a></p>
</div>"""

def convert(pdf, outdir, meta, brand, do_ocr=True):
    pdf = Path(pdf)
    meta.setdefault("title", pdf.stem.replace("_", " "))
    # EPUB shares the PDF's file name so the libertas.mobi importer can pair them
    name = meta.pop("epub_name", None) or pdf.stem
    work = Path(outdir) / "work" / name
    work.mkdir(parents=True, exist_ok=True)
    epub = Path(outdir) / f"{name}.epub"
    if epub.exists():
        log(f"exists, skipped: {epub}")
        return epub
    src = run_ocr(pdf, work / "ocr.pdf") if do_ocr else pdf
    doc = fitz.open(src)
    log(f"{meta['title']}: {len(doc)} pages")
    chapters = find_chapters(doc)
    log(f"{len(chapters)} chapters")
    rep, bsize = repeating_lines(doc), body_size(doc)
    imgdir = work / "img"
    imgdir.mkdir(exist_ok=True)
    parts = [front_matter(meta, brand)]
    bounds = [p for _, p in chapters[1:]] + [len(doc)]
    for (title, start), end in zip(chapters, bounds):
        parts.append(f"<h1>{html.escape(title)}</h1>")
        for i in range(start, max(start + 1, end)):
            parts.append(page_html(doc, i, rep, bsize, imgdir, "img"))
    parts.append(support_page(brand))
    (work / "book.html").write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>"
        + html.escape(meta["title"]) + "</title></head><body>\n" + "\n".join(parts)
        + "\n</body></html>", encoding="utf-8")
    (work / "style.css").write_text(CSS)
    make_cover(doc, meta, brand, work / "cover.png")
    md = work / "meta.yaml"
    md.write_text("\n".join([
        f"title: {json.dumps(meta['title'])}",
        f"creator: {json.dumps(meta.get('issuer') or 'United States Government')}",
        f"publisher: {json.dumps(brand['library_name'] + ' (' + brand['site'] + ')')}",
        *( [f"date: {json.dumps(meta['date'])}"] if meta.get("date") else [] ),
        f"identifier: {json.dumps(meta.get('source_url') or name)}",
        'rights: "Public domain. U.S. Government work, approved for public release."',
        "lang: en-US",
        f"subject: {json.dumps(meta.get('category') or 'Manuals')}",
    ]) + "\n")
    cmd = ["pandoc", "book.html", "-f", "html", "-t", "epub3", "-o", str(epub.resolve()),
           "--metadata-file", "meta.yaml", "--epub-cover-image", "cover.png",
           "--css", "style.css", "--toc", "--toc-depth=1", "--epub-chapter-level=1",
           "--resource-path", "."]
    r = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("pandoc failed: " + r.stderr[-800:])
    check_epub(epub)
    log(f"done: {epub} ({epub.stat().st_size/1e6:.1f} MB)")
    return epub

def check_epub(epub):
    with zipfile.ZipFile(epub) as z:
        names = z.namelist()
        assert names[0] == "mimetype", "mimetype must be first"
        assert any(n.endswith(".opf") for n in names), "no OPF"
        bad = z.testzip()
        assert bad is None, f"corrupt member {bad}"

def load_brand(p):
    b = dict(DEFAULT_BRAND)
    if p and Path(p).exists():
        b.update(json.loads(Path(p).read_text()))
    return b

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?")
    ap.add_argument("outdir")
    ap.add_argument("--title"); ap.add_argument("--pubno"); ap.add_argument("--date")
    ap.add_argument("--issuer"); ap.add_argument("--source-url"); ap.add_argument("--category")
    ap.add_argument("--brand", default=str(HERE / "brand.json"))
    ap.add_argument("--manifest", help="CSV with file,title,pubno,date,issuer,source_url,category")
    ap.add_argument("--pdf-dir", default=".")
    ap.add_argument("--no-ocr", action="store_true")
    a = ap.parse_args()
    brand = load_brand(a.brand)
    Path(a.outdir).mkdir(parents=True, exist_ok=True)
    if a.manifest:
        ok = fail = 0
        with open(a.manifest, newline="") as f:
            for row in csv.DictReader(f):
                rel = (row.get("file") or row.get("pdf_file") or "").replace("\\", "/")
                pdf = Path(a.pdf_dir) / rel
                if not pdf.exists():
                    log(f"missing: {pdf}"); fail += 1; continue
                meta = {k: v for k, v in row.items() if k not in ("file", "epub_file") and v}
                if row.get("epub_file"):
                    meta["epub_name"] = Path(row["epub_file"]).stem
                try:
                    convert(pdf, a.outdir, meta, brand, not a.no_ocr); ok += 1
                except Exception as e:
                    log(f"FAILED {pdf.name}: {e}"); fail += 1
                    with open(Path(a.outdir) / "failures.log", "a") as fl:
                        fl.write(f"{pdf}\t{e}\n")
        log(f"batch finished: {ok} ok, {fail} failed")
        return
    if not a.input:
        ap.error("give INPUT.pdf or --manifest")
    meta = {k: v for k, v in dict(title=a.title, pubno=a.pubno, date=a.date, issuer=a.issuer,
            source_url=a.source_url, category=a.category).items() if v}
    convert(a.input, a.outdir, meta, brand, not a.no_ocr)

if __name__ == "__main__":
    main()
