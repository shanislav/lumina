FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# MediaInfo (file details), ffmpeg (audio analysis, preview clips), mkvtoolnix (muxing audio tracks)
RUN apt-get update && apt-get install -y --no-install-recommends libmediainfo0v5 ffmpeg mkvtoolnix \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY app/ app/
RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
