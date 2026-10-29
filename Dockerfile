# syntax=docker/dockerfile:1

# ---- build: install dependencies into an isolated virtualenv
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install -r requirements.txt

# ---- runtime: slim image, non-root user, no build tools
FROM python:3.12-slim
ENV PATH="/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/clinic.db \
    LOG_JSON=true \
    PORT=8000
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
COPY --from=build /venv /venv
WORKDIR /srv
COPY --chown=app app ./app
COPY --chown=app static ./static
USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=2)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
