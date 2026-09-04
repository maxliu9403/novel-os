FROM node:22-bookworm-slim AS codex-cli

ARG CODEX_VERSION=0.153.0
RUN npm install --global "@openai/codex@${CODEX_VERSION}"


FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Keep the production API on the same authenticated Codex transport as local
# development. The Compose service mounts only auth.json and config.toml.
COPY --from=codex-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-cli /usr/local/lib/node_modules/@openai/codex /usr/local/lib/node_modules/@openai/codex
RUN ln -s ../lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && codex --version

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY core ./core
COPY agents ./agents
COPY templates ./templates
COPY docker-entrypoint.sh /usr/local/bin/novel-os-entrypoint
RUN chmod +x /usr/local/bin/novel-os-entrypoint

EXPOSE 8000

ENTRYPOINT ["novel-os-entrypoint"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
