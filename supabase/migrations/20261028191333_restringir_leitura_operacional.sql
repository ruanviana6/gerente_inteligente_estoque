-- Dados brutos são autorizados por inventory.view; os outros módulos só calculam
-- seus indicadores por meio do backend protegido do Streamlit, sem service_role.
create table private.backend_credentials (
  organization_id uuid primary key references public.organizations(id),
  secret_hash text not null unique check(secret_hash ~ '^[a-f0-9]{64}$'),
  updated_at timestamptz not null default now()
);
alter table private.backend_credentials enable row level security;
create policy no_direct_backend_credentials on private.backend_credentials
  for all to anon,authenticated using(false) with check(false);
revoke all on private.backend_credentials from public,anon,authenticated,service_role;

-- Apenas o proprietário configura o segredo no SQL Editor. Ele não é um papel
-- administrativo e não substitui a identidade/permissão de quem usa o aplicativo.
create function private.configure_backend_secret(p_organization_id uuid,p_secret text) returns void
language plpgsql security definer set search_path='' as $$
begin
  if p_secret is null or length(p_secret) not between 32 and 512 then
    raise exception 'Segredo de backend inválido.' using errcode='22023';
  end if;
  insert into private.backend_credentials(organization_id,secret_hash)
    values(p_organization_id,encode(sha256(convert_to(p_secret,'UTF8')),'hex'))
    on conflict(organization_id) do update set secret_hash=excluded.secret_hash,updated_at=now();
end $$;
revoke all on function private.configure_backend_secret(uuid,text) from public,anon,authenticated,service_role;

-- As políticas existentes de catálogo, vendas, snapshots, lotes e parâmetros
-- chamam este helper. Custos continuam exigindo adicionalmente finance.view.
create or replace function private.can_read_inventory() returns boolean language sql stable security definer
set search_path='' as $$
  select private.has_permission('inventory.view')
$$;

-- A assinatura anterior é removida para não manter uma rota sem a nova checagem.
drop function public.load_inventory(text,date);
drop function private.load_inventory(text,date);
create function private.load_inventory(p_module text,p_month date default null,p_backend_secret text default null)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  if p_module is null or p_module not in ('dashboard.view','inventory.view','replenishment.view','trends.view',
    'reports.view','parameters.edit','decisions.write') then
    raise exception 'Módulo inválido.' using errcode='22023';
  end if;
  org := private.require_permission(p_module);
  if p_month is not null then perform private.require_permission('reports.view'); end if;
  if not private.has_permission('inventory.view') then
    if p_backend_secret is null or length(p_backend_secret) not between 32 and 512 or not exists(
      select 1 from private.backend_credentials c where c.organization_id=org
        and c.secret_hash=encode(sha256(convert_to(p_backend_secret,'UTF8')),'hex')) then
      raise exception 'Operação requer o backend autorizado.' using errcode='42501';
    end if;
  end if;
  return private.inventory_payload(org,auth.uid(),p_month);
end $$;
create function public.load_inventory(p_module text,p_month date default null,p_backend_secret text default null)
returns jsonb language sql stable security invoker set search_path=''
as $$select private.load_inventory(p_module,p_month,p_backend_secret)$$;
revoke all on function private.load_inventory(text,date,text),public.load_inventory(text,date,text)
  from public,anon,authenticated,service_role;
grant execute on function private.load_inventory(text,date,text),public.load_inventory(text,date,text) to authenticated;
notify pgrst,'reload schema';
