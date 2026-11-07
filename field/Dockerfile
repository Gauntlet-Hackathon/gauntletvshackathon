# Field laptop app. Build from the repo root: docker build -f field/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/shared:/app/field:/app/engine \
    FIELD_DATA_DIR=/data \
    DEVICE_ID=laptop-dev-01 \
    DEVICE_TOKEN=dev-token-laptop-dev-01 \
    CLOUD_URL=http://cloud:4000 \
    DEMO_MODE=0 \
    ENGINE_CMD="python -m rescue_engine.fallback" \
    PORT=8000

WORKDIR /app
COPY constraints.txt ./
COPY shared/requirements.txt shared/requirements.txt
COPY field/requirements.txt field/requirements.txt
COPY engine/requirements.txt engine/requirements.txt
RUN pip install --no-cache-dir -c constraints.txt \
    -r shared/requirements.txt -r field/requirements.txt -r engine/requirements.txt

COPY contracts contracts
COPY shared shared
COPY engine engine
COPY field field

RUN mkdir -p /data
VOLUME /data
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn rescue_field.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
