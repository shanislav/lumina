FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# ffmpeg (audio analysis, preview clips): a pinned static build — the audio sync's thresholds are tuned on its
# decoding, and tests run locally on the same version
COPY --from=mwader/static-ffmpeg:9.0.2 /ffmpeg /ffprobe /usr/local/bin/
# MediaInfo (file details), mkvtoolnix (muxing audio tracks)
RUN apt-get update && apt-get install -y --no-install-recommends libmediainfo0v5 mkvtoolnix \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY app/ app/
RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
