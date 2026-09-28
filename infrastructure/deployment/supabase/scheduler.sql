-- Run once in the Supabase SQL editor after deploying the API (production project only).
-- Calls the protected runner every minute. The scheduler secret lives in Supabase Vault,
-- never in this file or in job payloads. Replace the URL with your API deployment URL.
create extension if not exists pg_cron;
create extension if not exists pg_net;

select vault.create_secret('<SCHEDULER_SECRET from your API environment>', 'oneledger_scheduler_secret');

select cron.schedule(
  'oneledger-runner',
  '* * * * *',
  $$
  select net.http_post(
    url := 'https://<your-api-project>.vercel.app/api/internal/runner',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'oneledger_scheduler_secret'),
      'Content-Type', 'application/json'
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 55000
  );
  $$
);
