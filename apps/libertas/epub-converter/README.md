# Libertas Library EPUB pipeline

Official PDF -> OCR (only if pages are scanned) -> reflowable EPUB 3 with a branded cover,
chapter table of contents, an "About this edition" public-domain notice, and a closing
libertas.mobi page: "Questions? Call Kevin, a real person."

## Install (once per Pi node)
    bash setup_cluster.sh

## One manual
    python3 libertas_epub.py manual.pdf OUTDIR --title "Radio Operator's Handbook" \
      --pubno "MCRP 3-40.3B" --date 2016 --issuer "U.S. Marine Corps" --source-url URL

## A batch (uses the download thread's manifest)
    python3 libertas_epub.py --manifest manifest.csv --pdf-dir PDFDIR OUTDIR
Manifest columns: file,title,pubno,date,issuer,source_url,category (extra columns ignored).
Finished books are skipped on re-run; failures go to OUTDIR/failures.log.

## Branding
Edit brand.json (phone, email, URLs). Leave phone empty until Kevin picks a business number.

Chapters come from the PDF's bookmarks, else "CHAPTER n" headings, else 15-page parts.
Pages with diagrams also get a picture of the original page so figures are not lost.
Validated with EPUBCheck 5 (0 errors) on a test manual.
