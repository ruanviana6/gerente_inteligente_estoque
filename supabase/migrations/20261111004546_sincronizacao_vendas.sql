begin;
-- Cópia analítica versionada. Nenhuma função acessa ou modifica o ERP.
-- Os limites conservadores reduzem o risco de ultrapassar o plano gratuito.
create table public.integration_sources (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  source_key text not null check (source_key ~ '^[a-zA-Z0-9_-]{1,80}$'),
  name text not null check(length(btrim(name)) between 1 and 120),
  stores text[] not null check(cardinality(stores) between 1 and 30),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique(organization_id,id), unique(organization_id,source_key)
);
create table public.sync_runs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null,
  source_id uuid not null,
  store_id text not null,
  author_id uuid not null references auth.users(id),
  request_key uuid not null,
  content_hash text not null check(content_hash ~ '^[0-9a-f]{64}$'),
  claim_token uuid not null default gen_random_uuid(),
  status text not null check(status in ('staging','previewed','completed','cancelled','interrupted')),
  period_start date not null,
  period_end date not null,
  expected_rows integer not null check(expected_rows between 0 and 20000),
  expected_quantity numeric not null check(expected_quantity>=0 and expected_quantity<1e12),
  checkpoint integer not null default 0,
  row_count integer not null default 0,
  net_quantity numeric not null default 0,
  changed_rows integer not null default 0,
  coverage_confirmed boolean not null default false,
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  lease_until timestamptz not null default now()+interval '30 minutes',
  check(period_start<=period_end),
  foreign key(organization_id,source_id) references public.integration_sources(organization_id,id),
  unique(organization_id,id), unique(organization_id,source_id,store_id,request_key)
);
create unique index sync_one_active_scope on public.sync_runs(source_id,store_id)
  where status in ('staging','previewed');
create index sync_runs_scope on public.sync_runs(organization_id,source_id,store_id,finished_at desc);
create table public.sync_pages (
  organization_id uuid not null, run_id uuid not null, page integer not null,
  fingerprint text not null, primary key(run_id,page),
  foreign key(organization_id,run_id) references public.sync_runs(organization_id,id)
);
create table public.sync_staging (
  organization_id uuid not null, run_id uuid not null,
  document_id text not null, line_id text not null,
  payload jsonb not null,
  primary key(run_id,document_id,line_id),
  foreign key(organization_id,run_id) references public.sync_runs(organization_id,id)
);
create table public.sales_line_versions (
  id bigint generated always as identity primary key,
  organization_id uuid not null, source_id uuid not null, store_id text not null,
  document_id text not null, line_id text not null,
  run_id uuid not null,
  payload jsonb not null,
  observed_at timestamptz not null default now(),
  unique(organization_id,id),
  foreign key(organization_id,run_id) references public.sync_runs(organization_id,id),
  foreign key(organization_id,source_id) references public.integration_sources(organization_id,id)
);
create index sales_versions_scope on public.sales_line_versions(organization_id,source_id,store_id,document_id,line_id);
create table public.sales_lines_current (
  organization_id uuid not null, source_id uuid not null, store_id text not null,
  document_id text not null, line_id text not null, version_id bigint not null,
  primary key(organization_id,source_id,store_id,document_id,line_id),
  foreign key(organization_id,version_id) references public.sales_line_versions(organization_id,id),
  foreign key(organization_id,source_id) references public.integration_sources(organization_id,id)
);

create function private.sync_source(p_source_id uuid,p_store_id text) returns uuid
language plpgsql stable security definer set search_path='' as $$
declare org uuid:=private.require_master();
begin
  perform private.require_permission('integrations.manage');
  if not exists(select 1 from public.integration_sources where id=p_source_id and organization_id=org
    and active and p_store_id=any(stores)) then
    raise exception 'Fonte ou loja não autorizada.' using errcode='42501';
  end if;
  return org;
