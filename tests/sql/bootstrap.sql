-- Exclusivo do PostgreSQL descartável do CI: nunca executar no projeto Supabase.
\set ON_ERROR_STOP on
do $$begin
  if exists(select 1 from pg_namespace where nspname='auth') then
    raise exception 'Bootstrap de teste recusado: schema auth já existe.';
  end if;
end $$;
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create schema auth;
create table auth.users (
  id uuid primary key,
  email text unique,
  email_confirmed_at timestamptz,
  raw_user_meta_data jsonb not null default '{}'
);
create table auth.sessions (
  id uuid primary key,
  user_id uuid not null references auth.users(id),
  not_after timestamptz
);
create function auth.jwt() returns jsonb language sql stable
as $$select coalesce(nullif(current_setting('request.jwt.claims',true),'')::jsonb,'{}'::jsonb)$$;
create function auth.uid() returns uuid language sql stable
as $$select (auth.jwt()->>'sub')::uuid$$;
grant usage on schema auth to anon,authenticated;
grant execute on function auth.jwt(),auth.uid() to anon,authenticated;
