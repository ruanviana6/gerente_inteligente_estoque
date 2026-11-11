-- Estado privado de primeiro acesso e corte de sessões. Não contém senhas.
-- A impressão digital compara o hash mantido pelo Auth antes e depois da troca.
create table private.account_access_controls (
  user_id uuid primary key references auth.users(id) on delete cascade,
  pending boolean not null default false,
  ready boolean not null default false,
  expires_at timestamptz,
  revoked_before timestamptz,
  password_fingerprint text,
  operation_id uuid,
  check (password_fingerprint is null or length(password_fingerprint)=32)
);
alter table private.account_access_controls enable row level security;
revoke all on private.account_access_controls from public, anon, authenticated;

-- Separa a sessão Auth válida do bloqueio de dados durante a troca de senha.
-- Sessões anteriores à desativação ou redefinição não recuperam acesso depois.
create function private.auth_session_valid() returns boolean language plpgsql stable security definer
set search_path='' as $$
declare claims jsonb := auth.jwt(); sid uuid; expiry numeric;
begin
  if auth.uid() is null or coalesce((claims->>'is_anonymous')::boolean,false) then return false; end if;
  sid := (claims->>'session_id')::uuid;
  expiry := (claims->>'exp')::numeric;
  if sid is null or expiry is null or expiry <= extract(epoch from now()) then return false; end if;
  return exists(select 1 from auth.sessions s
    left join private.account_access_controls g on g.user_id=s.user_id
    where s.id=sid and s.user_id=auth.uid() and (s.not_after is null or s.not_after>now())
      and (g.revoked_before is null or s.created_at>g.revoked_before));
exception when invalid_text_representation or numeric_value_out_of_range then return false;
end $$;

create or replace function private.session_valid() returns boolean language sql stable security definer
set search_path='' as $$
  select private.auth_session_valid() and not exists(
    select 1 from private.account_access_controls where user_id=auth.uid() and pending)
$$;

create function private.block_deactivated_sessions() returns trigger language plpgsql security definer
set search_path='' as $$
begin
  if old.active and not new.active then
    insert into private.account_access_controls(user_id,revoked_before)
      values(new.user_id,clock_timestamp())
      on conflict(user_id) do update set revoked_before=excluded.revoked_before;
  end if;
  return new;
end $$;
create trigger invalidate_deactivated_sessions after update of active on public.app_users
  for each row execute function private.block_deactivated_sessions();

create function private.password_gate_status() returns jsonb language plpgsql stable security definer
set search_path='' as $$
declare gate private.account_access_controls;
begin
  if not private.auth_session_valid() or not exists(
    select 1 from public.app_users where user_id=auth.uid() and active) then
    raise exception 'Acesso negado.' using errcode='42501';
  end if;
  select * into gate from private.account_access_controls where user_id=auth.uid();
  return jsonb_build_object('required',coalesce(gate.pending,false),
    'ready',coalesce(gate.ready,false),
    'expired',coalesce(gate.pending and gate.expires_at<=now(),false));
end $$;

create function private.finish_initial_password() returns jsonb language plpgsql security definer
set search_path='' as $$
declare gate private.account_access_controls; fingerprint text;
begin
  if not private.auth_session_valid() or not exists(
    select 1 from public.app_users where user_id=auth.uid() and active) then
    raise exception 'Acesso negado.' using errcode='42501';
  end if;
  select * into gate from private.account_access_controls where user_id=auth.uid() for update;
  select md5(encrypted_password) into fingerprint from auth.users where id=auth.uid();
  if not coalesce(gate.pending,false) or not coalesce(gate.ready,false)
      or gate.expires_at is null or gate.expires_at<=now()
      or fingerprint is null or fingerprint is not distinct from gate.password_fingerprint then
    raise exception 'A troca de senha não foi confirmada.' using errcode='42501';
  end if;
  update private.account_access_controls set pending=false,ready=false,expires_at=null,
    password_fingerprint=null,operation_id=null,revoked_before=clock_timestamp() where user_id=auth.uid();
  insert into public.audit_events(organization_id,author_id,action,target)
    select organization_id,auth.uid(),'users.password_changed',auth.uid()::text
    from public.app_users where user_id=auth.uid();
  return jsonb_build_object('updated',true);
end $$;

