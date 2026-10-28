-- Serializa o vínculo do mesmo UUID entre organizações, sem mudar perfis existentes.
create or replace function private.admin_set_user(p_user_id uuid,p_display_name text,p_role text,p_active boolean,p_permissions jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; previous public.app_users; entry record;
begin
  org := private.require_master();
  perform pg_advisory_xact_lock(hashtextextended('users:'||org::text,0));
  if p_user_id is null or p_display_name is null or length(btrim(p_display_name)) not between 1 and 120 or p_active is null
    or p_role is null or p_role not in ('Administrador','Operador','Visualizador')
    or jsonb_typeof(p_permissions) is distinct from 'object' then raise exception 'Perfil ou permissões inválidos.' using errcode='22023'; end if;
  -- A identidade só pode ser vinculada por uma organização por vez.
  -- O lock por organização não serializa dois Masters de organizações distintas.
  perform pg_advisory_xact_lock(hashtextextended('user:'||p_user_id::text,0));
  select * into previous from public.app_users where user_id=p_user_id for update;
  if previous.role='Master' or (previous.organization_id is not null and previous.organization_id<>org)
    or not exists(select 1 from auth.users where id=p_user_id) then raise exception 'Conta não gerenciável.' using errcode='42501'; end if;
  for entry in select * from jsonb_each(p_permissions) loop
    if jsonb_typeof(entry.value) <> 'boolean' or not exists(select 1 from public.permissions where name=entry.key)
      or entry.key in ('users.manage','permissions.manage','reports.manage','integrations.manage') then
      raise exception 'Permissão não delegável ou inválida.' using errcode='22023';
    end if;
  end loop;
  insert into public.app_users(user_id,organization_id,display_name,role,active)
    values(p_user_id,org,btrim(p_display_name),p_role,p_active)
    on conflict(user_id) do update set display_name=excluded.display_name,role=excluded.role,active=excluded.active,updated_at=now();
  delete from public.user_permissions where user_id=p_user_id;
  insert into public.user_permissions(user_id,permission,allowed)
    select p_user_id,key,(value::text)::boolean from jsonb_each(p_permissions);
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'users.manage',p_user_id::text,jsonb_build_object('role',p_role,'active',p_active,'permissions',p_permissions));
  return jsonb_build_object('updated',true);
end $$;

