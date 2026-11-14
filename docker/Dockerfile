# Multi-stage production Dockerfile
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


FROM python:3.12-slim AS runtime

WORKDIR /app

# Create non-root user
RUN useradd --create-home --shell /bin/bash trader \
    && mkdir -p /app/data /app/logs /app/config \
    && chown -R trader:trader /app

# Copy installed packages from builder
COPY --from=builder /root/.local /home/trader/.local
ENV PATH=/home/trader/.local/bin:$PATH

# Copy application code
COPY --chown=trader:trader . .

USER trader

# Healthcheck – Prometheus metrics endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/metrics')" || exit 1

EXPOSE 8000

CMD ["python", "main.py"]