-- A criação Auth ocorre primeiro; até esta transação a identidade não tem perfil.
create function private.admin_register_temporary_user(p_user_id uuid,p_display_name text,p_role text,p_permissions jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; fingerprint text;
begin
  org := private.require_master();
  perform pg_advisory_xact_lock(hashtextextended('user:'||p_user_id::text,0));
  if exists(select 1 from public.app_users where user_id=p_user_id) then
    raise exception 'Conta não gerenciável.' using errcode='42501';
  end if;
  select md5(encrypted_password) into fingerprint from auth.users where id=p_user_id;
  if fingerprint is null then raise exception 'Conta não gerenciável.' using errcode='42501'; end if;
  perform private.admin_set_user(p_user_id,p_display_name,p_role,true,p_permissions);
  insert into private.account_access_controls(user_id,pending,ready,expires_at,password_fingerprint)
    values(p_user_id,true,true,clock_timestamp()+interval '24 hours',fingerprint);
  insert into public.audit_events(organization_id,author_id,action,target)
    values(org,auth.uid(),'users.temporary_access',p_user_id::text);
  return jsonb_build_object('updated',true);
end $$;

create function private.admin_begin_password_reset(p_user_id uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; target public.app_users; fingerprint text; operation uuid := gen_random_uuid();
begin
  org := private.require_master();
  select * into target from public.app_users where user_id=p_user_id for update;
  if target.organization_id is distinct from org or target.role='Master' or not coalesce(target.active,false) then
    raise exception 'Conta não gerenciável.' using errcode='42501';
  end if;
  if (select count(*) from public.audit_events where organization_id=org and author_id=auth.uid()
      and action='users.password_reset_started' and created_at>now()-interval '1 hour')>=5 then
    raise exception 'Limite de redefinições atingido.' using errcode='42900';
  end if;
  select md5(encrypted_password) into fingerprint from auth.users where id=p_user_id;
  if fingerprint is null then raise exception 'Conta não gerenciável.' using errcode='42501'; end if;
  insert into private.account_access_controls(user_id,pending,ready,expires_at,revoked_before,password_fingerprint,operation_id)
    values(p_user_id,true,false,clock_timestamp()+interval '24 hours',clock_timestamp(),fingerprint,operation)
    on conflict(user_id) do update set pending=true,ready=false,expires_at=excluded.expires_at,
      revoked_before=excluded.revoked_before,password_fingerprint=excluded.password_fingerprint,operation_id=excluded.operation_id;
  insert into public.audit_events(organization_id,author_id,action,target)
    values(org,auth.uid(),'users.password_reset_started',p_user_id::text);
  return jsonb_build_object('operation_id',operation);
end $$;

create function private.admin_finish_password_reset(p_user_id uuid,p_operation_id uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; gate private.account_access_controls; fingerprint text;
begin
  org := private.require_master();
  if not exists(select 1 from public.app_users where user_id=p_user_id and organization_id=org
      and active and role<>'Master') then raise exception 'Conta não gerenciável.' using errcode='42501'; end if;
  select * into gate from private.account_access_controls where user_id=p_user_id for update;
  select md5(encrypted_password) into fingerprint from auth.users where id=p_user_id;
  if not coalesce(gate.pending,false) or gate.ready or gate.operation_id is distinct from p_operation_id
      or gate.expires_at<=now() or fingerprint is null or fingerprint is not distinct from gate.password_fingerprint then
    raise exception 'A redefinição não foi confirmada.' using errcode='42501';
  end if;
  update private.account_access_controls set ready=true,password_fingerprint=fingerprint,operation_id=null,
    expires_at=clock_timestamp()+interval '24 hours' where user_id=p_user_id;
  insert into public.audit_events(organization_id,author_id,action,target)
    values(org,auth.uid(),'users.password_reset_prepared',p_user_id::text);
  return jsonb_build_object('updated',true);
end $$;

create function public.password_gate_status() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.password_gate_status()$$;
create function public.finish_initial_password() returns jsonb language sql security invoker set search_path=''
  as $$select private.finish_initial_password()$$;
create function public.admin_register_temporary_user(p_user_id uuid,p_display_name text,p_role text,p_permissions jsonb)
returns jsonb language sql security invoker set search_path=''
  as $$select private.admin_register_temporary_user(p_user_id,p_display_name,p_role,p_permissions)$$;
create function public.admin_begin_password_reset(p_user_id uuid) returns jsonb language sql security invoker set search_path=''
  as $$select private.admin_begin_password_reset(p_user_id)$$;
create function public.admin_finish_password_reset(p_user_id uuid,p_operation_id uuid) returns jsonb
language sql security invoker set search_path=''
  as $$select private.admin_finish_password_reset(p_user_id,p_operation_id)$$;

revoke all on function private.auth_session_valid(),private.block_deactivated_sessions(),private.password_gate_status(),
  private.finish_initial_password(),private.admin_register_temporary_user(uuid,text,text,jsonb),
  private.admin_begin_password_reset(uuid),private.admin_finish_password_reset(uuid,uuid) from public,anon,authenticated;
revoke all on function public.password_gate_status(),public.finish_initial_password(),
  public.admin_register_temporary_user(uuid,text,text,jsonb),public.admin_begin_password_reset(uuid),
  public.admin_finish_password_reset(uuid,uuid) from public,anon;
grant execute on function private.password_gate_status(),private.finish_initial_password(),
  private.admin_register_temporary_user(uuid,text,text,jsonb),private.admin_begin_password_reset(uuid),
  private.admin_finish_password_reset(uuid,uuid) to authenticated;
grant execute on function public.password_gate_status(),public.finish_initial_password(),
  public.admin_register_temporary_user(uuid,text,text,jsonb),public.admin_begin_password_reset(uuid),
  public.admin_finish_password_reset(uuid,uuid) to authenticated;
