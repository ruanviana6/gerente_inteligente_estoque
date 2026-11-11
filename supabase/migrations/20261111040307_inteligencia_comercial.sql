-- Permissões novas são negadas por padrão aos perfis não Master.
insert into public.permissions(name) values('commercial.view'),('commercial.customer_detail'),('commercial.manage')
  on conflict(name) do nothing;

-- Mantém a trava de concorrência e não permite delegar a gestão comercial.
create or replace function private.admin_set_user(p_user_id uuid,p_display_name text,p_role text,p_active boolean,p_permissions jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; previous public.app_users; entry record;
begin
  org := private.require_master();
  perform pg_advisory_xact_lock(hashtextextended('users:'||org::text,0));
  if p_user_id is null or p_display_name is null or length(btrim(p_display_name)) not between 1 and 120 or p_active is null
    or p_role is null or p_role not in ('Administrador','Operador','Visualizador')
    or jsonb_typeof(p_permissions) is distinct from 'object' then raise exception 'Perfil ou permissões inválidos.' using errcode='22023'; end if;
  perform pg_advisory_xact_lock(hashtextextended('user:'||p_user_id::text,0));
  select * into previous from public.app_users where user_id=p_user_id for update;
  if previous.role='Master' or (previous.organization_id is not null and previous.organization_id<>org)
    or not exists(select 1 from auth.users where id=p_user_id) then raise exception 'Conta não gerenciável.' using errcode='42501'; end if;
  for entry in select * from jsonb_each(p_permissions) loop
    if jsonb_typeof(entry.value) <> 'boolean' or not exists(select 1 from public.permissions where name=entry.key)
      or entry.key in ('users.manage','permissions.manage','reports.manage','integrations.manage','commercial.manage') then
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

-- Resumo recomputável: não substitui documentos nem versões detalhadas de venda.
create table public.commercial_daily_sku (
  organization_id uuid not null,
  source_id uuid not null,
  store_id text not null,
  sale_date date not null,
  sku text not null,
  net_units numeric not null check(net_units>=0),
  orders integer not null check(orders>0),
  updated_at timestamptz not null default now(),
  primary key(organization_id,source_id,store_id,sale_date,sku),
  foreign key(organization_id,source_id) references public.integration_sources(organization_id,id)
);
create table public.commercial_processed_runs (
  organization_id uuid not null,
  run_id uuid not null,
  processed_at timestamptz not null default now(),
  affected_days integer not null check(affected_days>=0),
  primary key(organization_id,run_id),
  foreign key(organization_id,run_id) references public.sync_runs(organization_id,id)
);
alter table public.commercial_daily_sku enable row level security;
alter table public.commercial_processed_runs enable row level security;
revoke all on public.commercial_daily_sku,public.commercial_processed_runs from public,anon,authenticated;

-- Só execução explícita do Master processa uma sincronização já confirmada.
-- Todas as datas antigas das linhas alteradas são refeitas no mesmo lote para
-- corrigir mudanças tardias sem remover dados de origem ou versões históricas.
create function private.refresh_commercial_daily(p_run_id uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; r public.sync_runs; dates date[]; total integer;
begin
  org := private.require_master();
  perform private.require_permission('commercial.manage');
  select * into r from public.sync_runs where id=p_run_id and organization_id=org for update;
  if r.id is null or r.status<>'completed' then raise exception 'Sincronização não confirmada.' using errcode='22023'; end if;
  perform private.sync_source(r.source_id,r.store_id);
  perform pg_advisory_xact_lock(hashtextextended(org::text,13));
  if exists(select 1 from public.commercial_processed_runs where organization_id=org and run_id=r.id) then
    return jsonb_build_object('reused',true,'affected_days',0);
  end if;
  select coalesce(array_agg(distinct (v.payload->>'sale_date')::date),'{}'::date[]) into dates
    from public.sales_line_versions v where v.organization_id=org and v.source_id=r.source_id and v.store_id=r.store_id
    and exists(select 1 from public.sales_line_versions changed where changed.organization_id=org and changed.run_id=r.id
      and changed.document_id=v.document_id and changed.line_id=v.line_id);
  if cardinality(dates)>3660 then raise exception 'Período excessivo para agregação.' using errcode='54000'; end if;
  delete from public.commercial_daily_sku where organization_id=org and source_id=r.source_id
    and store_id=r.store_id and sale_date=any(dates);
  insert into public.commercial_daily_sku(organization_id,source_id,store_id,sale_date,sku,net_units,orders)
  select org,r.source_id,r.store_id,(v.payload->>'sale_date')::date,v.payload->>'sku',
    sum((v.payload->>'quantity')::numeric-(v.payload->>'returned_quantity')::numeric),count(distinct c.document_id)::integer
  from public.sales_lines_current c join public.sales_line_versions v on v.id=c.version_id and v.organization_id=c.organization_id
  where c.organization_id=org and c.source_id=r.source_id and c.store_id=r.store_id
    and (v.payload->>'sale_date')::date=any(dates) and v.payload->>'status'='completed'
    and (v.payload->>'quantity')::numeric>(v.payload->>'returned_quantity')::numeric
  group by (v.payload->>'sale_date')::date,v.payload->>'sku';
  total := cardinality(dates);
  insert into public.commercial_processed_runs(organization_id,run_id,affected_days) values(org,r.id,total);
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'commercial.refresh',r.id::text,jsonb_build_object('affected_days',total));
  return jsonb_build_object('reused',false,'affected_days',total);
end $$;

create function private.commercial_daily(p_source_id uuid,p_store_id text,p_start date,p_end date)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_permission('commercial.view');
  if p_start is null or p_end is null or p_start>p_end or p_end-p_start>366
    or not exists(select 1 from public.integration_sources s where s.id=p_source_id and s.organization_id=org
      and s.active and p_store_id=any(s.stores)) then raise exception 'Escopo inválido.' using errcode='42501'; end if;
  return coalesce((select jsonb_agg(jsonb_build_object('date',sale_date,'sku',sku,'units',net_units,'orders',orders)
    order by sale_date,sku) from (select * from public.commercial_daily_sku where organization_id=org
      and source_id=p_source_id and store_id=p_store_id and sale_date between p_start and p_end
      order by sale_date,sku limit 5000) d),'[]'::jsonb);
end $$;

-- Dados agregados comerciais são tipados e restritos; nenhum nome de cliente,
-- e-mail, endereço completo ou payload livre é salvo nesta tabela.
create table public.commercial_batches (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  source_id uuid not null, store_id text not null,
  content_hash text not null check(content_hash ~ '^[0-9a-f]{64}$'),
  city_basis text not null check(city_basis in ('delivery','registration','billing','unknown')),
  period_start date not null, period_end date not null check(period_end>=period_start and period_end-period_start<=365),
  computed_at timestamptz not null default now(),
  author_id uuid not null references public.app_users(user_id),
  unique(organization_id,source_id,store_id,content_hash),
  unique(organization_id,id),
  foreign key(organization_id,source_id) references public.integration_sources(organization_id,id)
);
create table public.commercial_active_batches (
  organization_id uuid not null, source_id uuid not null, store_id text not null,
  batch_id uuid not null, previous_batch_id uuid,
  primary key(organization_id,source_id,store_id),
  foreign key(organization_id,batch_id) references public.commercial_batches(organization_id,id),
  foreign key(organization_id,previous_batch_id) references public.commercial_batches(organization_id,id)
);
alter table public.commercial_batches enable row level security;
alter table public.commercial_active_batches enable row level security;
revoke all on public.commercial_batches,public.commercial_active_batches from public,anon,authenticated;
create table public.commercial_insights (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  batch_id uuid not null,
  source_id uuid not null,
  store_id text not null,
  dimension text not null check(dimension in ('customer','city','pair')),
  alert_code text not null check(alert_code in ('cadencia_atrasada','volume_crescente','volume_reduzido','mix_alterado',
    'cidade_crescimento','cidade_queda','produtos_juntos')),
  customer_ref text check(customer_ref is null or customer_ref ~ '^[0-9a-f]{64}$'),
  city_code text check(city_code is null or city_code ~ '^[0-9]{7}$'),
  city_name text check(city_name is null or length(city_name) between 1 and 100),
  state text check(state is null or state ~ '^[A-Z]{2}$'),
  sku_a text check(sku_a is null or length(sku_a) between 1 and 120),
  sku_b text check(sku_b is null or length(sku_b) between 1 and 120),
  period_start date not null, period_end date not null check(period_end>=period_start),
  current_units numeric not null check(current_units>=0), previous_units numeric not null check(previous_units>=0),
  current_orders integer not null check(current_orders>=0), previous_orders integer not null check(previous_orders>=0),
  group_customers integer not null check(group_customers>=0),
  previous_customers integer not null check(previous_customers>=0),
  total_orders integer not null default 0 check(total_orders>=0),
  support numeric check(support is null or support between 0 and 1),
  confidence numeric check(confidence is null or confidence between 0 and 1),
  lift numeric check(lift is null or lift>=0),
  median_gap_days numeric check(median_gap_days is null or median_gap_days>0),
  recency_days integer check(recency_days is null or recency_days>=0),
  mix_similarity numeric check(mix_similarity is null or mix_similarity between 0 and 1),
  computed_at timestamptz not null default now(),
  foreign key(organization_id,source_id) references public.integration_sources(organization_id,id),
  foreign key(organization_id,batch_id) references public.commercial_batches(organization_id,id),
  check((dimension='customer' and customer_ref is not null and city_code is null
         and alert_code in ('cadencia_atrasada','volume_crescente','volume_reduzido','mix_alterado'))
     or (dimension='city' and customer_ref is null and city_code is not null and group_customers>=3 and previous_customers>=3
         and alert_code in ('cidade_crescimento','cidade_queda'))
     or (dimension='pair' and customer_ref is null and city_code is null and current_orders>=3 and total_orders>=20
         and sku_a is not null and sku_b is not null and alert_code='produtos_juntos'
         and support is not null and confidence is not null and lift is not null))
);
create index commercial_insights_scope on public.commercial_insights(organization_id,batch_id,dimension);
alter table public.commercial_insights enable row level security;
revoke all on public.commercial_insights from public,anon,authenticated;

create function private.commercial_sources() returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_permission('commercial.view');
  return coalesce((select jsonb_agg(jsonb_build_object('id',s.id,'name',s.name,'stores',s.stores,
    'last_sync',(select max(r.finished_at) from public.sync_runs r where r.organization_id=org
      and r.source_id=s.id and r.status='completed')) order by s.name)
    from public.integration_sources s where s.organization_id=org and s.active),'[]'::jsonb);
end $$;

create function private.commercial_insights(p_source_id uuid,p_store_id text,p_dimension text,p_offset integer default 0)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_permission('commercial.view');
  if p_dimension='customer' and not private.has_permission('commercial.customer_detail') then
    raise exception 'Detalhe de clientes não autorizado.' using errcode='42501';
  end if;
  if p_dimension is null or p_dimension not in ('customer','city','pair') or p_offset is null or p_offset not between 0 and 100000
    or not exists(select 1 from public.integration_sources s where s.id=p_source_id and s.organization_id=org
      and s.active and p_store_id=any(s.stores)) then raise exception 'Escopo inválido.' using errcode='42501'; end if;
  return coalesce((select jsonb_agg(to_jsonb(v)-'organization_id' order by abs(v.current_units-v.previous_units) desc,v.computed_at desc,v.id)
    from (select i.*,b.city_basis from public.commercial_insights i
      join public.commercial_active_batches a on a.organization_id=i.organization_id and a.batch_id=i.batch_id
      join public.commercial_batches b on b.organization_id=i.organization_id and b.id=i.batch_id
      where i.organization_id=org and i.source_id=p_source_id and i.store_id=p_store_id and i.dimension=p_dimension
      order by abs(i.current_units-i.previous_units) desc,i.computed_at desc,i.id limit 10 offset p_offset) v),'[]'::jsonb);
end $$;

create table public.storage_measurements (
  organization_id uuid not null references public.organizations(id),
  measured_on date not null default current_date,
  database_bytes bigint not null check(database_bytes>=0),
  storage_bytes bigint check(storage_bytes>=0),
  measured_at timestamptz not null default now(),
  primary key(organization_id,measured_on)
);
alter table public.storage_measurements enable row level security;
revoke all on public.storage_measurements from public,anon,authenticated;

-- Publicação atômica e idempotente. A tela nunca executa esta função.
create function private.publish_commercial_batch(p_source_id uuid,p_store_id text,p_start date,p_end date,
  p_city_basis text,p_items jsonb) returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; batch uuid; fingerprint text; item jsonb; old uuid;
begin
  org := private.require_master();
  perform private.require_permission('commercial.manage');
  perform private.sync_source(p_source_id,p_store_id);
  if p_start is null or p_end is null or p_end<p_start or p_end-p_start>365 or p_end>current_date
    or p_city_basis is null or p_city_basis not in ('delivery','registration','billing','unknown')
    or jsonb_typeof(p_items) is distinct from 'array' or jsonb_array_length(p_items)>500
    or octet_length(p_items::text)>524288 then raise exception 'Lote comercial inválido.' using errcode='22023'; end if;
  fingerprint := encode(sha256(convert_to(jsonb_build_object('start',p_start,'end',p_end,'city_basis',p_city_basis,
    'items',p_items)::text,'UTF8')),'hex');
  perform pg_advisory_xact_lock(hashtextextended(org::text||p_source_id::text||p_store_id,14));
  select id into batch from public.commercial_batches where organization_id=org and source_id=p_source_id
    and store_id=p_store_id and content_hash=fingerprint;
  if batch is not null then return jsonb_build_object('id',batch,'reused',true); end if;
  if (select count(*) from public.commercial_batches where organization_id=org and source_id=p_source_id and store_id=p_store_id)>=12 then
    raise exception 'Limite de versões atingido. Revise a retenção e o backup; nenhum dado foi apagado.' using errcode='54000'; end if;
  insert into public.commercial_batches(organization_id,source_id,store_id,content_hash,city_basis,period_start,period_end,author_id)
    values(org,p_source_id,p_store_id,fingerprint,p_city_basis,p_start,p_end,auth.uid()) returning id into batch;
  for item in select value from jsonb_array_elements(p_items) loop
    if jsonb_typeof(item) is distinct from 'object' or item-array['dimension','alert_code','customer_ref','city_code',
      'city_name','state','sku_a','sku_b','period_start','period_end','current_units','previous_units','current_orders',
      'previous_orders','group_customers','previous_customers','total_orders','support','confidence','lift',
      'median_gap_days','recency_days','mix_similarity'] <> '{}'::jsonb
      or (item->>'period_start')::date is distinct from p_start or (item->>'period_end')::date is distinct from p_end
      or (p_city_basis='unknown' and item->>'city_code' is not null)
      or (item->>'dimension'='customer' and ((item->>'previous_orders')::int<2
        or (item->>'previous_orders')::int+(item->>'current_orders')::int<4
        or item->>'median_gap_days' is null)) then
      raise exception 'Indicador fora do contrato minimizado.' using errcode='22023'; end if;
    insert into public.commercial_insights select (jsonb_populate_record(null::public.commercial_insights,
      item||jsonb_build_object('id',gen_random_uuid(),'organization_id',org,'source_id',p_source_id,'store_id',p_store_id,
        'batch_id',batch,'computed_at',now()))).*;
  end loop;
  select batch_id into old from public.commercial_active_batches where organization_id=org and source_id=p_source_id and store_id=p_store_id;
  insert into public.commercial_active_batches(organization_id,source_id,store_id,batch_id,previous_batch_id)
    values(org,p_source_id,p_store_id,batch,old)
    on conflict(organization_id,source_id,store_id) do update set batch_id=excluded.batch_id,previous_batch_id=excluded.previous_batch_id;
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'commercial.publish',batch::text,jsonb_build_object('count',jsonb_array_length(p_items)));
  return jsonb_build_object('id',batch,'reused',false);