end $$;
create function private.integration_sources() returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid:=private.require_permission('trends.view');
begin
  return coalesce((select jsonb_agg(jsonb_build_object('id',s.id,'source_key',s.source_key,'name',s.name,
    'stores',s.stores,'active',s.active,'last_sync',(
      select jsonb_build_object('finished_at',r.finished_at,'period_start',r.period_start,'period_end',r.period_end,
        'rows',r.row_count,'net_quantity',r.net_quantity,'duration_seconds',extract(epoch from r.finished_at-r.created_at),
        'coverage_confirmed',r.coverage_confirmed)
      from public.sync_runs r where r.source_id=s.id and r.organization_id=org and r.status='completed'
      order by r.finished_at desc,r.id desc limit 1)) order by s.name)
      from public.integration_sources s where s.organization_id=org and s.active),'[]'::jsonb);
end $$;
create function private.integration_register_source(p_key text,p_name text,p_stores text[]) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.require_master(); src public.integration_sources;
begin
  perform private.require_permission('integrations.manage');
  if p_key is null or p_key !~ '^[a-zA-Z0-9_-]{1,80}$' or p_name is null or length(btrim(p_name)) not between 1 and 120
    or p_stores is null or cardinality(p_stores) not between 1 and 30
    or exists(select 1 from unnest(p_stores) s where s is null or length(btrim(s)) not between 1 and 120)
    or cardinality(p_stores)<>(select count(distinct s) from unnest(p_stores) s) then
    raise exception 'Configuração de fonte inválida.' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(org::text,11));
  if (select count(*) from public.integration_sources where organization_id=org)>=10
    and not exists(select 1 from public.integration_sources where organization_id=org and source_key=p_key) then
    raise exception 'Limite de fontes atingido.' using errcode='54000';
  end if;
  insert into public.integration_sources(organization_id,source_key,name,stores) values(org,p_key,btrim(p_name),p_stores)
    on conflict(organization_id,source_key) do update set name=excluded.name,stores=excluded.stores
    returning * into src;
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'integrations.source',src.id::text,jsonb_build_object('stores_count',cardinality(p_stores)));
  return jsonb_build_object('id',src.id,'source_key',src.source_key,'name',src.name,'stores',src.stores);
end $$;
create function private.sync_run_payload(r public.sync_runs) returns jsonb
language sql immutable set search_path='' as $$
  select jsonb_build_object('id',r.id,'claim_token',r.claim_token,'status',r.status,'checkpoint',r.checkpoint,
    'row_count',r.row_count,'net_quantity',r.net_quantity,'changed_rows',r.changed_rows,
    'period_start',r.period_start,'period_end',r.period_end,'coverage_confirmed',r.coverage_confirmed)
$$;
create function private.sync_begin(p_source_id uuid,p_store_id text,p_request_key uuid,p_period_start date,
  p_period_end date,p_expected_rows integer,p_expected_quantity numeric,p_content_hash text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.sync_source(p_source_id,p_store_id); r public.sync_runs;
begin
  if p_content_hash is null or p_content_hash !~ '^[0-9a-f]{64}$'
    or p_request_key is null or p_period_start is null or p_period_end is null or p_period_start>p_period_end
    or p_period_end>(now() at time zone 'America/Fortaleza')::date
    or p_expected_rows is null or p_expected_rows not between 0 and 20000
    or p_expected_quantity is null or p_expected_quantity<0 or p_expected_quantity>=1e12 then
    raise exception 'Abrangência ou reconciliação inválida.' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_source_id::text||'/'||p_store_id,12));
  select * into r from public.sync_runs where organization_id=org and source_id=p_source_id and store_id=p_store_id
    and request_key=p_request_key for update;
  if found then
    if r.period_start<>p_period_start or r.period_end<>p_period_end or r.expected_rows<>p_expected_rows
      or r.expected_quantity<>p_expected_quantity or r.content_hash<>p_content_hash or r.status='cancelled' then
      raise exception 'Identificador de execução incompatível.' using errcode='22023';
    end if;
    if r.status='completed' then return private.sync_run_payload(r); end if;
  end if;
  -- Abandono retém checkpoints; só outra execução com o mesmo escopo perde a concessão.
  update public.sync_runs set status='interrupted'
    where source_id=p_source_id and store_id=p_store_id and status in ('staging','previewed') and lease_until<now();
  if exists(select 1 from public.sync_runs where source_id=p_source_id and store_id=p_store_id
      and status in ('staging','previewed') and id is distinct from r.id) then
    raise exception 'Há sincronização em andamento para esta fonte e loja.' using errcode='55000';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(org::text,13));
  if r.id is not null then
    update public.sync_runs set status='staging',claim_token=gen_random_uuid(),lease_until=now()+interval '30 minutes'
      where id=r.id returning * into r;
  else
    if (select count(*) from public.sync_runs where organization_id=org)>=1000 or
      (select count(*) from public.sync_runs where organization_id=org and status not in ('completed','cancelled'))>=20 then
      raise exception 'Cancele execuções abandonadas antes de iniciar outras.' using errcode='54000';
    end if;
    insert into public.sync_runs(organization_id,source_id,store_id,author_id,request_key,status,
      period_start,period_end,expected_rows,expected_quantity,content_hash)
      values(org,p_source_id,p_store_id,auth.uid(),p_request_key,'staging',p_period_start,p_period_end,p_expected_rows,p_expected_quantity,p_content_hash)
      returning * into r;
  end if;
  return private.sync_run_payload(r);
