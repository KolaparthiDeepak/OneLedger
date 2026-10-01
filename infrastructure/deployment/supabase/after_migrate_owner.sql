-- Run as the table owner (the migration login, oneledger_migrator) after after_migrate_postgres.sql,
-- e.g. psql "$MIGRATION_DATABASE_URL" -f infrastructure/deployment/supabase/after_migrate_owner.sql
-- (strip the "+psycopg" from the URL for psql). Only a table's owner can grant on it; a grant run
-- as postgres is silently ignored ("no privileges were granted").
-- The functions now owned by postgres read users, sessions, api_tokens and invites (postgres can
-- already read them on Supabase) and write these two tables:
grant update on oneledger.jobs, oneledger.invites to postgres;  -- claim_jobs, invite_consume

-- Expect t | t.
select has_table_privilege('postgres', 'oneledger.jobs', 'UPDATE'),
       has_table_privilege('postgres', 'oneledger.invites', 'UPDATE');
