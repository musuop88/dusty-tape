FROM python:3.11-slim
# Premium API - no ads, global
RUN apt-get update && apt-get install -y --no-install-recommends nodejs ffmpeg curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
# yt-dlp is installed via pip, ensure node runtime available
ENV PORT=10000
ENV YTDLP=yt-dlp
ENV PYTHONUNBUFFERED=1
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD curl -f http://localhost:$PORT/health || exit 1
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 2 --threads 8 --timeout 180 --keep-alive 30 server:app"]
