#!/bin/sh
# The bundled Postgres's first start (deploy/compose.yaml, deploy/demo/compose.yaml): what a DBA
# does with install.sql, done once. Runs in the Planlayer image as the database's superuser
# (PGHOST, PGUSER, PGPASSWORD). The app role's and the backup role's passwords are taken from
# PLANLAYER_APP_DATABASE_URL and PLANLAYER_BACKUP_DATABASE_URL, so they are set in one place.
#
#   bundled-init.sh          app store and output in one database; the source is elsewhere
#   bundled-init.sh --demo   also the Nordwerk test warehouse (schemas bronze and gold), the
#                            migrations and the demo's data (planlayer seed-demo)
set -eu

mode=${1:-}
part() {
  python -c 'import sys; from sqlalchemy import make_url; print(getattr(make_url(sys.argv[1]), sys.argv[2]) or "")' "$1" "$2"
}
db=$(part "$PLANLAYER_APP_DATABASE_URL" database)

if [ -n "$(psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$db'")" ]; then
  echo "database $db exists; nothing to do"
  exit 0
fi
trap 'echo "first start failed: remove the database volume (docker compose down -v) and start again" >&2' EXIT

set -- -q -v ON_ERROR_STOP=1 -d postgres -v app_db="$db" \
  -v app_role="$(part "$PLANLAYER_APP_DATABASE_URL" username)" \
  -v app_password="$(part "$PLANLAYER_APP_DATABASE_URL" password)"
if [ -n "${PLANLAYER_BACKUP_DATABASE_URL:-}" ]; then
  set -- "$@" -v backup_role="$(part "$PLANLAYER_BACKUP_DATABASE_URL" username)" \
    -v backup_password="$(part "$PLANLAYER_BACKUP_DATABASE_URL" password)"
fi

psql -v ON_ERROR_STOP=1 -d postgres -c "CREATE DATABASE \"$db\""
if [ "$mode" = "--demo" ]; then
  psql -v ON_ERROR_STOP=1 -d "$db" -c "CREATE SCHEMA bronze" -c "CREATE SCHEMA gold"
  gen-testdata --db "postgresql://$PGUSER:$PGPASSWORD@$PGHOST/$db" --variant standard
  set -- "$@" -v source_schema=gold
fi
psql "$@" -f /app/deploy/install.sql
echo "database $db created with the install script"

if [ "$mode" = "--demo" ]; then
  planlayer migrate
  planlayer seed-demo
fi
trap - EXIT
