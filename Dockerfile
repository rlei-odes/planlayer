# Planlayer image: the API serves the built frontend (D-25).

FROM node:24-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY backend/src ./src
RUN uv sync --frozen --no-dev --no-editable

COPY --from=frontend /src/dist /app/frontend
ENV PLANLAYER_FRONTEND_DIST=/app/frontend

RUN useradd --system --uid 10001 planlayer
USER planlayer

EXPOSE 8000
CMD ["planlayer", "serve"]
