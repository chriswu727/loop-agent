# Ephemeral per-task sandbox image. Commands the agent runs execute here — not on
# the host — with only the task workspace mounted and (by default) no network.
FROM python:3.12-slim

# Office/data libraries so in-container document editing matches the inline path.
RUN pip install --no-cache-dir openpyxl>=3.1 python-docx>=1.1 pandas>=2.2 \
    && useradd --create-home --uid 10001 sandbox

USER sandbox
WORKDIR /workspace
