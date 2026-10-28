-- Fonte da migração inicial. O CI usa a CLI para gerar o nome versionado.
-- Esta migração cria somente objetos do gerente; não modifica dados do ERP.
create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated;

create table public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(btrim(name)) between 1 and 120),
  created_at timestamptz not null default now()
);
create table public.app_users (
  user_id uuid primary key references auth.users(id),
  organization_id uuid not null references public.organizations(id),
  display_name text not null check (length(btrim(display_name)) between 1 and 120),
  role text not null check (role in ('Master','Administrador','Operador','Visualizador')),
  active boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id,user_id)
);
create unique index one_master_per_organization on public.app_users(organization_id) where role='Master';
create index app_users_organization on public.app_users(organization_id);
create table public.permissions (name text primary key);
insert into public.permissions(name) values
 ('dashboard.view'),('inventory.view'),('replenishment.view'),('replenishment.export'),
 ('finance.view'),('trends.view'),('inventory.import'),('parameters.edit'),('decisions.write'),
 ('audit.view'),('users.manage'),('permissions.manage'),('integrations.manage'),
 ('reports.view'),('reports.export'),('reports.receive'),('reports.manage');
create table public.role_permissions (
  role text not null check (role in ('Administrador','Operador','Visualizador')),
  permission text not null references public.permissions(name),
  primary key(role,permission)
);
insert into public.role_permissions(role,permission)
select 'Administrador',name from public.permissions
where name not in ('users.manage','permissions.manage','integrations.manage','reports.manage');
insert into public.role_permissions(role,permission) values
 ('Operador','dashboard.view'),('Operador','inventory.view'),('Operador','replenishment.view'),
 ('Operador','trends.view'),('Operador','decisions.write'),('Operador','reports.view'),
 ('Visualizador','dashboard.view');
create table public.user_permissions (
  user_id uuid not null references public.app_users(user_id),
  permission text not null references public.permissions(name),
  allowed boolean not null,
  primary key(user_id,permission)
);

