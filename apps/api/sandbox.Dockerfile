# Ephemeral per-task sandbox image. Commands the agent runs execute here — not on
# the host — with only the task workspace mounted and (by default) no network.
FROM python:3.12-slim

ARG SANDBOX_UID=10001
ARG SANDBOX_GID=10001

COPY apps/api/sandbox-requirements.lock /tmp/sandbox-requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/sandbox-requirements.lock \
    && sandbox_group="$(getent group "${SANDBOX_GID}" | cut -d: -f1)" \
    && if [ -z "${sandbox_group}" ]; then groupadd --gid "${SANDBOX_GID}" sandbox; sandbox_group=sandbox; fi \
    && useradd --create-home --uid "${SANDBOX_UID}" --gid "${sandbox_group}" sandbox

USER sandbox
WORKDIR /workspace
