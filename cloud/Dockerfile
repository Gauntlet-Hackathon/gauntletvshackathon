# Build from the repo root (the context must include engine/dist):
#   docker build -f cloud/Dockerfile .
#
# This file is based on these images:
#
#   - https://hub.docker.com/r/hexpm/elixir/tags - for the builder image
#     E.g.: docker.io/hexpm/elixir:1.20.5-erlang-29.1.2-debian-trixie-20261104-slim
#   - https://hub.docker.com/_/debian/tags?name=trixie-20261104-slim - for the runner image
#     E.g.: docker.io/debian:trixie-20261104-slim
#
# Find builder and runner images on Docker Hub or on Hex's Build Server (Bob).
# We recommend using Bob's Web UI to find recent tags:
#
#   - https://bob.hex.pm/docker?repo=hexpm/elixir&os=debian&sort=elixir_version,erlang_version,os_version
#
# We suggest using the same Debian version for both the builder and runner images.
#
# We suggest Debian/Ubuntu instead of Alpine to avoid production compatibility issues
# (such as DNS resolution failures, and dynamically linked NIFs/precompiled binaries).
#
# For finding packages in Debian, search on https://packages.debian.org/.

ARG ELIXIR_VERSION=1.20.5
ARG OTP_VERSION=29.1.2
ARG DEBIAN_VERSION=trixie-20261104-slim
# frozen Mercury engine build replayed by RescueCloud.Replay (no Python needed)
# folder under engine/dist/: the rules_version with ':' as '-' (WINPATH-1)
ARG ENGINE_BUILD=rules@a4dbac566db96b00951930ad9da941e85c1b2429+bin-2a2fac7006832dd7

ARG BUILDER_IMAGE="docker.io/hexpm/elixir:${ELIXIR_VERSION}-erlang-${OTP_VERSION}-debian-${DEBIAN_VERSION}"
ARG RUNNER_IMAGE="docker.io/debian:${DEBIAN_VERSION}"

FROM ${BUILDER_IMAGE} AS builder

# install build dependencies
RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential git \
  && rm -rf /var/lib/apt/lists/*

# prepare build dir
WORKDIR /app

# install hex + rebar
RUN mix local.hex --force \
  && mix local.rebar --force

# set build ENV
ENV MIX_ENV="prod"

# install mix dependencies
COPY cloud/mix.exs cloud/mix.lock ./
RUN mix deps.get --only $MIX_ENV
RUN mkdir config

# copy compile-time config files before we compile dependencies
# to ensure any relevant config change will trigger the dependencies
# to be re-compiled.
COPY cloud/config/config.exs cloud/config/devices.exs cloud/config/${MIX_ENV}.exs config/
RUN mix deps.compile

RUN mix assets.setup

COPY cloud/priv priv

COPY cloud/lib lib

# Compile the release
RUN mix compile

COPY cloud/assets assets

# compile assets
RUN mix assets.deploy

# Changes to config/runtime.exs don't require recompiling the code
COPY cloud/config/runtime.exs config/

COPY cloud/rel rel
RUN mix release

# start a new build stage so that the final image will only contain
# the compiled release and other runtime necessities
FROM ${RUNNER_IMAGE} AS final

# re-declare so the build arg is visible in this stage
ARG ENGINE_BUILD

RUN apt-get update \
  && apt-get install -y --no-install-recommends libstdc++6 openssl libncurses6 locales ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Set the locale
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
  && locale-gen

ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8

WORKDIR "/app"
RUN chown nobody /app

# set runner ENV
ENV MIX_ENV="prod"

# Only copy the final release from the build stage
COPY --from=builder --chown=nobody:root /app/_build/${MIX_ENV}/rel/rescue_cloud ./

# Frozen engine build for the replay verifier
COPY --chown=nobody:root engine/dist/${ENGINE_BUILD}/ /app/engine/dist/${ENGINE_BUILD}/
RUN test -x /app/engine/dist/${ENGINE_BUILD}/bin/mercury_engine \
  && test -f /app/engine/dist/${ENGINE_BUILD}/manifest.json
ENV ENGINE_DIST=/app/engine/dist

USER nobody

# If using an environment that doesn't automatically reap zombie processes, it is
# advised to add an init process such as tini via `apt-get install`
# above and adding an entrypoint. See https://github.com/krallin/tini for details
# ENTRYPOINT ["/tini", "--"]

CMD ["/app/bin/server"]
