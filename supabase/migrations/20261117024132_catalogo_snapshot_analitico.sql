begin;
-- Fotografia analítica de estoque por loja. Não modifica inventário operacional nem ERP.
-- Escrita somente por RPC autenticada Master, após reconciliação integral e confirmação.
create table public.catalog_stock_batches (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  source_id uuid not null,
  store_id text not null check (length(btrim(store_id)) between 1 and 120),
  author_id uuid not null references auth.users(id),
  checksum text not null check (checksum ~ '^[0-9a-f]{64}$'),
  product_count integer not null check (product_count between 1 and 1000),
  total_stock numeric(20,6) not null check (total_stock between 0 and 1000000000000),
  missing_variation_ids integer not null check (missing_variation_ids between 0 and 100000),
  missing_variation_codes integer not null check (missing_variation_codes between 0 and 100000),
  created_at timestamptz not null default now(),
  unique(organization_id,id),
  unique(organization_id,source_id,store_id,checksum),
  foreign key(organization_id,source_id) references public.integration_sources(organization_id,id)
);
create index catalog_stock_batches_scope on public.catalog_stock_batches
  (organization_id,source_id,store_id,created_at desc);

create table public.catalog_stock_rows (
  organization_id uuid not null,
  batch_id uuid not null,
  product_id text not null check (product_id ~ '^[0-9]{1,30}$'),
  codigo_interno text check (
    codigo_interno is null or
    (length(btrim(codigo_interno)) between 1 and 100
     and codigo_interno !~ '[<>[:cntrl:]]')
  ),
  estoque numeric(18,6) not null check (estoque between 0 and 1000000000),
  variacoes_sem_id integer not null check (variacoes_sem_id between 0 and 100),
  variacoes_sem_codigo integer not null check (variacoes_sem_codigo between 0 and 100),
  primary key (organization_id,batch_id,product_id),
  foreign key(organization_id,batch_id) references public.catalog_stock_batches(organization_id,id)
);
create index catalog_stock_rows_batch on public.catalog_stock_rows (organization_id,batch_id);

alter table public.catalog_stock_batches enable row level security;
alter table public.catalog_stock_rows enable row level security;
revoke all on public.catalog_stock_batches, public.catalog_stock_rows
  from public,anon,authenticated,service_role;

create function private.catalog_stock_commit(
  p_source_id uuid, p_store_id text, p_expected_rows integer,
  p_expected_stock numeric, p_rows jsonb, p_confirmed boolean
) returns jsonb
language plpgsql security definer set search_path='' as $$
declare
  org uuid := private.sync_source(p_source_id,p_store_id);
  linha jsonb;
  nome text;
  qtd text;
  identidade text;
  sem_id text;
  sem_codigo text;
  soma numeric := 0;
  count_rows integer;
  qtd_sem_id integer := 0;
  qtd_sem_codigo integer := 0;
  fingerprint text;
  batch uuid;
  reused boolean := false;