-- Cada lote conserva catálogo e vendas completos: relatórios antigos não usam saldo atual.
create table public.inventory_imports (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  author_id uuid not null references auth.users(id),
  fingerprint text not null,
  snapshot_date date not null,
  period_start date not null,
  period_end date not null,
  source text not null default 'CSV completo',
  created_at timestamptz not null default now(),
  check (period_start <= period_end and period_end <= snapshot_date),
  unique(organization_id,id),
  unique(organization_id,fingerprint)
);
create index inventory_imports_period on public.inventory_imports(organization_id,snapshot_date,created_at desc);
create index inventory_imports_author on public.inventory_imports(author_id);
create table public.inventory_current (
  organization_id uuid primary key references public.organizations(id),
  batch_id uuid not null,
  foreign key(organization_id,batch_id) references public.inventory_imports(organization_id,id)
);
create table public.inventory_products (
  organization_id uuid not null,
  batch_id uuid not null,
  codigo text not null check(length(btrim(codigo)) between 1 and 120),
  nome text not null check(length(btrim(nome)) between 1 and 300),
  fornecedor text not null check(length(fornecedor) <= 300),
  estoque integer not null check(estoque >= 0),
  prazo_entrega_dias integer not null check(prazo_entrega_dias >= 0),
  estoque_seguranca integer not null check(estoque_seguranca >= 0),
  pedidos_pendentes integer not null check(pedidos_pendentes >= 0),
  reservas integer not null check(reservas >= 0),
  cobertura_alvo_dias integer not null check(cobertura_alvo_dias > 0),
  primary key(organization_id,batch_id,codigo),
  foreign key(organization_id,batch_id) references public.inventory_imports(organization_id,id)
);
-- RLS não filtra colunas: os custos têm tabela e política próprias.
create table public.inventory_costs (
  organization_id uuid not null,
  batch_id uuid not null,
  codigo text not null,
  custo_unitario numeric not null check(custo_unitario >= 0 and custo_unitario < 'Infinity'::numeric),
  primary key(organization_id,batch_id,codigo),
  foreign key(organization_id,batch_id,codigo) references public.inventory_products(organization_id,batch_id,codigo)
);
create table public.inventory_sales (
  organization_id uuid not null,
  batch_id uuid not null,
  codigo text not null,
  data date not null,
  quantidade integer not null check(quantidade >= 0),
  primary key(organization_id,batch_id,codigo,data),
  foreign key(organization_id,batch_id,codigo) references public.inventory_products(organization_id,batch_id,codigo)
);
create index inventory_sales_period on public.inventory_sales(organization_id,batch_id,data);
create table public.inventory_snapshots (
  organization_id uuid not null references public.organizations(id),
  codigo text not null,
  data date not null,
  estoque integer not null check(estoque >= 0),
  primary key(organization_id,codigo,data)
);
create table public.product_parameters (
  organization_id uuid not null references public.organizations(id),
  codigo text not null,
  prazo_entrega_dias integer not null check(prazo_entrega_dias >= 0),
  estoque_seguranca integer not null check(estoque_seguranca >= 0),
  cobertura_alvo_dias integer not null check(cobertura_alvo_dias > 0),
  updated_at timestamptz not null default now(),
  primary key(organization_id,codigo)
);
create table public.decisions (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  author_id uuid not null references auth.users(id),
  codigo text not null,
  quantidade_sugerida integer not null check(quantidade_sugerida >= 0),
  acao text not null check(acao in ('Avaliar compra','Ignorar')),
  observacao text not null default '' check(length(observacao) <= 500),
  data_hora timestamptz not null default now()
);
create index decisions_organization_author on public.decisions(organization_id,author_id,data_hora desc);
create table public.audit_events (
  id bigint generated always as identity primary key,
  organization_id uuid not null references public.organizations(id),
  author_id uuid references auth.users(id),
  action text not null,
  target text not null,
  details jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index audit_events_organization on public.audit_events(organization_id,created_at desc);
create index audit_events_author on public.audit_events(author_id);

-- O token é verificado pelo gateway; estas checagens também revogam sessões já encerradas.
create function private.session_valid() returns boolean language plpgsql stable security definer
set search_path='' as $$
declare claims jsonb := auth.jwt(); sid uuid; expiry numeric;
begin
  if auth.uid() is null or coalesce((claims->>'is_anonymous')::boolean,false) then return false; end if;
  sid := (claims->>'session_id')::uuid;
  expiry := (claims->>'exp')::numeric;
  if sid is null or expiry is null or expiry <= extract(epoch from now()) then return false; end if;
  return exists(select 1 from auth.sessions s where s.id=sid and s.user_id=auth.uid()
    and (s.not_after is null or s.not_after > now()));
exception when invalid_text_representation or numeric_value_out_of_range then return false;
end $$;
-- A API modifica parâmetros atuais, preservando os parâmetros das versões históricas.
create function private.update_parameters(p_codigo text,p_values jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; field text; n numeric;
begin
  org := private.require_permission('parameters.edit');
  if jsonb_typeof(p_values) is distinct from 'object' or
    (select count(*) from jsonb_object_keys(p_values))<>3 then raise exception 'Parâmetros inválidos.' using errcode='22023'; end if;
  foreach field in array array['prazo_entrega_dias','estoque_seguranca','cobertura_alvo_dias'] loop
    if jsonb_typeof(p_values->field) is distinct from 'number' then raise exception 'Parâmetros inválidos.' using errcode='22023'; end if;
    n := (p_values->>field)::numeric;
    if n<0 or n>2147483647 or n<>trunc(n) or (field='cobertura_alvo_dias' and n=0) then
      raise exception 'Parâmetros fora do limite.' using errcode='22023';
    end if;
  end loop;
  if not exists(select 1 from public.inventory_products p join public.inventory_current c on p.batch_id=c.batch_id and p.organization_id=c.organization_id
    where p.organization_id=org and p.codigo=p_codigo) then raise exception 'Produto não encontrado.' using errcode='22023'; end if;
  insert into public.product_parameters(organization_id,codigo,prazo_entrega_dias,estoque_seguranca,cobertura_alvo_dias)
    values(org,p_codigo,(p_values->>'prazo_entrega_dias')::numeric::integer,(p_values->>'estoque_seguranca')::numeric::integer,
    (p_values->>'cobertura_alvo_dias')::numeric::integer)
    on conflict(organization_id,codigo) do update set prazo_entrega_dias=excluded.prazo_entrega_dias,
      estoque_seguranca=excluded.estoque_seguranca,cobertura_alvo_dias=excluded.cobertura_alvo_dias,updated_at=now();
  insert into public.audit_events(organization_id,author_id,action,target,details) values(org,auth.uid(),'parameters.edit',p_codigo,p_values);
  return jsonb_build_object('updated',true);
end $$;
create function private.save_decision(p_codigo text,p_acao text,p_quantidade integer,p_observacao text default '')
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; result uuid;
begin
  org := private.require_permission('decisions.write');
  if p_acao is null or p_acao not in ('Avaliar compra','Ignorar') or p_quantidade is null or p_quantidade<0
    or p_observacao is null or length(p_observacao)>500 then raise exception 'Decisão inválida.' using errcode='22023'; end if;
  if not exists(select 1 from public.inventory_products p join public.inventory_current c on p.batch_id=c.batch_id and p.organization_id=c.organization_id
    where p.organization_id=org and p.codigo=p_codigo) then raise exception 'Produto não encontrado.' using errcode='22023'; end if;
  insert into public.decisions(organization_id,author_id,codigo,acao,quantidade_sugerida,observacao)
    values(org,auth.uid(),p_codigo,p_acao,p_quantidade,p_observacao) returning id into result;
  insert into public.audit_events(organization_id,author_id,action,target) values(org,auth.uid(),'decisions.write',result::text);
  return jsonb_build_object('id',result);
end $$;
create function private.list_decisions() returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_permission('decisions.write');
  return coalesce((select jsonb_agg(to_jsonb(d)-'organization_id' order by d.data_hora desc) from public.decisions d
    where d.organization_id=org and (d.author_id=auth.uid() or private.is_master())),'[]'::jsonb);
end $$;
create function private.admin_list_users() returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_master();
  return coalesce((select jsonb_agg(jsonb_build_object('user_id',u.user_id,'display_name',u.display_name,
    'role',u.role,'active',u.active,'email',a.email,'permissions',coalesce((select jsonb_object_agg(permission,allowed)
      from public.user_permissions where user_id=u.user_id),'{}'::jsonb)) order by u.display_name)
    from public.app_users u join auth.users a on a.id=u.user_id where u.organization_id=org),'[]'::jsonb);
end $$;
create function private.admin_set_user(p_user_id uuid,p_display_name text,p_role text,p_active boolean,p_permissions jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; previous public.app_users; entry record;
begin
  org := private.require_master();
  perform pg_advisory_xact_lock(hashtextextended('users:'||org::text,0));
  if p_user_id is null or p_display_name is null or length(btrim(p_display_name)) not between 1 and 120 or p_active is null
    or p_role is null or p_role not in ('Administrador','Operador','Visualizador')
    or jsonb_typeof(p_permissions) is distinct from 'object' then raise exception 'Perfil ou permissões inválidos.' using errcode='22023'; end if;
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
create function private.admin_audit() returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_permission('audit.view');
  return coalesce((select jsonb_agg(to_jsonb(e)-'organization_id' order by e.created_at desc)
    from (select * from public.audit_events where organization_id=org order by created_at desc,id desc limit 500) e),'[]'::jsonb);
end $$;

-- Bootstrap exclusivo do proprietário no SQL Editor, sem senha padrão nem promoção pela API.
create function private.bootstrap_master(p_user_id uuid,p_display_name text,p_organization_name text default 'Meu estoque')
returns uuid language plpgsql security definer set search_path='' as $$
declare org uuid;
begin
  perform pg_advisory_xact_lock(hashtextextended('bootstrap-master-gerente',0));
  if exists(select 1 from public.app_users where role='Master') then raise exception 'Master já inicializado.' using errcode='22023'; end if;
  if not exists(select 1 from auth.users where id=p_user_id and email_confirmed_at is not null)
    or p_display_name is null or length(btrim(p_display_name)) not between 1 and 120 then
    raise exception 'Informe uma conta Auth confirmada e um nome válido.' using errcode='22023';
  end if;
  insert into public.organizations(name) values(p_organization_name) returning id into org;
  insert into public.app_users(user_id,organization_id,display_name,role,active) values(p_user_id,org,btrim(p_display_name),'Master',true);
  insert into public.audit_events(organization_id,author_id,action,target) values(org,p_user_id,'master.bootstrap',p_user_id::text);
  return org;
end $$;
create function private.current_organization() returns uuid language sql stable security definer
set search_path='' as $$
  select organization_id from public.app_users
  where user_id=auth.uid() and active and private.session_valid()
$$;
create function private.user_has_permission(p_user uuid,p_permission text) returns boolean
language sql stable security definer set search_path='' as $$
  select coalesce((select u.active and exists(select 1 from public.permissions where name=p_permission)
    and (u.role='Master' or coalesce(
      (select allowed from public.user_permissions where user_id=u.user_id and permission=p_permission),
      exists(select 1 from public.role_permissions where role=u.role and permission=p_permission)))
    from public.app_users u where u.user_id=p_user),false)
$$;
create function private.has_permission(p_permission text) returns boolean
language sql stable security definer set search_path='' as $$
  select private.session_valid() and private.user_has_permission(auth.uid(),p_permission)
$$;
create function private.is_master() returns boolean language sql stable security definer
set search_path='' as $$
  select private.session_valid() and exists(select 1 from public.app_users where user_id=auth.uid() and active and role='Master')
$$;
create function private.can_read_inventory() returns boolean language sql stable security definer
set search_path='' as $$
  select private.has_permission('dashboard.view') or private.has_permission('inventory.view')
    or private.has_permission('replenishment.view') or private.has_permission('trends.view')
    or private.has_permission('reports.view')
$$;
create function private.require_permission(p_permission text) returns uuid language plpgsql stable security definer
set search_path='' as $$
begin
  if not private.has_permission(p_permission) then raise exception 'Acesso negado.' using errcode='42501'; end if;
  return private.current_organization();
end $$;
create function private.require_master() returns uuid language plpgsql stable security definer
set search_path='' as $$
begin
  if not private.is_master() then raise exception 'Acesso administrativo negado.' using errcode='42501'; end if;
  return private.current_organization();
end $$;
create function private.app_context() returns jsonb language plpgsql stable security definer
set search_path='' as $$
declare result jsonb;
begin
  if private.current_organization() is null then raise exception 'Sessão sem acesso autorizado.' using errcode='42501'; end if;
  select jsonb_build_object('profile',jsonb_build_object('user_id',u.user_id,'organization_id',u.organization_id,
    'display_name',u.display_name,'role',u.role,'active',u.active),'permissions',
    coalesce((select jsonb_agg(p.name order by p.name) from public.permissions p where private.has_permission(p.name)),'[]'::jsonb))
    into result from public.app_users u where u.user_id=auth.uid();
  return result;
end $$;

-- Payload privado compartilhado por consultas interativas e relatórios autorizados.
create function private.inventory_payload(p_org uuid,p_user uuid,p_month date default null) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare batch public.inventory_imports; products jsonb; sales jsonb; snapshots jsonb; imports jsonb; month_end date;
begin
  if not exists(select 1 from public.app_users where user_id=p_user and organization_id=p_org and active) then
    raise exception 'Acesso negado.' using errcode='42501';
  end if;
  if p_month is null then
    select b.* into batch from public.inventory_imports b join public.inventory_current c on c.batch_id=b.id and c.organization_id=b.organization_id
      where b.organization_id=p_org;
  else
    if p_month <> date_trunc('month',p_month)::date or p_month >= date_trunc('month',now() at time zone 'America/Fortaleza')::date then
      raise exception 'Informe o primeiro dia de um mês encerrado.' using errcode='22023';
    end if;
    month_end := (p_month + interval '1 month - 1 day')::date;
    select b.* into batch from public.inventory_imports b where b.organization_id=p_org and b.snapshot_date=month_end
      and b.period_start<=p_month and b.period_end>=month_end order by b.created_at desc,b.id limit 1;
  end if;
  if batch.id is null then
    return jsonb_build_object('products','[]'::jsonb,'sales','[]'::jsonb,'snapshots','[]'::jsonb,
      'imports','[]'::jsonb,'metadata',jsonb_build_object('month_complete',false,'source','CSV completo'));
  end if;
  select coalesce(jsonb_agg((to_jsonb(p)-'organization_id'-'batch_id') ||
    case when p_month is null and parameters.codigo is not null then jsonb_build_object(
      'prazo_entrega_dias',parameters.prazo_entrega_dias,'estoque_seguranca',parameters.estoque_seguranca,
      'cobertura_alvo_dias',parameters.cobertura_alvo_dias) else '{}'::jsonb end ||
    case when private.user_has_permission(p_user,'finance.view') then jsonb_build_object('custo_unitario',c.custo_unitario) else '{}'::jsonb end
    order by p.codigo),'[]'::jsonb) into products
    from public.inventory_products p left join public.inventory_costs c on c.organization_id=p.organization_id and c.batch_id=p.batch_id and c.codigo=p.codigo
    left join public.product_parameters parameters on parameters.organization_id=p.organization_id and parameters.codigo=p.codigo
    where p.organization_id=p_org and p.batch_id=batch.id;
  select coalesce(jsonb_agg(jsonb_build_object('codigo',s.codigo,'data',s.data,'quantidade',s.quantidade) order by s.data,s.codigo),'[]'::jsonb)
    into sales from public.inventory_sales s where s.organization_id=p_org and s.batch_id=batch.id;
  select coalesce(jsonb_agg(jsonb_build_object('codigo',s.codigo,'data',s.data,'estoque',s.estoque) order by s.data,s.codigo),'[]'::jsonb)
    into snapshots from public.inventory_snapshots s where s.organization_id=p_org and s.data<=batch.snapshot_date
    and exists(select 1 from public.inventory_products p where p.organization_id=p_org and p.batch_id=batch.id and p.codigo=s.codigo);
  select coalesce(jsonb_agg(jsonb_build_object('id',b.id,'snapshot_date',b.snapshot_date,'period_start',b.period_start,
    'period_end',b.period_end,'created_at',b.created_at,'source',b.source) order by b.created_at desc),'[]'::jsonb)
    into imports from public.inventory_imports b where b.organization_id=p_org;
  return jsonb_build_object('products',products,'sales',sales,'snapshots',snapshots,'imports',imports,
    'metadata',jsonb_build_object('batch_id',batch.id,'snapshot_date',batch.snapshot_date,'period_start',batch.period_start,
      'period_end',batch.period_end,'created_at',batch.created_at,'source',batch.source,'month_complete',p_month is not null));
end $$;
create function private.load_inventory(p_module text,p_month date default null) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  if p_module is null or p_module not in ('dashboard.view','inventory.view','replenishment.view','trends.view','reports.view','parameters.edit','decisions.write') then
    raise exception 'Módulo inválido.' using errcode='22023';
  end if;
  org := private.require_permission(p_module);
  if p_month is not null then perform private.require_permission('reports.view'); end if;
  return private.inventory_payload(org,auth.uid(),p_month);
end $$;

-- Toda entrada também é validada no banco: a API pode ser chamada fora do Streamlit.
create function private.import_inventory(p_products jsonb,p_sales jsonb,p_snapshot_date date,p_period_start date,p_period_end date)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; batch uuid; v_fingerprint text; item jsonb; field text; n numeric; day date;
begin
  org := private.require_permission('inventory.import');
  perform pg_advisory_xact_lock(hashtextextended(org::text,0));
  if jsonb_typeof(p_products) is distinct from 'array' or jsonb_typeof(p_sales) is distinct from 'array'
    or jsonb_array_length(p_products) not between 1 and 200000 or jsonb_array_length(p_sales)>200000
    or octet_length(p_products::text)>20971520 or octet_length(p_sales::text)>20971520 then
    raise exception 'Importação inválida ou acima do limite.' using errcode='22023';
  end if;
  if p_snapshot_date is null or p_period_start is null or p_period_end is null
    or p_period_start<'1900-01-01'::date or p_period_start>p_period_end or p_period_end>p_snapshot_date
    or p_snapshot_date>(now() at time zone 'America/Fortaleza')::date then
    raise exception 'Período ou data de estoque inválido.' using errcode='22023';
  end if;
  for item in select value from jsonb_array_elements(p_products) loop
    if jsonb_typeof(item) <> 'object' or jsonb_typeof(item->'codigo') is distinct from 'string'
      or jsonb_typeof(item->'nome') is distinct from 'string' or length(btrim(item->>'codigo')) not between 1 and 120
      or length(btrim(item->>'nome')) not between 1 and 300
      or jsonb_typeof(item->'fornecedor') is distinct from 'string' or length(item->>'fornecedor')>300 then
      raise exception 'Identificação de produto inválida.' using errcode='22023';
    end if;
    foreach field in array array['estoque','prazo_entrega_dias','estoque_seguranca','pedidos_pendentes','reservas','cobertura_alvo_dias'] loop
      if jsonb_typeof(item->field) is distinct from 'number' then raise exception 'Parâmetro numérico inválido.' using errcode='22023'; end if;
      n := (item->>field)::numeric;
      if n<0 or n>2147483647 or n<>trunc(n) or (field='cobertura_alvo_dias' and n=0) then
        raise exception 'Parâmetro inteiro fora do limite.' using errcode='22023';
      end if;
    end loop;
    if item ? 'custo_unitario' and item->'custo_unitario'<>'null'::jsonb then
      if not private.has_permission('finance.view') then raise exception 'Importação de custos não autorizada.' using errcode='42501'; end if;
      if jsonb_typeof(item->'custo_unitario') is distinct from 'number' or (item->>'custo_unitario')::numeric<0
        or (item->>'custo_unitario')::numeric>1.7976931348623157e308::numeric then
        raise exception 'Custo inválido.' using errcode='22023';
      end if;
    end if;
  end loop;
  if (select count(*) from jsonb_array_elements(p_products)) <>
    (select count(distinct btrim(value->>'codigo')) from jsonb_array_elements(p_products)) then
    raise exception 'Código duplicado no catálogo.' using errcode='22023';
  end if;
  for item in select value from jsonb_array_elements(p_sales) loop
    if jsonb_typeof(item) <> 'object' or jsonb_typeof(item->'codigo') is distinct from 'string'
      or jsonb_typeof(item->'data') is distinct from 'string' or item->>'data' !~ '^\d{4}-\d{2}-\d{2}$'
      or jsonb_typeof(item->'quantidade') is distinct from 'number' then
      raise exception 'Venda inválida.' using errcode='22023';
    end if;
    day := (item->>'data')::date; n := (item->>'quantidade')::numeric;
    if day<p_period_start or day>p_period_end or n<0 or n>2147483647 or n<>trunc(n)
      or not exists(select 1 from jsonb_array_elements(p_products) p where btrim(p->>'codigo')=item->>'codigo') then
      raise exception 'Venda fora do período, quantidade ou catálogo.' using errcode='22023';
    end if;
  end loop;
  v_fingerprint := encode(sha256(convert_to(jsonb_build_object('products',p_products,'sales',p_sales,
    'snapshot',p_snapshot_date,'start',p_period_start,'end',p_period_end)::text,'UTF8')),'hex');
  select id into batch from public.inventory_imports where organization_id=org and fingerprint=v_fingerprint;
  if batch is not null then return jsonb_build_object('batch_id',batch,'reused',true); end if;
  insert into public.inventory_imports(organization_id,author_id,fingerprint,snapshot_date,period_start,period_end)
    values(org,auth.uid(),v_fingerprint,p_snapshot_date,p_period_start,p_period_end) returning id into batch;
  insert into public.inventory_products(organization_id,batch_id,codigo,nome,fornecedor,estoque,prazo_entrega_dias,
    estoque_seguranca,pedidos_pendentes,reservas,cobertura_alvo_dias)
  select org,batch,btrim(p->>'codigo'),btrim(p->>'nome'),p->>'fornecedor',(p->>'estoque')::numeric::integer,
    (p->>'prazo_entrega_dias')::numeric::integer,(p->>'estoque_seguranca')::numeric::integer,
    (p->>'pedidos_pendentes')::numeric::integer,(p->>'reservas')::numeric::integer,(p->>'cobertura_alvo_dias')::numeric::integer
    from jsonb_array_elements(p_products) p;
  insert into public.inventory_costs(organization_id,batch_id,codigo,custo_unitario)
    select org,batch,btrim(p->>'codigo'),(p->>'custo_unitario')::numeric from jsonb_array_elements(p_products) p
    where p ? 'custo_unitario' and p->'custo_unitario'<>'null'::jsonb;
  -- A soma é feita em numeric; a conversão final rejeita overflow, revertendo o lote inteiro.
  insert into public.inventory_sales(organization_id,batch_id,codigo,data,quantidade)
    select org,batch,p->>'codigo',(p->>'data')::date,sum((p->>'quantidade')::numeric)::integer
    from jsonb_array_elements(p_sales) p group by p->>'codigo',(p->>'data')::date;
  insert into public.inventory_snapshots(organization_id,codigo,data,estoque)
    select org,codigo,p_snapshot_date,estoque from public.inventory_products where organization_id=org and batch_id=batch
    on conflict(organization_id,codigo,data) do update set estoque=excluded.estoque;
  -- Importar um fechamento antigo não substitui a base operacional mais recente.
  if not exists(select 1 from public.inventory_current c join public.inventory_imports b on b.id=c.batch_id
    where c.organization_id=org and b.snapshot_date>p_snapshot_date) then
    insert into public.inventory_current(organization_id,batch_id) values(org,batch)
      on conflict(organization_id) do update set batch_id=excluded.batch_id;
  end if;
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'inventory.import',batch::text,jsonb_build_object('products',jsonb_array_length(p_products),'sales',jsonb_array_length(p_sales)));
  return jsonb_build_object('batch_id',batch,'reused',false);
end $$;

create table public.report_settings (
  organization_id uuid primary key references public.organizations(id),
  enabled boolean not null default false,
  real_enabled boolean not null default false,
  recipients uuid[] not null default '{}',
  blocks text[] not null default '{estoque,reposicao,tendencias}',
  send_day integer not null default 1 check(send_day between 1 and 28),
  updated_at timestamptz not null default now()
);
create table public.report_jobs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  user_id uuid not null,
  month date not null check(month=date_trunc('month',month)::date),
  dry_run boolean not null,
  status text not null default 'pendente' check(status in ('pendente','processando','simulado','enviado','falha','incerto')),
  attempts integer not null default 0 check(attempts between 0 and 3),
  claim_token uuid,
  lease_until timestamptz,
  error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key(organization_id,user_id) references public.app_users(organization_id,user_id),
  unique(organization_id,user_id,month,dry_run)
);
create index report_jobs_organization_status on public.report_jobs(organization_id,status,month);
create index report_jobs_recipient on public.report_jobs(user_id);
create table private.job_credentials (
  organization_id uuid primary key references public.organizations(id),
  secret_hash text not null unique,
  updated_at timestamptz not null default now()
);
alter table private.job_credentials enable row level security;
revoke all on private.job_credentials from public,anon,authenticated;

create function private.configure_job_secret(p_organization_id uuid,p_secret text) returns void
language plpgsql security definer set search_path='' as $$
begin
  if p_secret is null or length(p_secret)<32 or length(p_secret)>512 then raise exception 'Segredo de job inválido.' using errcode='22023'; end if;
  insert into private.job_credentials(organization_id,secret_hash) values(p_organization_id,encode(sha256(convert_to(p_secret,'UTF8')),'hex'))
    on conflict(organization_id) do update set secret_hash=excluded.secret_hash,updated_at=now();
end $$;
create function private.job_organization(p_secret text) returns uuid language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  if p_secret is null or length(p_secret) not between 32 and 512 then raise exception 'Job não autorizado.' using errcode='42501'; end if;
  select organization_id into org from private.job_credentials where secret_hash=encode(sha256(convert_to(p_secret,'UTF8')),'hex');
  if org is null then raise exception 'Job não autorizado.' using errcode='42501'; end if;
  return org;
end $$;
create function private.report_settings() returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_master();
  return coalesce((select to_jsonb(s)-'organization_id' from public.report_settings s where organization_id=org),
    jsonb_build_object('enabled',false,'real_enabled',false,'recipients','[]'::jsonb,'blocks',jsonb_build_array('estoque','reposicao','tendencias'),'send_day',1));
end $$;
create function private.admin_report_settings(p_enabled boolean,p_real_enabled boolean,p_recipients uuid[],p_blocks text[],p_send_day integer default 1)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_master();
  if p_enabled is null or p_real_enabled is null or p_recipients is null or p_blocks is null or p_send_day is null
    or p_send_day not between 1 and 28 or array_length(p_recipients,1)>100
    or exists(select 1 from unnest(p_blocks) b where b is null or b not in ('estoque','reposicao','tendencias','financeiro'))
    or exists(select 1 from unnest(p_recipients) recipient where recipient is null or not exists(
      select 1 from public.app_users u where u.user_id=recipient and u.organization_id=org)) then
    raise exception 'Configuração de relatórios inválida.' using errcode='22023';
  end if;
  insert into public.report_settings(organization_id,enabled,real_enabled,recipients,blocks,send_day)
    values(org,p_enabled,p_real_enabled,p_recipients,p_blocks,p_send_day)
    on conflict(organization_id) do update set enabled=excluded.enabled,real_enabled=excluded.real_enabled,
      recipients=excluded.recipients,blocks=excluded.blocks,send_day=excluded.send_day,updated_at=now();
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'reports.manage',org::text,jsonb_build_object('enabled',p_enabled,'real_enabled',p_real_enabled,
      'recipient_count',cardinality(p_recipients),'blocks',p_blocks,'send_day',p_send_day));
  return jsonb_build_object('updated',true);
