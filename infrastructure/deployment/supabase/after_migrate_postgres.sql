-- Run in the Supabase SQL editor (as postgres) after every `alembic upgrade head`.
-- OneLedger's SECURITY DEFINER functions (sign-in, session and token lookup, invites, the
-- scheduler's job claim) must bypass row-level security, as they do locally where the migration
-- user is a superuser. On Supabase the migration login (oneledger_migrator) cannot bypass RLS,
-- so the functions are handed to postgres, which can. Safe to re-run.
grant oneledger_migrator to postgres;  -- needed to take over objects owned by the migration login
do $$
declare f regprocedure;
begin
  for f in
    select p.oid::regprocedure
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'oneledger' and p.prosecdef
  loop
    execute format('alter function %s owner to postgres', f);
  end loop;
end $$;
revoke oneledger_migrator from postgres;

-- Expect every row to say postgres.
select p.proname, pg_get_userbyid(p.proowner) as owner
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'oneledger' and p.prosecdef
order by 1;
