FROM docker.io/library/python:3.12-slim
RUN pip install --no-cache-dir pypdf==5.1.0
COPY fetcher.py /app/
USER 1000:1000
ENV DATA=/data TARGETS=/config/targets.json PYTHONUNBUFFERED=1
CMD ["python", "-I", "/app/fetcher.py"]
