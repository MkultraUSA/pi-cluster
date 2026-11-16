"""manuals-verify: check every file in manifest.csv against its sha256. Read-only. Exit 1 on any problem."""
import csv, hashlib, os, sys

DATA = os.environ.get("DATA", "/data")
ok, bad, missing = 0, [], []
rows = list(csv.DictReader(open(os.path.join(DATA, "manifest.csv"), encoding="utf-8")))
listed = set()
for r in rows:
    p = os.path.join(DATA, r["file"])
    listed.add(os.path.normpath(r["file"]))
    if not os.path.exists(p):
        missing.append(r["file"]); continue
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() == r["sha256"]:
        ok += 1
    else:
        bad.append(r["file"])
unlisted = []
for root, dirs, files in os.walk(DATA):
    dirs[:] = [d for d in dirs if not d.startswith(".") and d != "epub"]
    for fn in files:
        if fn.lower().endswith(".pdf"):
            rel = os.path.normpath(os.path.relpath(os.path.join(root, fn), DATA))
            if rel not in listed:
                unlisted.append(rel)
print(f"manifest rows: {len(rows)}  OK: {ok}  MISMATCH: {len(bad)}  MISSING: {len(missing)}  PDFs not in manifest: {len(unlisted)}")
for x in bad: print("MISMATCH", x)
for x in missing: print("MISSING", x)
for x in unlisted: print("UNLISTED", x)
sys.exit(1 if (bad or missing) else 0)
