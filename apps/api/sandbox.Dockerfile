# Ephemeral per-task sandbox image. Commands the agent runs execute here — not on
# the host — with only the task workspace mounted and (by default) no network.
FROM node:22-bookworm-slim AS node-runtime

FROM python:3.12-slim

ARG SANDBOX_UID=10001
ARG SANDBOX_GID=10001

COPY apps/api/sandbox-requirements.lock /tmp/sandbox-requirements.lock
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global pnpm@11.13.0 \
    && pip install --no-cache-dir --require-hashes -r /tmp/sandbox-requirements.lock \
    && sandbox_group="$(getent group "${SANDBOX_GID}" | cut -d: -f1)" \
    && if [ -z "${sandbox_group}" ]; then groupadd --gid "${SANDBOX_GID}" sandbox; sandbox_group=sandbox; fi \
    && useradd --create-home --uid "${SANDBOX_UID}" --gid "${sandbox_group}" sandbox

USER sandbox
WORKDIR /workspace
