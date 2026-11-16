FROM docker.io/library/python:3.12-slim
COPY app.py /app/
USER 1000:1000
ENV DATA=/data PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-I", "/app/app.py"]