begin
  if p_confirmed is distinct from true or p_rows is null or
     jsonb_typeof(p_rows) <> 'array' or
     p_expected_rows is null or p_expected_rows not between 1 and 1000 or
     jsonb_array_length(p_rows) <> p_expected_rows or
     pg_column_size(p_rows) > 512000 or
     p_expected_stock is null or p_expected_stock < 0 or p_expected_stock > 1000000000000 then
    raise exception 'Lote do catálogo incompleto ou não confirmado.' using errcode='22023';
  end if;
  count_rows := jsonb_array_length(p_rows);
  for linha in select value from jsonb_array_elements(p_rows) loop
    if jsonb_typeof(linha) <> 'object' then
      raise exception 'Produto inválido no lote.' using errcode='22023';
    end if;
    if not (linha ?& array['id','codigo_interno','estoque','variacoes_sem_id','variacoes_sem_codigo'])
      or exists (
        select 1 from jsonb_object_keys(linha) as k(chave)
        where chave <> all(array['id','codigo_interno','estoque','variacoes_sem_id','variacoes_sem_codigo'])
      ) then
      raise exception 'Campos inesperados no catálogo.' using errcode='22023';
    end if;
    identidade := linha->>'id';
    nome := linha->>'codigo_interno';
    qtd := linha->>'estoque';
    sem_id := linha->>'variacoes_sem_id';
    sem_codigo := linha->>'variacoes_sem_codigo';
    if jsonb_typeof(linha->'id') <> 'string'
       or identidade !~ '^[0-9]{1,30}$' or identidade::numeric <= 0
       or jsonb_typeof(linha->'codigo_interno') not in ('null','string')
       or (nome is not null and (length(btrim(nome)) not between 1 and 100
                                  or nome ~ '[<>[:cntrl:]]'))
       or jsonb_typeof(linha->'estoque') <> 'string'
       or qtd !~ '^[0-9]{1,10}([.][0-9]{1,6})?$'
       or qtd::numeric > 1000000000
       or jsonb_typeof(linha->'variacoes_sem_id') <> 'number'
       or sem_id !~ '^[0-9]{1,3}$' or sem_id::integer > 100
       or jsonb_typeof(linha->'variacoes_sem_codigo') <> 'number'
       or sem_codigo !~ '^[0-9]{1,3}$' or sem_codigo::integer > 100 then
      raise exception 'Dados de estoque do catálogo inválidos.' using errcode='22023';
    end if;
    soma := soma + qtd::numeric;
    qtd_sem_id := qtd_sem_id + sem_id::integer;
    qtd_sem_codigo := qtd_sem_codigo + sem_codigo::integer;
  end loop;
  if (select count(distinct item->>'id') from jsonb_array_elements(p_rows) item) <> count_rows
    or soma <> p_expected_stock then
    raise exception 'Identificadores ou soma do catálogo inconsistentes.' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    org::text || ':' || p_source_id::text || ':' || p_store_id, 31
  ));
  fingerprint := encode(sha256(convert_to(p_rows::text,'UTF8')), 'hex');
  select id into batch from public.catalog_stock_batches
    where organization_id=org and source_id=p_source_id and store_id=p_store_id
      and checksum=fingerprint;
  if batch is not null then
    reused := true;
  else
    if (select count(*) from public.catalog_stock_batches
        where organization_id=org and source_id=p_source_id and store_id=p_store_id) >= 60 then
      raise exception 'Limite de fotografias atingido; exporte e revise a retenção.' using errcode='54000';
    end if;
    insert into public.catalog_stock_batches (
      organization_id,source_id,store_id,author_id,checksum,product_count,
      total_stock,missing_variation_ids,missing_variation_codes
    ) values (
      org,p_source_id,p_store_id,auth.uid(),fingerprint,count_rows,
      soma,qtd_sem_id,qtd_sem_codigo
    ) returning id into batch;
    insert into public.catalog_stock_rows (
      organization_id,batch_id,product_id,codigo_interno,estoque,
      variacoes_sem_id,variacoes_sem_codigo
    )
    select org,batch,item->>'id',item->>'codigo_interno',(item->>'estoque')::numeric,
      (item->>'variacoes_sem_id')::integer,(item->>'variacoes_sem_codigo')::integer
      from jsonb_array_elements(p_rows) item;
    insert into public.audit_events(organization_id,author_id,action,target,details)
    values (org,auth.uid(),'catalog.snapshot',batch::text,
            jsonb_build_object('items',count_rows,'store_id',p_store_id,
             'missing_variation_ids',qtd_sem_id));
  end if;
  return jsonb_build_object(
    'id',batch,'reused',reused,'product_count',count_rows,
    'total_stock',soma,'missing_variation_ids',qtd_sem_id,
    'missing_variation_codes',qtd_sem_codigo,
    'motor_atualizado',false
  );
end $$;

create function private.catalog_stock_history(p_source_id uuid,p_store_id text)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid := private.sync_source(p_source_id,p_store_id);
begin
  return coalesce((
    select jsonb_agg(jsonb_build_object(
      'id',sub.id,'created_at',sub.created_at,'product_count',sub.product_count,
      'total_stock',sub.total_stock,'missing_variation_ids',sub.missing_variation_ids,
      'missing_variation_codes',sub.missing_variation_codes
    ) order by sub.created_at desc)
    from (select b.* from public.catalog_stock_batches b
      where b.organization_id=org and b.source_id=p_source_id and b.store_id=p_store_id
      order by b.created_at desc limit 10) sub
  ),'[]'::jsonb);
end $$;

create function public.catalog_stock_commit(
  p_source_id uuid,p_store_id text,p_expected_rows integer,
  p_expected_stock numeric,p_rows jsonb,p_confirmed boolean
) returns jsonb language sql security invoker set search_path='' as $$
  select private.catalog_stock_commit(
    p_source_id,p_store_id,p_expected_rows,p_expected_stock,p_rows,p_confirmed
  )
$$;
create function public.catalog_stock_history(p_source_id uuid,p_store_id text)
returns jsonb language sql stable security invoker set search_path='' as $$
  select private.catalog_stock_history(p_source_id,p_store_id)
$$;
revoke all on function private.catalog_stock_commit(uuid,text,integer,numeric,jsonb,boolean),
  public.catalog_stock_commit(uuid,text,integer,numeric,jsonb,boolean),
  private.catalog_stock_history(uuid,text), public.catalog_stock_history(uuid,text)
  from public,anon,authenticated,service_role;
grant execute on function private.catalog_stock_commit(uuid,text,integer,numeric,jsonb,boolean),
  public.catalog_stock_commit(uuid,text,integer,numeric,jsonb,boolean),
  private.catalog_stock_history(uuid,text), public.catalog_stock_history(uuid,text)
  to authenticated;
notify pgrst,'reload schema';
commit;
