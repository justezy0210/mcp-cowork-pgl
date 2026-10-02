FROM node:24-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY --from=web /app/src/cowork_hub/web/dist ./src/cowork_hub/web/dist
RUN uv sync --frozen --extra web --extra postgres --no-dev --no-editable --no-cache \
    && groupadd --gid 10001 hub \
    && useradd --uid 10001 --gid hub --no-create-home hub \
    && mkdir /data && chown hub:hub /data && chmod 700 /data
ENV PATH="/app/.venv/bin:$PATH" HUB_DATA_DIR="/data" PYTHONUNBUFFERED="1"
USER hub
EXPOSE 8080
ENTRYPOINT ["cowork-hub"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