end $$;
create function private.sync_lock_run(p_run_id uuid,p_claim_token uuid) returns public.sync_runs
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.require_master(); r public.sync_runs;
begin
  perform private.require_permission('integrations.manage');
  select * into r from public.sync_runs where id=p_run_id and organization_id=org for update;
  if not found or r.claim_token is distinct from p_claim_token or r.status not in ('staging','previewed')
    or r.lease_until<now() then raise exception 'Execução não autorizada ou concessão expirada.' using errcode='42501'; end if;
  perform private.sync_source(r.source_id,r.store_id);
  return r;
end $$;
create function private.validate_sales_line(p_row jsonb,p_source_id uuid,p_store_id text,p_start date,p_end date)
returns jsonb language plpgsql stable set search_path='' as $$
declare k text; q numeric; ret numeric; d date;
begin
  if jsonb_typeof(p_row) is distinct from 'object' or exists(select 1 from jsonb_object_keys(p_row) x
    where x not in ('source_id','store_id','document_id','line_id','product_id','sku','product_name','category','supplier',
      'sale_date','status','quantity','returned_quantity','source_updated_at')) then
    raise exception 'Campos de venda inválidos.' using errcode='22023';
  end if;
  foreach k in array array['source_id','store_id','document_id','line_id','product_id','sku','sale_date','status'] loop
    if jsonb_typeof(p_row->k) is distinct from 'string' or length(btrim(p_row->>k)) not between 1 and 120 then
      raise exception 'Identificador ou data inválida.' using errcode='22023';
    end if;
  end loop;
  if p_row->>'source_id'<>p_source_id::text or p_row->>'store_id'<>p_store_id
    or p_row->>'sale_date' !~ '^\d{4}-\d{2}-\d{2}$'
    or p_row->>'status' not in ('potential','separating','completed','cancelled','returned','unknown') then
    raise exception 'Escopo ou status da venda inválido.' using errcode='22023';
  end if;
  d:=(p_row->>'sale_date')::date;
  if d<p_start or d>p_end then raise exception 'Venda fora do período.' using errcode='22023'; end if;
  foreach k in array array['product_name','category','supplier'] loop
    if p_row ? k and (jsonb_typeof(p_row->k) is distinct from 'string' or length(p_row->>k)>300) then
      raise exception 'Descrição de produto inválida.' using errcode='22023';
    end if;
  end loop;
  foreach k in array array['quantity','returned_quantity'] loop
    if jsonb_typeof(p_row->k) is distinct from 'number' or p_row->>k !~ '^[0-9]+(\.[0-9]{1,6})?$'
      or (p_row->>k)::numeric>1e9 then raise exception 'Quantidade inválida.' using errcode='22023'; end if;
  end loop;
  q:=(p_row->>'quantity')::numeric; ret:=(p_row->>'returned_quantity')::numeric;
  if ret>q then raise exception 'Devolução maior que a quantidade.' using errcode='22023'; end if;
  if p_row ? 'source_updated_at' and p_row->'source_updated_at'<>'null'::jsonb then
    if jsonb_typeof(p_row->'source_updated_at')<>'string' or length(p_row->>'source_updated_at')>40
      or p_row->>'source_updated_at' !~ '^\d{4}-\d{2}-\d{2}T.*(Z|[+-]\d{2}:\d{2})$'
      or (p_row->>'source_updated_at')::timestamptz>now()+interval '5 minutes' then
      raise exception 'Instante da alteração inválido.' using errcode='22023';
    end if;
  end if;
  return p_row;