end $$;

create function private.rollback_commercial_batch(p_source_id uuid,p_store_id text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; active public.commercial_active_batches;
begin
  org := private.require_master();
  perform private.require_permission('commercial.manage');
  perform private.sync_source(p_source_id,p_store_id);
  perform pg_advisory_xact_lock(hashtextextended(org::text||p_source_id::text||p_store_id,14));
  select * into active from public.commercial_active_batches where organization_id=org and source_id=p_source_id and store_id=p_store_id for update;
  if active.previous_batch_id is null then raise exception 'Não há versão anterior para restaurar.' using errcode='22023'; end if;
  update public.commercial_active_batches set batch_id=active.previous_batch_id,previous_batch_id=active.batch_id
    where organization_id=org and source_id=p_source_id and store_id=p_store_id;
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'commercial.rollback',active.previous_batch_id::text,'{}');
  return jsonb_build_object('restored',true);
end $$;

-- Manifestos e previsões numéricas, sem pickle/joblib nem dataset de treino.
create table public.commercial_model_runs (
  id uuid primary key default gen_random_uuid(), organization_id uuid not null references public.organizations(id),
  source_id uuid not null, store_id text not null,
  city_code text not null check(city_code ~ '^[0-9]{7}$'), sku text not null check(length(sku)<=120),
  data_hash text not null check(data_hash ~ '^[0-9a-f]{64}$'),
  manifest jsonb not null check(octet_length(manifest::text)<=16384),
  created_at timestamptz not null default now(),
  unique(organization_id,source_id,store_id,city_code,sku,data_hash), unique(organization_id,id),
  foreign key(organization_id,source_id) references public.integration_sources(organization_id,id)
);
create table public.commercial_active_models (
  organization_id uuid not null,source_id uuid not null,store_id text not null,city_code text not null,sku text not null,
  model_id uuid not null,previous_model_id uuid,
  primary key(organization_id,source_id,store_id,city_code,sku),
  foreign key(organization_id,model_id) references public.commercial_model_runs(organization_id,id),
  foreign key(organization_id,previous_model_id) references public.commercial_model_runs(organization_id,id)
);
alter table public.commercial_model_runs enable row level security;
alter table public.commercial_active_models enable row level security;
revoke all on public.commercial_model_runs,public.commercial_active_models from public,anon,authenticated;

