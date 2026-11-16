# Libertas Library EPUB converter. Builds for arm64 (Pi) or amd64.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      ocrmypdf tesseract-ocr tesseract-ocr-eng pandoc python3 python3-pip ca-certificates \
 && pip3 install --no-cache-dir --break-system-packages "pymupdf>=1.24" \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY libertas_epub.py brand.json /app/
RUN useradd -u 1000 -m conv
USER 1000
ENTRYPOINT ["python3", "/app/libertas_epub.py"]
