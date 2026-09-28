-- Run after `alembic upgrade head` (which creates the NOLOGIN group role oneledger_app).
-- Creates the restricted login role used by DATABASE_URL. No BYPASSRLS, no superuser.
create role oneledger_runtime login password '<generate a long random password>'
  nosuperuser nobypassrls nocreaterole nocreatedb;
grant oneledger_app to oneledger_runtime;
-- The oneledger schema is private: do NOT add it to Supabase API "Exposed schemas".
