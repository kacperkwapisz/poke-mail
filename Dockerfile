FROM python:3.13-slim AS base

# Prevent bytecode + enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps in a separate layer for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ src/

# Non-root user
RUN adduser --disabled-password --no-create-home appuser
USER appuser

EXPOSE 3000

ENV PORT=3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:3000/mcp', timeout=5); assert r.status_code == 405" || exit 1

CMD ["python", "src/server.py"]
