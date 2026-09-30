FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PADDLE_PDX_CPU_NUM_THREADS=4

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY backend/requirements.txt backend/constraints.txt /app/backend/
RUN pip install --no-cache-dir --no-deps -r /app/backend/requirements.txt -c /app/backend/constraints.txt

COPY backend /app/backend
COPY config /app/config
COPY .env.example /app/.env.example

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