end $$;
create function private.report_job_claim(p_job_secret text,p_month date,p_dry_run boolean default true)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; settings public.report_settings; job public.report_jobs; response jsonb := '[]'; perms jsonb; today date;
begin
  org := private.job_organization(p_job_secret);
  perform pg_advisory_xact_lock(hashtextextended('jobs:'||org::text,0));
  today := (now() at time zone 'America/Fortaleza')::date;
  if p_dry_run is null or p_month is null or p_month<>date_trunc('month',p_month)::date or p_month>=date_trunc('month',today)::date then
    raise exception 'Período de job inválido.' using errcode='22023';
  end if;
  select * into settings from public.report_settings where organization_id=org;
  if settings.organization_id is null or not settings.enabled then return response; end if;
  if not p_dry_run and (not settings.real_enabled or extract(day from today)<>settings.send_day
    or p_month<>(date_trunc('month',today)-interval '1 month')::date) then return response; end if;
  -- Uma chamada real interrompida pode ter sido entregue: nunca reenviamos automaticamente esse estado.
  update public.report_jobs set status='incerto',error_code='ENTREGA_INCERTA',updated_at=now()
    where organization_id=org and not dry_run and status='processando' and lease_until<now();
  insert into public.report_jobs(organization_id,user_id,month,dry_run)
    select org,u.user_id,p_month,p_dry_run from public.app_users u where u.organization_id=org
      and u.user_id=any(settings.recipients) and private.user_has_permission(u.user_id,'reports.receive')
      and private.user_has_permission(u.user_id,'reports.view')
    on conflict(organization_id,user_id,month,dry_run) do nothing;
  for job in select * from public.report_jobs j where j.organization_id=org and j.month=p_month and j.dry_run=p_dry_run
    and j.attempts<3 and (j.status in ('pendente','falha') or (j.dry_run and j.status='processando' and j.lease_until<now()))
    and j.user_id=any(settings.recipients) and private.user_has_permission(j.user_id,'reports.receive')
    and private.user_has_permission(j.user_id,'reports.view') for update skip locked loop
    update public.report_jobs set status='processando',attempts=attempts+1,claim_token=gen_random_uuid(),lease_until=now()+interval '15 minutes',updated_at=now()
      where id=job.id returning * into job;
    select coalesce(jsonb_agg(name order by name),'[]'::jsonb) into perms from public.permissions where private.user_has_permission(job.user_id,name);
    response := response || jsonb_build_array(jsonb_build_object('id',job.id,'organization_id',org,'user_id',job.user_id,
      'claim_token',job.claim_token,'permissions',perms,'payload',private.inventory_payload(org,job.user_id,p_month),
      'idempotency_key',job.id::text,'blocks',settings.blocks,'real_enabled',settings.real_enabled,'dry_run',job.dry_run,'attempts',job.attempts));
  end loop;
  return response;
