-- Planlayer install script (T-14, D-25).
--
-- Creates Planlayer's roles and schemas in existing databases and grants read access to the
-- source views. Planlayer itself never creates schemas or roles; review this script, then run it
-- as a superuser (or a role with CREATEROLE and CREATE on the databases involved):
--
--   psql -v app_db=planlayer -v output_db=dwh -v source_db=dwh -v source_schema=gold \
--        -v app_password='...' -f install.sql
--
-- Variables (defaults in brackets):
--   app_db         database for the app store                        (required)
--   output_db      database for the published output                 [app_db]
--   source_db      database holding the source views                 [app_db]
--   source_schema  schema holding the source views; unset: the source's DBA grants read
--                  access on their side (a warehouse on another server)  [none]
--   app_schema     app store schema                                  [planlayer_app]
--   output_schema  published output schema                           [planlayer_out]
--   app_role       login role Planlayer connects as                  [planlayer]
--   app_password   password of app_role                              (required)
--   bi_role        group role for BI readers of the output           [planlayer_bi]
--   backup_role    login role the backup reads the app store as      [planlayer_backup]
--   backup_password  password of backup_role; unset: no backup role  (optional)
--
-- The backup role (ADR 0032) has BYPASSRLS, which only a superuser can grant: the app role is
-- held to its own row-level security, so only such a role sees every row. It reads and nothing
-- else.
--
-- One database for everything: set only app_db (and source_schema).

\set ON_ERROR_STOP on

\if :{?output_db}
\else
  \set output_db :app_db
\endif
\if :{?source_db}
\else
  \set source_db :app_db
\endif
\if :{?app_schema}
\else
  \set app_schema planlayer_app
\endif
\if :{?output_schema}
\else
  \set output_schema planlayer_out
\endif
\if :{?app_role}
\else
  \set app_role planlayer
\endif
\if :{?bi_role}
\else
  \set bi_role planlayer_bi
\endif
\if :{?backup_role}
\else
  \set backup_role planlayer_backup
\endif

-- Roles ----------------------------------------------------------------------------------------
CREATE ROLE :"app_role" LOGIN PASSWORD :'app_password';
CREATE ROLE :"bi_role" NOLOGIN;

-- App store: owned by Planlayer, invisible to everyone else ---------------------------------------
\connect :app_db
GRANT CONNECT ON DATABASE :"app_db" TO :"app_role";
CREATE SCHEMA :"app_schema" AUTHORIZATION :"app_role";
REVOKE ALL ON SCHEMA :"app_schema" FROM PUBLIC;
\if :{?backup_password}
CREATE ROLE :"backup_role" LOGIN BYPASSRLS PASSWORD :'backup_password';
GRANT CONNECT ON DATABASE :"app_db" TO :"backup_role";
GRANT USAGE ON SCHEMA :"app_schema" TO :"backup_role";
-- The tables and sequences the app role's migrations create.
ALTER DEFAULT PRIVILEGES FOR ROLE :"app_role" IN SCHEMA :"app_schema"
  GRANT SELECT ON TABLES TO :"backup_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"app_role" IN SCHEMA :"app_schema"
  GRANT SELECT ON SEQUENCES TO :"backup_role";
\endif

-- Published output: written by Planlayer, read by BI ---------------------------------------------
\connect :output_db
GRANT CONNECT ON DATABASE :"output_db" TO :"app_role";
CREATE SCHEMA :"output_schema" AUTHORIZATION :"app_role";
REVOKE ALL ON SCHEMA :"output_schema" FROM PUBLIC;
GRANT USAGE ON SCHEMA :"output_schema" TO :"bi_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"app_role" IN SCHEMA :"output_schema"
  GRANT SELECT ON TABLES TO :"bi_role";

-- Source: read only -------------------------------------------------------------------------------
\if :{?source_schema}
\connect :source_db
GRANT CONNECT ON DATABASE :"source_db" TO :"app_role";
GRANT USAGE ON SCHEMA :"source_schema" TO :"app_role";
GRANT SELECT ON ALL TABLES IN SCHEMA :"source_schema" TO :"app_role";
-- Covers views created later by the role running this script; views created by other roles need
-- their own GRANT SELECT.
ALTER DEFAULT PRIVILEGES IN SCHEMA :"source_schema" GRANT SELECT ON TABLES TO :"app_role";
\endif
