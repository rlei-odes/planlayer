# Planlayer image: the API serves the built frontend (ADR 0031).

# The built frontend is the same on every platform: built once, natively.
FROM --platform=$BUILDPLATFORM node:24-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv

# The Postgres 18 client tools from the PostgreSQL project's repository (Debian's are older):
# pg_dump and pg_restore for backups (ADR 0032), psql for the install script in the bundled
# database and the demo. Major version pinned; minor releases are bug and security fixes.
ADD https://www.postgresql.org/media/keys/ACCC4CF8.asc /usr/share/keyrings/pgdg.asc
RUN chmod 644 /usr/share/keyrings/pgdg.asc \
 && . /etc/os-release \
 && echo "deb [signed-by=/usr/share/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-18 \
 && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY backend/src ./src
RUN uv sync --frozen --no-dev --no-editable

# The Nordwerk test warehouse generator, for the demo (gen-testdata). Its dependencies are the
# backend's, at the same pinned versions, so it is installed without resolving any.
COPY tools/gen_testdata /tmp/gen_testdata
RUN uv pip install --python /app/.venv/bin/python --no-deps /tmp/gen_testdata \
 && rm -rf /tmp/gen_testdata

# The DBA's install script, and the bundled database's and the demo's first start, which runs it.
COPY deploy/sql/install.sql deploy/bundled-init.sh /app/deploy/

# Where backups go (ADR 0032); a volume mounted here takes this owner.
RUN mkdir /backups

COPY --from=frontend /src/dist /app/frontend
ENV PLANLAYER_FRONTEND_DIST=/app/frontend

RUN useradd --system --uid 10001 planlayer && chown planlayer /backups
USER planlayer

EXPOSE 8000
CMD ["planlayer", "serve"]
