# Field laptop app. Build from the repo root: docker build -f field/Dockerfile .
# trixie pinned: the Mercury engine binary needs glibc 2.38+ (bookworm has 2.36).
FROM python:3.12-slim-trixie

# Frozen engine build (H12, #178). ENGINE_BUILD must match the single folder under engine/dist/,
# which is RULES_VERSION with ':' as '-' (WINPATH-1: Windows cannot check out a ':' in a path).
ARG RULES_VERSION=rules@a4dbac566db96b00951930ad9da941e85c1b2429+bin:2a2fac7006832dd7
ARG ENGINE_BUILD=rules@a4dbac566db96b00951930ad9da941e85c1b2429+bin-2a2fac7006832dd7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/shared:/app/field:/app/engine \
    FIELD_DATA_DIR=/data \
    DEVICE_ID=laptop-dev-01 \
    DEVICE_TOKEN=dev-token-laptop-dev-01 \
    CLOUD_URL=http://cloud:4000 \
    DEMO_MODE=0 \
    ENGINE_CMD=/app/engine/dist/${ENGINE_BUILD}/bin/mercury_engine \
    RULES_VERSION=${RULES_VERSION} \
    PORT=8000

WORKDIR /app
COPY constraints.txt ./
COPY shared/requirements.txt shared/requirements.txt
COPY field/requirements.txt field/requirements.txt
COPY engine/requirements.txt engine/requirements.txt
RUN pip install --no-cache-dir -c constraints.txt \
    -r shared/requirements.txt -r field/requirements.txt -r engine/requirements.txt

# Optional trained model (F32). Only when BOTH args are set: CPU torch + model deps, weights downloaded at build time.
# HF_TOKEN comes from a build secret (never an ARG/ENV), so it is not written into any layer.
ARG HF_REPO=""
ARG HF_REVISION=""
COPY field/requirements-model.txt field/requirements-model.txt
RUN --mount=type=secret,id=HF_TOKEN \
    if [ -n "$HF_REPO" ] && [ -n "$HF_REVISION" ]; then \
      echo "$HF_REVISION" | grep -Eq '^[0-9a-f]{40}$' || { echo "HF_REVISION must be a 40-hex commit SHA" >&2; exit 1; }; \
      pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu -c constraints.txt torch \
      && pip install --no-cache-dir -c constraints.txt -r field/requirements-model.txt \
      && if [ -f /run/secrets/HF_TOKEN ]; then export HF_TOKEN="$(cat /run/secrets/HF_TOKEN)"; fi \
      && python -c "from huggingface_hub import snapshot_download; snapshot_download('$HF_REPO', revision='$HF_REVISION', local_dir='/models/current')"; \
    fi
ENV HF_REPO=$HF_REPO HF_REVISION=$HF_REVISION HF_MODEL_DIR=/models/current HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

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