end $$;
create function private.sync_stage(p_run_id uuid,p_claim_token uuid,p_page integer,p_rows jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare r public.sync_runs:=private.sync_lock_run(p_run_id,p_claim_token); fp text; row jsonb; n integer;
begin
  if p_page is null or p_page not between 1 and 1000 or jsonb_typeof(p_rows) is distinct from 'array'
    or jsonb_array_length(p_rows) not between 1 and 100 or octet_length(p_rows::text)>1048576 then
    raise exception 'Página de sincronização inválida.' using errcode='22023';
  end if;
  fp:=encode(sha256(convert_to(p_rows::text,'UTF8')),'hex');
  if exists(select 1 from public.sync_pages where run_id=r.id and page=p_page) then
    if not exists(select 1 from public.sync_pages where run_id=r.id and page=p_page and fingerprint=fp) then
      raise exception 'Página reimportada com conteúdo diferente.' using errcode='22023';
    end if;
    return private.sync_run_payload(r);
  end if;
  if r.status<>'staging' or p_page<>r.checkpoint+1 then
    raise exception 'Checkpoint fora de sequência.' using errcode='22023'; end if;
  n:=jsonb_array_length(p_rows);
  -- A cota é da organização: outras lojas também disputam o mesmo limite.
  -- Ordem consistente com commit: primeiro o lote, depois a cota da organização.
  perform pg_advisory_xact_lock(hashtextextended(r.organization_id::text,13));
  if r.row_count+n>r.expected_rows or (select count(*) from public.sync_staging where organization_id=r.organization_id)+n>20000 then
    raise exception 'Limite ou total de registros excedido.' using errcode='54000'; end if;
  for row in select value from jsonb_array_elements(p_rows) loop
    row:=private.validate_sales_line(row,r.source_id,r.store_id,r.period_start,r.period_end);
    if exists(select 1 from public.sync_staging where run_id=r.id and document_id=row->>'document_id' and line_id=row->>'line_id') then
      raise exception 'Documento/linha duplicado na execução.' using errcode='22023'; end if;
    insert into public.sync_staging values(r.organization_id,r.id,row->>'document_id',row->>'line_id',row);
  end loop;
  insert into public.sync_pages values(r.organization_id,r.id,p_page,fp);
  update public.sync_runs set checkpoint=p_page,row_count=row_count+n,lease_until=now()+interval '30 minutes'
    where id=r.id returning * into r;
  return private.sync_run_payload(r);
end $$;
create function private.sync_preview(p_run_id uuid,p_claim_token uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare r public.sync_runs:=private.sync_lock_run(p_run_id,p_claim_token); q numeric;
begin
  select coalesce(sum(case when payload->>'status'='completed'
    then (payload->>'quantity')::numeric-(payload->>'returned_quantity')::numeric else 0 end),0) into q
    from public.sync_staging where run_id=r.id;
  if r.row_count<>r.expected_rows or q<>r.expected_quantity then
    raise exception 'Reconciliação de quantidade ou registros não confere.' using errcode='22023'; end if;
  update public.sync_runs set status='previewed',net_quantity=q,lease_until=now()+interval '30 minutes'
    where id=r.id returning * into r;
  return private.sync_run_payload(r)||jsonb_build_object('sample',coalesce((select jsonb_agg(payload) from
    (select payload from public.sync_staging where run_id=r.id order by document_id,line_id limit 10) t),'[]'::jsonb));
end $$;
create function private.sync_commit(p_run_id uuid,p_claim_token uuid,p_coverage_confirmed boolean default false) returns jsonb
language plpgsql security definer set search_path='' as $$
declare r public.sync_runs; row record; old jsonb; v bigint; changed integer:=0; reconciled_rows integer; reconciled_quantity numeric; org uuid:=private.require_master();
begin
  perform private.require_permission('integrations.manage');
  select * into r from public.sync_runs where id=p_run_id and organization_id=org for update;
  if found and r.status='completed' and r.claim_token=p_claim_token then return private.sync_run_payload(r); end if;
  r:=private.sync_lock_run(p_run_id,p_claim_token);
  if r.status<>'previewed' then raise exception 'Confira a pré-visualização antes de confirmar.' using errcode='22023'; end if;
  perform pg_advisory_xact_lock(hashtextextended(org::text,13));
  if (select count(*) from public.sales_lines_current where organization_id=org)+r.row_count>100000
    or (select count(*) from public.sales_line_versions where organization_id=org)+r.row_count>200000 then
    raise exception 'Limite analítico conservador atingido. Confira o armazenamento.' using errcode='54000'; end if;
  for row in select * from public.sync_staging where run_id=r.id order by document_id,line_id loop
    select v.payload into old from public.sales_lines_current c join public.sales_line_versions v on v.id=c.version_id
      where c.organization_id=org and c.source_id=r.source_id and c.store_id=r.store_id
        and c.document_id=row.document_id and c.line_id=row.line_id;
    if old is not null and old=row.payload then continue; end if;
    -- Sem horário comparável, recusar substituição em vez de ressuscitar dados antigos.
    if old is not null and (old->>'source_updated_at' is null or row.payload->>'source_updated_at' is null
      or (row.payload->>'source_updated_at')::timestamptz<=(old->>'source_updated_at')::timestamptz) then
      raise exception 'Versão alterada sem instante posterior comprovado. Reconcilie a origem.' using errcode='22023'; end if;
    insert into public.sales_line_versions(organization_id,source_id,store_id,document_id,line_id,run_id,payload)
      values(org,r.source_id,r.store_id,row.document_id,row.line_id,r.id,row.payload) returning id into v;
    insert into public.sales_lines_current values(org,r.source_id,r.store_id,row.document_id,row.line_id,v)
      on conflict(organization_id,source_id,store_id,document_id,line_id) do update set version_id=excluded.version_id;
    changed:=changed+1;
  end loop;
  if p_coverage_confirmed is true then
    -- Ausência na resposta não prova exclusão. Uma declaração de período completo
    -- exige que a cópia final também reconcilie, sem apagar linhas antigas.
    select count(*),coalesce(sum(case when v.payload->>'status'='completed' then
      (v.payload->>'quantity')::numeric-(v.payload->>'returned_quantity')::numeric else 0 end),0)
      into reconciled_rows,reconciled_quantity
      from public.sales_lines_current c join public.sales_line_versions v on v.id=c.version_id
      where c.organization_id=org and c.source_id=r.source_id and c.store_id=r.store_id
        and (v.payload->>'sale_date')::date between r.period_start and r.period_end;
    if reconciled_rows<>r.expected_rows or reconciled_quantity<>r.expected_quantity then
      raise exception 'A cópia consolidada não reconcilia com o período completo. Confira linhas ausentes e cancelamentos explícitos na origem.'
        using errcode='22023';
    end if;
  end if;
  update public.sync_runs set status='completed',changed_rows=changed,coverage_confirmed=coalesce(p_coverage_confirmed,false),
    finished_at=now() where id=r.id returning * into r;
  -- O histórico definitivo permanece nas versões; staging não é histórico operacional.
  delete from public.sync_staging where run_id=r.id;
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'integrations.sync',r.id::text,jsonb_build_object('rows',r.row_count,'changed',changed));
  return private.sync_run_payload(r);
end $$;
create function private.sync_cancel(p_run_id uuid,p_claim_token uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.require_master(); r public.sync_runs;
begin
  perform private.require_permission('integrations.manage');
  select * into r from public.sync_runs where id=p_run_id and organization_id=org for update;
  if not found or r.status not in ('staging','previewed','interrupted') or
    (r.claim_token is distinct from p_claim_token and not
      (p_claim_token is null and (r.status='interrupted' or r.lease_until<now()))) then
    raise exception 'Cancelamento não autorizado.' using errcode='42501';
  end if;
  update public.sync_runs set status='cancelled',finished_at=now(),claim_token=gen_random_uuid()
    where id=r.id returning * into r;
  delete from public.sync_staging where run_id=r.id;
  return private.sync_run_payload(r);
end $$;
create function private.sales_payload(p_source_id uuid,p_store_id text,p_start date,p_end date) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid:=private.require_permission('trends.view'); rows jsonb; coverage jsonb;
begin
  if p_start is null or p_end is null or p_start>p_end or p_end-p_start>3653 then
    raise exception 'Período de análise inválido.' using errcode='22023'; end if;
  if not exists(select 1 from public.integration_sources where id=p_source_id and organization_id=org and active
    and p_store_id=any(stores)) then raise exception 'Fonte ou loja não autorizada.' using errcode='42501'; end if;
  if (select count(*) from public.sales_lines_current c join public.sales_line_versions v on v.id=c.version_id
    where c.organization_id=org and c.source_id=p_source_id and c.store_id=p_store_id
    and (v.payload->>'sale_date')::date between p_start and p_end)>20000 then
    raise exception 'Reduza o período para até vinte mil linhas.' using errcode='54000'; end if;
  select coalesce(jsonb_agg(v.payload order by v.payload->>'sale_date',c.document_id,c.line_id),'[]'::jsonb) into rows
    from public.sales_lines_current c join public.sales_line_versions v on v.id=c.version_id
    where c.organization_id=org and c.source_id=p_source_id and c.store_id=p_store_id
    and (v.payload->>'sale_date')::date between p_start and p_end;
  -- Junta apenas intervalos confirmados adjacentes/sobrepostos do mesmo escopo.
  -- max anterior, em vez de lag(end), conserva ilhas com intervalos aninhados.
  with observed as (
    select period_start,period_end,finished_at,id,
      max(period_end) over(order by period_start,period_end,id rows between unbounded preceding and 1 preceding) previous_end
    from public.sync_runs where organization_id=org and source_id=p_source_id and store_id=p_store_id
      and status='completed' and coverage_confirmed
  ), boundaries as (
    select *,case when previous_end is null or period_start>previous_end+1 then 1 else 0 end new_island from observed
  ), grouped as (
    select *,sum(new_island) over(order by period_start,period_end,id rows unbounded preceding) island from boundaries
  ), islands as (
    select min(period_start) coverage_start,max(period_end) coverage_end,max(finished_at) last_sync,count(*) runs
    from grouped group by island
  )
  select jsonb_build_object('coverage_start',coverage_start,'coverage_end',coverage_end,'coverage_verified',true,
    'last_sync',last_sync,'coverage_runs',runs) into coverage from islands
    where coverage_start<=p_start and coverage_end>=p_end order by last_sync desc limit 1;
  return jsonb_build_object('rows',rows,'metadata',coalesce(coverage,jsonb_build_object('coverage_verified',false)),
    'source_id',p_source_id,'store_id',p_store_id);
end $$;

create function private.sync_history(p_source_id uuid,p_store_id text) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid:=private.sync_source(p_source_id,p_store_id);
begin
  return coalesce((select jsonb_agg(payload order by created_at desc,id desc) from
    (select r.id,r.created_at,jsonb_build_object('id',r.id,'status',r.status,'checkpoint',r.checkpoint,
      'row_count',r.row_count,'net_quantity',r.net_quantity,'changed_rows',r.changed_rows,
      'period_start',r.period_start,'period_end',r.period_end,'coverage_confirmed',r.coverage_confirmed,
      'created_at',r.created_at,'finished_at',r.finished_at,
      'duration_seconds',extract(epoch from coalesce(r.finished_at,now())-r.created_at)) payload
      from public.sync_runs r where r.organization_id=org and r.source_id=p_source_id and r.store_id=p_store_id
      order by r.created_at desc,r.id desc limit 20) t),'[]'::jsonb);
end $$;

-- Nenhuma tabela nova aceita SELECT/DML direto; a API estreita aplica escopo e limites.
do $$declare t text; begin
  foreach t in array array['integration_sources','sync_runs','sync_pages','sync_staging','sales_line_versions','sales_lines_current'] loop
    execute format('alter table public.%I enable row level security',t);
    execute format('revoke all on public.%I from public,anon,authenticated,service_role',t);
  end loop;
end $$;
create function public.integration_sources() returns jsonb language sql security invoker set search_path='' as $$
  select private.integration_sources()
$$;
create function public.integration_register_source(p_key text,p_name text,p_stores text[]) returns jsonb language sql security invoker set search_path='' as $$
  select private.integration_register_source(p_key,p_name,p_stores)
$$;
create function public.sync_begin(p_source_id uuid,p_store_id text,p_request_key uuid,p_period_start date,p_period_end date,p_expected_rows integer,p_expected_quantity numeric,p_content_hash text) returns jsonb language sql security invoker set search_path='' as $$
  select private.sync_begin(p_source_id,p_store_id,p_request_key,p_period_start,p_period_end,p_expected_rows,p_expected_quantity,p_content_hash)
$$;
create function public.sync_stage(p_run_id uuid,p_claim_token uuid,p_page integer,p_rows jsonb) returns jsonb language sql security invoker set search_path='' as $$
  select private.sync_stage(p_run_id,p_claim_token,p_page,p_rows)
$$;
create function public.sync_preview(p_run_id uuid,p_claim_token uuid) returns jsonb language sql security invoker set search_path='' as $$
  select private.sync_preview(p_run_id,p_claim_token)
$$;
create function public.sync_commit(p_run_id uuid,p_claim_token uuid,p_coverage_confirmed boolean default false) returns jsonb language sql security invoker set search_path='' as $$
  select private.sync_commit(p_run_id,p_claim_token,p_coverage_confirmed)
$$;
create function public.sync_cancel(p_run_id uuid,p_claim_token uuid) returns jsonb language sql security invoker set search_path='' as $$
  select private.sync_cancel(p_run_id,p_claim_token)
$$;
create function public.sales_payload(p_source_id uuid,p_store_id text,p_start date,p_end date) returns jsonb language sql security invoker set search_path='' as $$
  select private.sales_payload(p_source_id,p_store_id,p_start,p_end)
$$;

do $$declare f record; begin
  for f in select p.oid::regprocedure signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname in ('public','private') and p.proname in (
      'sync_source','integration_sources','integration_register_source','sync_run_payload','sync_begin',
      'sync_lock_run','validate_sales_line','sync_stage','sync_preview','sync_commit','sync_cancel','sales_payload') loop
    execute 'revoke all on function '||f.signature||' from public,anon,authenticated,service_role';
  end loop;
end $$;
grant execute on function public.integration_sources(), private.integration_sources() to authenticated;
grant execute on function public.integration_register_source(text,text,text[]), private.integration_register_source(text,text,text[]) to authenticated;
grant execute on function public.sync_begin(uuid,text,uuid,date,date,integer,numeric,text), private.sync_begin(uuid,text,uuid,date,date,integer,numeric,text) to authenticated;
grant execute on function public.sync_stage(uuid,uuid,integer,jsonb), private.sync_stage(uuid,uuid,integer,jsonb) to authenticated;
grant execute on function public.sync_preview(uuid,uuid), private.sync_preview(uuid,uuid) to authenticated;
grant execute on function public.sync_commit(uuid,uuid,boolean), private.sync_commit(uuid,uuid,boolean) to authenticated;
grant execute on function public.sync_cancel(uuid,uuid), private.sync_cancel(uuid,uuid) to authenticated;
grant execute on function public.sales_payload(uuid,text,date,date), private.sales_payload(uuid,text,date,date) to authenticated;

create function public.sync_history(p_source_id uuid,p_store_id text) returns jsonb
language sql security invoker set search_path='' as $$select private.sync_history(p_source_id,p_store_id)$$;
revoke all on function public.sync_history(uuid,text),private.sync_history(uuid,text) from public,anon,authenticated,service_role;
grant execute on function public.sync_history(uuid,text),private.sync_history(uuid,text) to authenticated;

create function private.sync_authorize_source(p_source_id uuid,p_store_id text) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid:=private.sync_source(p_source_id,p_store_id);
begin return jsonb_build_object('organization_id',org,'source_id',p_source_id,'store_id',p_store_id); end $$;
create function public.sync_authorize_source(p_source_id uuid,p_store_id text) returns jsonb
language sql stable security invoker set search_path='' as $$select private.sync_authorize_source(p_source_id,p_store_id)$$;
revoke all on function public.sync_authorize_source(uuid,text),private.sync_authorize_source(uuid,text) from public,anon,authenticated,service_role;
grant execute on function public.sync_authorize_source(uuid,text),private.sync_authorize_source(uuid,text) to authenticated;

notify pgrst,'reload schema';
commit;