end $$;
create function private.report_job_authorize(p_job_secret text,p_job_id uuid,p_claim_token uuid)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid; job public.report_jobs; settings public.report_settings; allowed boolean; perms jsonb; address text;
begin
  org := private.job_organization(p_job_secret);
  select * into job from public.report_jobs where id=p_job_id and organization_id=org and claim_token=p_claim_token
    and status='processando' and lease_until>now();
  if job.id is null then raise exception 'Execução não autorizada.' using errcode='42501'; end if;
  select * into settings from public.report_settings where organization_id=org;
  allowed := coalesce(settings.enabled and job.user_id=any(settings.recipients)
    and private.user_has_permission(job.user_id,'reports.receive') and private.user_has_permission(job.user_id,'reports.view')
    and (job.dry_run or settings.real_enabled),false);
  if not allowed then return jsonb_build_object('allowed',false,'active',false,'permissions','[]'::jsonb,'real_enabled',false); end if;
  select coalesce(jsonb_agg(name order by name),'[]'::jsonb) into perms from public.permissions where private.user_has_permission(job.user_id,name);
  select email into address from auth.users where id=job.user_id;
  return jsonb_build_object('allowed',true,'active',true,'permissions',perms,'email',address,
    'real_enabled',settings.real_enabled,'payload',private.inventory_payload(org,job.user_id,job.month),'blocks',settings.blocks);
