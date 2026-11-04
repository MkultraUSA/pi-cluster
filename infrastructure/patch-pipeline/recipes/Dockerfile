FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*
COPY actuate.py publish.py kev-poll.py /actuator/
CMD ["python3", "/actuator/actuate.py"]
