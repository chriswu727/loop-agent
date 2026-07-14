# Ephemeral per-task sandbox image. Commands the agent runs execute here — not on
# the host — with only the task workspace mounted and (by default) no network.
FROM python:3.12-slim

COPY apps/api/sandbox-requirements.lock /tmp/sandbox-requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/sandbox-requirements.lock \
    && useradd --create-home --uid 10001 sandbox

USER sandbox
WORKDIR /workspace
