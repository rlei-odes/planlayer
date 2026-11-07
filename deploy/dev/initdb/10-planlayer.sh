#!/bin/sh
# Development database, mirroring the typical installation (T-14): Planlayer's app store in its
# own database, the warehouse (source views and published output) in "dwh". Run once by the
# postgres image on first start.
set -eu

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres <<'SQL'
CREATE DATABASE planlayer;
CREATE DATABASE dwh;
SQL

# The simulated warehouse (TD-10): raw ERP-style tables in bronze, contract views in gold.
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d dwh <<'SQL'
CREATE SCHEMA bronze;
CREATE SCHEMA gold;
SQL

psql -U "$POSTGRES_USER" -d postgres \
  -v app_db=planlayer -v output_db=dwh -v source_db=dwh -v source_schema=gold \
  -v app_password=planlayer \
  -f /planlayer/install.sql
