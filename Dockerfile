FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY src/backend/requirements.txt src/backend/requirements.txt
RUN pip install --no-cache-dir -r src/backend/requirements.txt \
    && useradd --uid 10001 --create-home app

COPY --chown=app:app src/backend/ src/backend/
COPY --chown=app:app src/frontend/ src/frontend/
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)"
CMD ["python", "-m", "uvicorn", "src.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
