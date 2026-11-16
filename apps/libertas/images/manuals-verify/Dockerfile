FROM docker.io/library/python:3.12-slim
COPY verify.py /app/
USER 1000:1000
ENV DATA=/data PYTHONUNBUFFERED=1
CMD ["python", "-I", "/app/verify.py"]
