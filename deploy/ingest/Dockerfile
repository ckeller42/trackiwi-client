# trackiwi ingest loop. Build context is the repository root (see docker-compose.yml).
FROM python:3.13-slim
RUN useradd --create-home --uid 10001 trackiwi
COPY . /src
RUN pip install --no-cache-dir /src && rm -rf /src
USER trackiwi
ENV HOME=/home/trackiwi
# A failed run (network down, InfluxDB restarting) must not stop the loop:
# the cache buffers positions and the next run catches up.
CMD ["sh", "-c", "while true; do trackiwi ingest || true; sleep \"${TRACKIWI_INGEST_INTERVAL:-600}\"; done"]
