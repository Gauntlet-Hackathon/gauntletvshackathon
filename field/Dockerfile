# Field laptop app. Build from the repo root: docker build -f field/Dockerfile .
# trixie pinned: the Mercury engine binary needs glibc 2.38+ (bookworm has 2.36).
FROM python:3.12-slim-trixie

# Frozen engine build (H12, #178); must match the single folder under engine/dist/.
ARG ENGINE_BUILD=rules@a4dbac566db96b00951930ad9da941e85c1b2429+bin:36c2f7f7da6998e5

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/shared:/app/field:/app/engine \
    FIELD_DATA_DIR=/data \
    DEVICE_ID=laptop-dev-01 \
    DEVICE_TOKEN=dev-token-laptop-dev-01 \
    CLOUD_URL=http://cloud:4000 \
    DEMO_MODE=0 \
    ENGINE_CMD=/app/engine/dist/${ENGINE_BUILD}/bin/mercury_engine \
    RULES_VERSION=${ENGINE_BUILD} \
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
# Only the frozen build ships; the engine source is not needed once ENGINE_CMD is the binary.
COPY engine/dist/${ENGINE_BUILD}/ /app/engine/dist/${ENGINE_BUILD}/
RUN test -f /app/engine/dist/${ENGINE_BUILD}/manifest.json \
 && grep -q '"engine_kind": "mercury"' /app/engine/dist/${ENGINE_BUILD}/manifest.json
COPY field field

RUN mkdir -p /data
VOLUME /data
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn rescue_field.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
