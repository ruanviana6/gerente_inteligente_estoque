-- Sessão de navegador: apenas um identificador aleatório sai do backend.
-- Tokens Supabase ficam cifrados e sem acesso direto por qualquer papel da API.
create table private.persistent_sessions (
  selector uuid primary key,
  organization_id uuid not null references public.organizations(id),
  user_id uuid not null references public.app_users(user_id),
  auth_session_id uuid not null,
  validator_hash text not null check(validator_hash ~ '^[0-9a-f]{64}$'),
  encrypted_session text not null check(octet_length(encrypted_session)<=24000),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default now()+interval '30 days',
  revoked_at timestamptz,
  claim_token uuid,
  lease_until timestamptz,
  last_used_at timestamptz
);
create index persistent_sessions_user on private.persistent_sessions(user_id,expires_at);
alter table private.persistent_sessions enable row level security;
revoke all on private.persistent_sessions from public,anon,authenticated,service_role;

create function private.persistent_backend(p_backend_secret text) returns uuid
language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  if p_backend_secret is null or length(p_backend_secret) not between 32 and 512 then
    raise exception 'Acesso lembrado não autorizado.' using errcode='42501'; end if;
  select organization_id into org from private.backend_credentials
    where secret_hash=encode(sha256(convert_to(p_backend_secret,'UTF8')),'hex');
  if org is null then raise exception 'Acesso lembrado não autorizado.' using errcode='42501'; end if;
  return org;
end $$;
revoke all on function private.persistent_backend(text) from public,anon,authenticated,service_role;

create function private.persistent_valid(p private.persistent_sessions) returns boolean
language sql stable security definer set search_path='' as $$
  select p.revoked_at is null and p.expires_at>now() and exists(
    select 1 from public.app_users a join auth.sessions s on s.user_id=a.user_id
    left join private.account_access_controls g on g.user_id=a.user_id
    where a.user_id=p.user_id and a.organization_id=p.organization_id and a.active
      and s.id=p.auth_session_id and (s.not_after is null or s.not_after>now())
      and not coalesce(g.pending,false) and (g.revoked_before is null or s.created_at>g.revoked_before))
$$;
revoke all on function private.persistent_valid(private.persistent_sessions) from public,anon,authenticated,service_role;

