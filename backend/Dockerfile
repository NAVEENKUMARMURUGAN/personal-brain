FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY . .

# Railway injects $PORT; fall back to 8000 for local Docker
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