end $$;
create function private.report_job_finish(p_job_secret text,p_job_id uuid,p_claim_token uuid,p_status text,p_error_code text default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; job public.report_jobs;
begin
  org := private.job_organization(p_job_secret);
  if p_status is null or p_status not in ('simulado','enviado','falha','incerto') or
    (p_error_code is not null and p_error_code not in ('DADOS_INCOMPLETOS','ACESSO_REVOGADO','CONFIGURACAO_AUSENTE','PROVEDOR_REJEITOU','ENTREGA_INCERTA','FALHA_LOCAL')) then
    raise exception 'Resultado de job inválido.' using errcode='22023';
  end if;
  select * into job from public.report_jobs where id=p_job_id and organization_id=org and claim_token=p_claim_token and status='processando' for update;
  if job.id is null or (p_status='enviado' and job.dry_run) or (p_status='simulado' and not job.dry_run) then
    raise exception 'Execução não autorizada.' using errcode='42501';
  end if;
  update public.report_jobs set status=p_status,error_code=p_error_code,claim_token=null,lease_until=null,updated_at=now() where id=job.id;
  insert into public.audit_events(organization_id,action,target,details)
    values(org,'reports.job',job.id::text,jsonb_build_object('status',p_status,'dry_run',job.dry_run,'error_code',p_error_code));
  return jsonb_build_object('updated',true);
end $$;
create function private.report_job_history() returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_master();
  return coalesce((select jsonb_agg(to_jsonb(j)-'claim_token'-'organization_id' order by j.created_at desc)
    from (select * from public.report_jobs where organization_id=org order by created_at desc limit 200) j),'[]'::jsonb);
end $$;

-- As tabelas não oferecem mutações diretas; RPCs auditadas validam a operação inteira.
do $$
declare tabela text;
begin
  foreach tabela in array array['organizations','app_users','permissions','role_permissions','user_permissions',
    'inventory_imports','inventory_current','inventory_products','inventory_costs','inventory_sales',
    'inventory_snapshots','product_parameters','decisions','audit_events','report_settings','report_jobs'] loop
    execute format('alter table public.%I enable row level security',tabela);
    execute format('revoke all on public.%I from public,anon,authenticated,service_role',tabela);
  end loop;
end $$;
revoke all on all sequences in schema public from anon,authenticated;
grant select on public.organizations,public.app_users,public.permissions,public.role_permissions,public.user_permissions,
  public.inventory_current,public.inventory_products,public.inventory_costs,public.inventory_sales,
  public.inventory_snapshots,public.product_parameters,public.decisions,public.audit_events,public.report_settings to authenticated;
-- O fingerprint inclui custos no hash; não pode ser enumerado por usuários sem acesso financeiro.
grant select(id,organization_id,author_id,snapshot_date,period_start,period_end,source,created_at)
  on public.inventory_imports to authenticated;
create policy organizations_read on public.organizations for select to authenticated
  using(id=(select private.current_organization()));
create policy app_users_read on public.app_users for select to authenticated
  using(organization_id=(select private.current_organization()) and (user_id=(select auth.uid()) or (select private.is_master())));
create policy permissions_read on public.permissions for select to authenticated using((select private.current_organization()) is not null);
create policy role_permissions_read on public.role_permissions for select to authenticated using((select private.current_organization()) is not null);
create policy user_permissions_read on public.user_permissions for select to authenticated
  using(user_id=(select auth.uid()) and (select private.current_organization()) is not null);
do $$
declare tabela text;
begin
  foreach tabela in array array['inventory_imports','inventory_current','inventory_products','inventory_sales','inventory_snapshots','product_parameters'] loop
    execute format('create policy authorized_inventory on public.%I for select to authenticated using(organization_id=(select private.current_organization()) and (select private.can_read_inventory()))',tabela);
  end loop;
end $$;
create policy finance_read on public.inventory_costs for select to authenticated
  using(organization_id=(select private.current_organization()) and (select private.has_permission('finance.view')) and (select private.can_read_inventory()));
create policy decisions_read on public.decisions for select to authenticated
  using(organization_id=(select private.current_organization()) and (select private.has_permission('inventory.view'))
    and (author_id=(select auth.uid()) or (select private.is_master())));
create policy audit_read on public.audit_events for select to authenticated
  using(organization_id=(select private.current_organization()) and (select private.has_permission('audit.view')));
create policy report_settings_read on public.report_settings for select to authenticated
  using(organization_id=(select private.current_organization()) and (select private.is_master()));
-- report_jobs não tem grants nem políticas diretas: histórico público remove claim_token.

-- Wrappers públicos INVOKER: a lógica privilegiada fica no schema não exposto private.
create function public.app_context() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.app_context()$$;
create function public.load_inventory(p_module text,p_month date default null) returns jsonb language sql stable security invoker set search_path=''
  as $$select private.load_inventory(p_module,p_month)$$;
create function public.import_inventory(p_products jsonb,p_sales jsonb,p_snapshot_date date,p_period_start date,p_period_end date)
  returns jsonb language sql security invoker set search_path=''
  as $$select private.import_inventory(p_products,p_sales,p_snapshot_date,p_period_start,p_period_end)$$;
create function public.update_parameters(p_codigo text,p_values jsonb) returns jsonb language sql security invoker set search_path=''
  as $$select private.update_parameters(p_codigo,p_values)$$;
create function public.save_decision(p_codigo text,p_acao text,p_quantidade integer,p_observacao text default '') returns jsonb language sql security invoker set search_path=''
  as $$select private.save_decision(p_codigo,p_acao,p_quantidade,p_observacao)$$;
create function public.list_decisions() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.list_decisions()$$;
create function public.admin_list_users() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.admin_list_users()$$;
create function public.admin_set_user(p_user_id uuid,p_display_name text,p_role text,p_active boolean,p_permissions jsonb)
  returns jsonb language sql security invoker set search_path=''
  as $$select private.admin_set_user(p_user_id,p_display_name,p_role,p_active,p_permissions)$$;
create function public.admin_audit() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.admin_audit()$$;
create function public.report_settings() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.report_settings()$$;
create function public.admin_report_settings(p_enabled boolean,p_real_enabled boolean,p_recipients uuid[],p_blocks text[],p_send_day integer default 1)
  returns jsonb language sql security invoker set search_path=''
  as $$select private.admin_report_settings(p_enabled,p_real_enabled,p_recipients,p_blocks,p_send_day)$$;
create function public.report_job_history() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.report_job_history()$$;
create function public.report_job_claim(p_job_secret text,p_month date,p_dry_run boolean default true)
  returns jsonb language sql security invoker set search_path=''
  as $$select private.report_job_claim(p_job_secret,p_month,p_dry_run)$$;
create function public.report_job_authorize(p_job_secret text,p_job_id uuid,p_claim_token uuid)
  returns jsonb language sql stable security invoker set search_path=''
  as $$select private.report_job_authorize(p_job_secret,p_job_id,p_claim_token)$$;
create function public.report_job_finish(p_job_secret text,p_job_id uuid,p_claim_token uuid,p_status text,p_error_code text default null)
  returns jsonb language sql security invoker set search_path=''
  as $$select private.report_job_finish(p_job_secret,p_job_id,p_claim_token,p_status,p_error_code)$$;

revoke all on all functions in schema private from public,anon,authenticated,service_role;
-- Revogação somente das funções deste aplicativo, sem alterar funções de terceiros em public.
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname in ('app_context','load_inventory','import_inventory','update_parameters',
      'save_decision','list_decisions','admin_list_users','admin_set_user','admin_audit','report_settings',
      'admin_report_settings','report_job_history','report_job_claim','report_job_authorize','report_job_finish') loop
    execute format('revoke all on function %s from public,anon,authenticated,service_role',f.signature);
    execute format('grant execute on function %s to authenticated',f.signature);
  end loop;
end $$;
grant execute on function private.current_organization(),private.has_permission(text),private.is_master(),private.can_read_inventory(),
  private.app_context(),private.load_inventory(text,date),private.import_inventory(jsonb,jsonb,date,date,date),
  private.update_parameters(text,jsonb),private.save_decision(text,text,integer,text),private.list_decisions(),
  private.admin_list_users(),private.admin_set_user(uuid,text,text,boolean,jsonb),private.admin_audit(),
  private.report_settings(),private.admin_report_settings(boolean,boolean,uuid[],text[],integer),private.report_job_history()
  to authenticated;
-- O job usa segredo dedicado e não precisa de service_role ou de sessão de um usuário humano.
grant usage on schema private to anon;
grant execute on function public.report_job_claim(text,date,boolean),public.report_job_authorize(text,uuid,uuid),
  public.report_job_finish(text,uuid,uuid,text,text),private.report_job_claim(text,date,boolean),
  private.report_job_authorize(text,uuid,uuid),private.report_job_finish(text,uuid,uuid,text,text) to anon,authenticated;
revoke all on function private.bootstrap_master(uuid,text,text),private.configure_job_secret(uuid,text) from public,anon,authenticated,service_role;
notify pgrst,'reload schema';
