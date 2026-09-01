FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home appuser

COPY pyproject.toml README.md LICENSE /app/
COPY ioc_scraper /app/ioc_scraper

RUN pip install --no-cache-dir .[dashboard] \
    && python -m playwright install --with-deps chromium \
    && mkdir -p /app/data /opt/ms-playwright \
    && chmod -R a+rX /opt/ms-playwright \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')"

CMD ["ioc-scraper", "dashboard", "--host", "0.0.0.0", "--port", "8080"]