create function private.save_commercial_model(p_source_id uuid,p_store_id text,p_city_code text,p_sku text,p_manifest jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; model uuid; old uuid; point jsonb; metric jsonb; expected date; horizon text;
begin
  org := private.require_master(); perform private.require_permission('commercial.manage');
  perform private.sync_source(p_source_id,p_store_id);
  if p_city_code is null or p_city_code !~ '^[0-9]{7}$' or p_sku is null or length(p_sku)>120
    or jsonb_typeof(p_manifest) is distinct from 'object' or octet_length(p_manifest::text)>16384
    or p_manifest-array['model','training_start','training_end','data_hash','features','library_version','evaluation','predictions']<>'{}'::jsonb
    or p_manifest->>'model' is null or p_manifest->>'model' not in ('arvore','media_7_dias')
    or coalesce(p_manifest->>'data_hash','')!~'^[0-9a-f]{64}$'
    or jsonb_typeof(p_manifest->'predictions') is distinct from 'array' or jsonb_array_length(p_manifest->'predictions')<>30
    or p_manifest->'features' is distinct from '["lag_1","lag_7","media_7","media_28","dia_semana"]'::jsonb
    or (p_manifest->>'training_end')::date>current_date
    or (p_manifest->>'training_end')::date-(p_manifest->>'training_start')::date not between 179 and 1824
    or coalesce(p_manifest->>'library_version','') !~ '^[0-9]+\.[0-9]+\.[0-9]+([a-z0-9.]+)?$'
    or jsonb_typeof(p_manifest->'evaluation') is distinct from 'object'
    or (p_manifest->'evaluation')-array['selecionado','metricas','cortes','treino_ate','limites']<>'{}'::jsonb
    or p_manifest#>>'{evaluation,selecionado}' is distinct from p_manifest->>'model'
    or p_manifest#>>'{evaluation,limites}' is distinct from
      'Validação temporal local; não comprova ganho em dados empresariais nem sazonalidade anual.'
    or jsonb_typeof(p_manifest#>'{evaluation,cortes}') is distinct from 'array'
    or jsonb_array_length(p_manifest#>'{evaluation,cortes}')<>3
    or (p_manifest#>'{evaluation,metricas}')-array['7','14','21','30']<>'{}'::jsonb then
    raise exception 'Manifesto inválido ou excessivo.' using errcode='22023'; end if;
  -- O banco também exige a melhoria mínima registrada no backtest.
  foreach horizon in array array['7','14','21','30'] loop
    metric := p_manifest#>array['evaluation','metricas',horizon];
    if jsonb_typeof(metric) is distinct from 'object' or metric-array['amostras','mae_baseline','mae_modelo','wape_baseline','wape_modelo','vies_modelo']<>'{}'::jsonb
      or (metric->>'amostras')::int is distinct from 3*horizon::int
      or jsonb_typeof(metric->'mae_baseline') is distinct from 'number'
      or jsonb_typeof(metric->'mae_modelo') is distinct from 'number'
      or (metric->>'mae_baseline')::numeric<0 or (metric->>'mae_modelo')::numeric<0
      or jsonb_typeof(metric->'vies_modelo') is distinct from 'number'
      or (metric->>'wape_baseline' is not null and (jsonb_typeof(metric->'wape_baseline')<>'number' or (metric->>'wape_baseline')::numeric<0))
      or (metric->>'wape_modelo' is not null and (jsonb_typeof(metric->'wape_modelo')<>'number' or (metric->>'wape_modelo')::numeric<0))
      or (p_manifest->>'model'='arvore' and ((metric->>'mae_baseline')::numeric<=0
        or (metric->>'mae_modelo')::numeric>(metric->>'mae_baseline')::numeric*0.95)) then
      raise exception 'Avaliação temporal não permite ativar o modelo.' using errcode='22023'; end if;
  end loop;
  if (p_manifest#>>'{evaluation,cortes,0}')::date >= (p_manifest#>>'{evaluation,cortes,1}')::date
    or (p_manifest#>>'{evaluation,cortes,1}')::date >= (p_manifest#>>'{evaluation,cortes,2}')::date
    or (p_manifest#>>'{evaluation,cortes,2}')::date > (p_manifest->>'training_end')::date-29
    or (p_manifest#>>'{evaluation,treino_ate}')::date > (p_manifest->>'training_end')::date-30 then
    raise exception 'Cortes temporais incompatíveis.' using errcode='22023'; end if;
  expected := (p_manifest->>'training_end')::date+1;
  for point in select value from jsonb_array_elements(p_manifest->'predictions') loop
    if jsonb_typeof(point) is distinct from 'object' or point-array['date','units']<>'{}'::jsonb
      or (point->>'date')::date is distinct from expected or jsonb_typeof(point->'units') is distinct from 'number'
      or (point->>'units')::numeric not between 0 and 2147483647 then
      raise exception 'Previsão fora do contrato.' using errcode='22023'; end if;
    expected := expected+1;
  end loop;
  perform pg_advisory_xact_lock(hashtextextended(org::text||p_source_id::text||p_store_id||p_city_code||p_sku,15));
  select id into model from public.commercial_model_runs where organization_id=org and source_id=p_source_id
    and store_id=p_store_id and city_code=p_city_code and sku=p_sku and data_hash=p_manifest->>'data_hash';
  if model is not null then
    if exists(select 1 from public.commercial_model_runs where id=model and manifest<>p_manifest) then
      raise exception 'O histórico já foi avaliado com outro manifesto. Revise a versão.' using errcode='22023'; end if;
    return jsonb_build_object('id',model,'reused',true);
  end if;
  if (select count(*) from public.commercial_model_runs where organization_id=org and source_id=p_source_id
      and store_id=p_store_id and city_code=p_city_code and sku=p_sku)>=12 then
    raise exception 'Limite de versões atingido; revise retenção e backup.' using errcode='54000'; end if;
  insert into public.commercial_model_runs(organization_id,source_id,store_id,city_code,sku,data_hash,manifest)
    values(org,p_source_id,p_store_id,p_city_code,p_sku,p_manifest->>'data_hash',p_manifest) returning id into model;
  select model_id into old from public.commercial_active_models where organization_id=org and source_id=p_source_id
    and store_id=p_store_id and city_code=p_city_code and sku=p_sku;
  insert into public.commercial_active_models(organization_id,source_id,store_id,city_code,sku,model_id,previous_model_id)
    values(org,p_source_id,p_store_id,p_city_code,p_sku,model,old)
    on conflict(organization_id,source_id,store_id,city_code,sku) do update
    set model_id=excluded.model_id,previous_model_id=excluded.previous_model_id;
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'commercial.model',model::text,jsonb_build_object('model',p_manifest->>'model'));
  return jsonb_build_object('id',model,'reused',false);
end $$;

create function private.rollback_commercial_model(p_source_id uuid,p_store_id text,p_city_code text,p_sku text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; active public.commercial_active_models;
begin
  org := private.require_master(); perform private.require_permission('commercial.manage');
  perform private.sync_source(p_source_id,p_store_id);
  perform pg_advisory_xact_lock(hashtextextended(org::text||p_source_id::text||p_store_id||p_city_code||p_sku,15));
  select * into active from public.commercial_active_models where organization_id=org and source_id=p_source_id
    and store_id=p_store_id and city_code=p_city_code and sku=p_sku for update;
  if active.previous_model_id is null then raise exception 'Não há modelo anterior.' using errcode='22023'; end if;
  update public.commercial_active_models set model_id=active.previous_model_id,previous_model_id=active.model_id
    where organization_id=org and source_id=p_source_id and store_id=p_store_id and city_code=p_city_code and sku=p_sku;
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'commercial.model.rollback',active.previous_model_id::text,'{}');
  return jsonb_build_object('restored',true);
end $$;

create function private.commercial_model_status() returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_master(); perform private.require_permission('commercial.manage');
  return coalesce((select jsonb_agg(jsonb_build_object('id',r.id,'city_code',r.city_code,'sku',r.sku,
    'model',r.manifest->>'model','training_end',r.manifest->>'training_end','created_at',r.created_at,
    'evaluation',r.manifest->'evaluation','bytes',octet_length(r.manifest::text),'previous_available',a.previous_model_id is not null)
    order by r.created_at desc) from public.commercial_active_models a join public.commercial_model_runs r
    on r.id=a.model_id and r.organization_id=a.organization_id where r.organization_id=org),'[]'::jsonb);
end $$;

create function private.storage_overview() returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid; database_bytes bigint; storage_bytes bigint; count_objects bigint; known_objects bigint; tables jsonb;
begin
  org := private.require_master();
  -- A métrica do projeto não deve revelar ocupação de outras organizações.
  if (select count(*) from public.organizations)<>1 then
    return jsonb_build_object('database_bytes',null,'storage_bytes',null,'reason','Projeto com múltiplas organizações.');
  end if;
  database_bytes := pg_database_size(current_database());
  select coalesce(jsonb_agg(jsonb_build_object('name',relname,'bytes',bytes) order by bytes desc),'[]'::jsonb) into tables
    from (select c.relname,pg_total_relation_size(c.oid) bytes from pg_catalog.pg_class c
      join pg_catalog.pg_namespace n on n.oid=c.relnamespace
      where n.nspname='public' and c.relkind in ('r','m') order by bytes desc limit 10) sizes;
  if to_regclass('storage.objects') is not null then
    execute $q$select count(*),count(*) filter(where metadata->>'size' ~ '^[0-9]+$'),
      coalesce(sum(case when metadata->>'size' ~ '^[0-9]+$' then (metadata->>'size')::bigint else 0 end),0)
      from storage.objects$q$ into count_objects,known_objects,storage_bytes;
    if count_objects<>known_objects then storage_bytes := null; end if;
  end if;
  return jsonb_build_object('database_bytes',database_bytes,'storage_bytes',storage_bytes,'tables',tables,
    'reason',case when storage_bytes is null then 'Tamanho dos objetos não disponível.' else null end);
end $$;

create function private.record_storage_measurement() returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; current jsonb;
begin
  org := private.require_master();
  current := private.storage_overview();
  if current->>'database_bytes' is null then raise exception 'Medição não disponível.' using errcode='42501'; end if;
  insert into public.storage_measurements(organization_id,measured_on,database_bytes,storage_bytes)
    values(org,current_date,(current->>'database_bytes')::bigint,(current->>'storage_bytes')::bigint)
    on conflict(organization_id,measured_on) do update set database_bytes=excluded.database_bytes,
      storage_bytes=excluded.storage_bytes,measured_at=now();
  return jsonb_build_object('recorded',true);
end $$;
create function private.storage_history() returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_master();
  return coalesce((select jsonb_agg(to_jsonb(x)-'organization_id' order by x.measured_on desc)
    from (select * from public.storage_measurements where organization_id=org order by measured_on desc limit 365) x),'[]'::jsonb);
end $$;

create function public.refresh_commercial_daily(p_run_id uuid) returns jsonb language sql security invoker set search_path=''
  as $$select private.refresh_commercial_daily(p_run_id)$$;
create function public.commercial_sources() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.commercial_sources()$$;
create function public.commercial_daily(p_source_id uuid,p_store_id text,p_start date,p_end date) returns jsonb
language sql stable security invoker set search_path='' as $$select private.commercial_daily(p_source_id,p_store_id,p_start,p_end)$$;
create function public.commercial_insights(p_source_id uuid,p_store_id text,p_dimension text,p_offset integer default 0)
returns jsonb language sql stable security invoker set search_path=''
  as $$select private.commercial_insights(p_source_id,p_store_id,p_dimension,p_offset)$$;
create function public.storage_overview() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.storage_overview()$$;
create function public.record_storage_measurement() returns jsonb language sql security invoker set search_path=''
  as $$select private.record_storage_measurement()$$;
create function public.storage_history() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.storage_history()$$;
create function public.publish_commercial_batch(p_source_id uuid,p_store_id text,p_start date,p_end date,p_city_basis text,p_items jsonb)
returns jsonb language sql security invoker set search_path=''
  as $$select private.publish_commercial_batch(p_source_id,p_store_id,p_start,p_end,p_city_basis,p_items)$$;
create function public.rollback_commercial_batch(p_source_id uuid,p_store_id text)
returns jsonb language sql security invoker set search_path=''
  as $$select private.rollback_commercial_batch(p_source_id,p_store_id)$$;
create function public.save_commercial_model(p_source_id uuid,p_store_id text,p_city_code text,p_sku text,p_manifest jsonb)
returns jsonb language sql security invoker set search_path=''
  as $$select private.save_commercial_model(p_source_id,p_store_id,p_city_code,p_sku,p_manifest)$$;
create function public.rollback_commercial_model(p_source_id uuid,p_store_id text,p_city_code text,p_sku text)
returns jsonb language sql security invoker set search_path=''
  as $$select private.rollback_commercial_model(p_source_id,p_store_id,p_city_code,p_sku)$$;
create function public.commercial_model_status() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.commercial_model_status()$$;
revoke all on function private.publish_commercial_batch(uuid,text,date,date,text,jsonb),private.rollback_commercial_batch(uuid,text),
  private.save_commercial_model(uuid,text,text,text,jsonb),private.rollback_commercial_model(uuid,text,text,text),
  private.commercial_model_status() from public,anon,authenticated;
revoke all on function public.publish_commercial_batch(uuid,text,date,date,text,jsonb),public.rollback_commercial_batch(uuid,text),
  public.save_commercial_model(uuid,text,text,text,jsonb),public.rollback_commercial_model(uuid,text,text,text),
  public.commercial_model_status() from public,anon;
grant execute on function private.publish_commercial_batch(uuid,text,date,date,text,jsonb),private.rollback_commercial_batch(uuid,text),
  private.save_commercial_model(uuid,text,text,text,jsonb),private.rollback_commercial_model(uuid,text,text,text),
  private.commercial_model_status() to authenticated;
grant execute on function public.publish_commercial_batch(uuid,text,date,date,text,jsonb),public.rollback_commercial_batch(uuid,text),
  public.save_commercial_model(uuid,text,text,text,jsonb),public.rollback_commercial_model(uuid,text,text,text),
  public.commercial_model_status() to authenticated;
revoke all on function private.refresh_commercial_daily(uuid),private.commercial_daily(uuid,text,date,date),
  private.commercial_insights(uuid,text,text,integer),private.commercial_sources(),private.storage_overview(),
  private.record_storage_measurement(),private.storage_history() from public,anon,authenticated;
revoke all on function public.refresh_commercial_daily(uuid),public.commercial_daily(uuid,text,date,date),
  public.commercial_insights(uuid,text,text,integer),public.commercial_sources(),public.storage_overview(),
  public.record_storage_measurement(),public.storage_history() from public,anon;
grant execute on function private.refresh_commercial_daily(uuid),private.commercial_daily(uuid,text,date,date),
  private.commercial_insights(uuid,text,text,integer),private.commercial_sources(),private.storage_overview(),
  private.record_storage_measurement(),private.storage_history() to authenticated;
grant execute on function public.refresh_commercial_daily(uuid),public.commercial_daily(uuid,text,date,date),
  public.commercial_insights(uuid,text,text,integer),public.commercial_sources(),public.storage_overview(),
  public.record_storage_measurement(),public.storage_history() to authenticated;