create function private.persistent_register(p_selector uuid,p_validator_hash text,p_encrypted_session text,p_backend_secret text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; sid uuid;
begin
  org := private.current_organization();
  if org is null or not private.session_valid() or private.persistent_backend(p_backend_secret)<>org then
    raise exception 'Acesso lembrado não autorizado.' using errcode='42501'; end if;
  if p_selector is null or p_validator_hash is null or p_validator_hash !~ '^[0-9a-f]{64}$'
    or p_encrypted_session is null or octet_length(p_encrypted_session) not between 40 and 24000 then
    raise exception 'Sessão inválida.' using errcode='22023'; end if;
  sid := (auth.jwt()->>'session_id')::uuid;
  perform pg_advisory_xact_lock(hashtextextended('persistent:'||auth.uid()::text,0));
  if (select count(*) from private.persistent_sessions p where p.user_id=auth.uid() and private.persistent_valid(p))>=10
    or (select count(*) from private.persistent_sessions where organization_id=org)>=2000 then
    raise exception 'Limite de navegadores lembrados atingido.' using errcode='54000'; end if;
  insert into private.persistent_sessions(selector,organization_id,user_id,auth_session_id,validator_hash,encrypted_session)
    values(p_selector,org,auth.uid(),sid,p_validator_hash,p_encrypted_session);
  return jsonb_build_object('saved',true);
end $$;

-- A concessão serializa a rotação do refresh token entre abas e instâncias.
create function private.persistent_claim(p_selector uuid,p_validator text,p_backend_secret text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; p private.persistent_sessions; token uuid;
begin
  org := private.persistent_backend(p_backend_secret);
  if p_validator is null or p_validator !~ '^[A-Za-z0-9_-]{43}$' then
    raise exception 'Acesso lembrado não autorizado.' using errcode='42501'; end if;
  select * into p from private.persistent_sessions where selector=p_selector and organization_id=org for update;
  if p.selector is null or p.validator_hash<>encode(sha256(convert_to(p_validator,'UTF8')),'hex') or not private.persistent_valid(p) then
    raise exception 'Acesso lembrado não autorizado.' using errcode='42501'; end if;
  if p.lease_until>now() then return jsonb_build_object('busy',true); end if;
  token := gen_random_uuid();
  update private.persistent_sessions set claim_token=token,lease_until=now()+interval '30 seconds',last_used_at=now()
    where selector=p.selector;
  return jsonb_build_object('claim',token,'encrypted_session',p.encrypted_session,'organization_id',org,'user_id',p.user_id);
end $$;

create function private.persistent_finish(p_selector uuid,p_claim uuid,p_encrypted_session text,p_backend_secret text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; p private.persistent_sessions;
begin
  org := private.persistent_backend(p_backend_secret);
  select * into p from private.persistent_sessions where selector=p_selector and organization_id=org for update;
  if p.selector is null or p.claim_token is distinct from p_claim or p_claim is null or p.lease_until<=now()
    or not private.persistent_valid(p) or not private.session_valid() or p.user_id is distinct from auth.uid()
    or p.auth_session_id is distinct from (auth.jwt()->>'session_id')::uuid then
    raise exception 'Acesso lembrado não autorizado.' using errcode='42501'; end if;
  if p_encrypted_session is not null and octet_length(p_encrypted_session) not between 40 and 24000 then
    raise exception 'Sessão inválida.' using errcode='22023'; end if;
  update private.persistent_sessions set encrypted_session=coalesce(p_encrypted_session,encrypted_session),
    claim_token=null,lease_until=null where selector=p.selector;
  return jsonb_build_object('saved',true);
end $$;

create function private.persistent_release(p_selector uuid,p_claim uuid,p_revoke boolean,p_backend_secret text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid;
begin
  org := private.persistent_backend(p_backend_secret);
  if p_claim is null or p_revoke is null then raise exception 'Concessão inválida.' using errcode='22023'; end if;
  update private.persistent_sessions set claim_token=null,lease_until=null,
    revoked_at=case when p_revoke then now() else revoked_at end
    where selector=p_selector and organization_id=org and claim_token=p_claim;
  return jsonb_build_object('released',true);
end $$;

create function private.persistent_forget(p_selector uuid,p_validator text,p_backend_secret text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid;
begin
  org := private.persistent_backend(p_backend_secret);
  if p_validator is null or p_validator !~ '^[A-Za-z0-9_-]{43}$' then
    raise exception 'Acesso lembrado não autorizado.' using errcode='42501'; end if;
  update private.persistent_sessions set revoked_at=now(),claim_token=null,lease_until=null
    where selector=p_selector and organization_id=org
      and validator_hash=encode(sha256(convert_to(p_validator,'UTF8')),'hex');
  return jsonb_build_object('forgotten',true);
end $$;

create function public.persistent_register(p_selector uuid,p_validator_hash text,p_encrypted_session text,p_backend_secret text)
returns jsonb language sql security invoker set search_path=''
as $$select private.persistent_register(p_selector,p_validator_hash,p_encrypted_session,p_backend_secret)$$;
create function public.persistent_claim(p_selector uuid,p_validator text,p_backend_secret text)
returns jsonb language sql security invoker set search_path=''
as $$select private.persistent_claim(p_selector,p_validator,p_backend_secret)$$;
create function public.persistent_finish(p_selector uuid,p_claim uuid,p_encrypted_session text,p_backend_secret text)
returns jsonb language sql security invoker set search_path=''
as $$select private.persistent_finish(p_selector,p_claim,p_encrypted_session,p_backend_secret)$$;
create function public.persistent_release(p_selector uuid,p_claim uuid,p_revoke boolean,p_backend_secret text)
returns jsonb language sql security invoker set search_path=''
as $$select private.persistent_release(p_selector,p_claim,p_revoke,p_backend_secret)$$;
create function public.persistent_forget(p_selector uuid,p_validator text,p_backend_secret text)
returns jsonb language sql security invoker set search_path=''
as $$select private.persistent_forget(p_selector,p_validator,p_backend_secret)$$;

revoke all on function private.persistent_register(uuid,text,text,text),public.persistent_register(uuid,text,text,text),
  private.persistent_finish(uuid,uuid,text,text),public.persistent_finish(uuid,uuid,text,text)
  from public,anon,authenticated,service_role;
grant execute on function private.persistent_register(uuid,text,text,text),public.persistent_register(uuid,text,text,text),
  private.persistent_finish(uuid,uuid,text,text),public.persistent_finish(uuid,uuid,text,text) to authenticated;
revoke all on function private.persistent_claim(uuid,text,text),public.persistent_claim(uuid,text,text),
  private.persistent_release(uuid,uuid,boolean,text),public.persistent_release(uuid,uuid,boolean,text),
  private.persistent_forget(uuid,text,text),public.persistent_forget(uuid,text,text) from public,anon,authenticated,service_role;
grant execute on function private.persistent_claim(uuid,text,text),public.persistent_claim(uuid,text,text),
  private.persistent_release(uuid,uuid,boolean,text),public.persistent_release(uuid,uuid,boolean,text),
  private.persistent_forget(uuid,text,text),public.persistent_forget(uuid,text,text) to anon,authenticated;

create or replace function public.app_context(p_supported_permissions text[]) returns jsonb
language sql stable security invoker set search_path='' as $$
  select private.app_context_for_client(p_supported_permissions)||jsonb_build_object('capabilities',jsonb_build_object('persistent_login',true))
$$;
notify pgrst,'reload schema';
