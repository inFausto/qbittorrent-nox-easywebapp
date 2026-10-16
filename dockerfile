FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8083

WORKDIR /app
COPY torrent_web.py .

# non-root user
RUN useradd --system --uid 1000 --no-create-home app
USER app

EXPOSE 8083

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import os,socket; socket.create_connection(('127.0.0.1', int(os.environ.get('WEB_PORT','8083'))), 3)"

CMD ["python3", "torrent_web.py"]
