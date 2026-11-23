-- Identificação real de produtos por fotografia, vinculada ao ID e à loja.
-- Nada é inventado ou retropreenchido; fotografias antigas mantêm NULL até nova leitura GET.
ALTER TABLE public.catalog_stock_rows
  ADD COLUMN IF NOT EXISTS nome text,
  ADD COLUMN IF NOT EXISTS categoria text;
ALTER TABLE public.catalog_stock_rows
  ADD CONSTRAINT catalog_stock_rows_nome_valid_ck
    CHECK (nome IS NULL OR
      (char_length(btrim(nome)) BETWEEN 1 AND 200 AND nome !~ '[<>[:cntrl:]]')),
  ADD CONSTRAINT catalog_stock_rows_categoria_valid_ck
    CHECK (categoria IS NULL OR
      (char_length(btrim(categoria)) BETWEEN 1 AND 200 AND categoria !~ '[<>[:cntrl:]]'));


CREATE OR REPLACE FUNCTION private.catalog_stock_commit(p_source_id uuid, p_store_id text, p_expected_rows integer, p_expected_stock numeric, p_rows jsonb, p_confirmed boolean)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  org uuid := private.sync_source(p_source_id,p_store_id);
  linha jsonb;
  nome text;
  nome_produto text;
  categoria_produto text;
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
        where chave <> all(array['id','codigo_interno','nome','categoria','estoque','variacoes_sem_id','variacoes_sem_codigo'])
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
    if (linha ? 'nome' and jsonb_typeof(linha->'nome') not in ('string','null'))
      or (linha ? 'categoria' and jsonb_typeof(linha->'categoria') not in ('string','null')) then
      raise exception 'Nome ou categoria do catálogo inválido.' using errcode='22023';
    end if;
    nome_produto := linha->>'nome';
    categoria_produto := linha->>'categoria';
    if (nome_produto is not null and
          (length(btrim(nome_produto)) not between 1 and 200
           or nome_produto ~ '[<>[:cntrl:]]'))
      or (categoria_produto is not null and
          (length(btrim(categoria_produto)) not between 1 and 200
           or categoria_produto ~ '[<>[:cntrl:]]')) then
      raise exception 'Identificação de produto inválida.' using errcode='22023';
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
      total_stock,missing_variation_ids,missing_variation_codes,created_at
    ) values (
      org,p_source_id,p_store_id,auth.uid(),fingerprint,count_rows,
      soma,qtd_sem_id,qtd_sem_codigo,clock_timestamp()
    ) returning id into batch;
    insert into public.catalog_stock_rows (
      organization_id,batch_id,product_id,codigo_interno,nome,categoria,estoque,
      variacoes_sem_id,variacoes_sem_codigo
    )
    select org,batch,item->>'id',item->>'codigo_interno',
      item->>'nome',item->>'categoria',(item->>'estoque')::numeric,
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
end $function$
;

CREATE OR REPLACE FUNCTION private.catalog_stock_latest(p_source_id uuid, p_store_id text)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  org uuid := private.sync_source(p_source_id,p_store_id);
  fotografia public.catalog_stock_batches;
  qtd integer;
  linhas jsonb;
begin
  select * into fotografia
    from public.catalog_stock_batches b
    where b.organization_id=org and b.source_id=p_source_id and b.store_id=p_store_id
    order by b.created_at desc, b.id desc limit 1;
  if not found then
    return jsonb_build_object('found',false,'store_id',p_store_id);
  end if;
  select count(*),
    coalesce(jsonb_agg(jsonb_build_object('id',r.product_id,'codigo_interno',r.codigo_interno,
      'nome',r.nome,'categoria',r.categoria,'estoque',r.estoque)
      order by r.product_id),'[]'::jsonb)
    into qtd,linhas
    from public.catalog_stock_rows r
    where r.organization_id=org and r.batch_id=fotografia.id;
  if qtd<>fotografia.product_count or qtd>1000 or qtd<1 then
    raise exception 'A fotografia não reconcilia com os produtos armazenados.'
      using errcode='22023';
  end if;
  return jsonb_build_object(
    'found',true,
    'store_id',p_store_id,
    'snapshot_id',fotografia.id,
    'created_at',fotografia.created_at,
    'product_count',fotografia.product_count,
    'total_stock',fotografia.total_stock,
    'missing_variation_ids',fotografia.missing_variation_ids,
    'products',linhas
  );
end $function$
;

CREATE OR REPLACE FUNCTION private.demand_observation_review(p_source_id uuid, p_store_id text)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  org uuid := private.sync_source(p_source_id,p_store_id);
  obs public.demand_observation_batches;
  foto public.catalog_stock_batches;
  qtd integer;
  soma numeric;
  linhas jsonb;
begin
  select b.* into obs from public.demand_observation_batches b
    where b.organization_id=org and b.source_id=p_source_id and b.store_id=p_store_id
    order by b.observed_at desc,b.id desc limit 1;
  if not found then
    return jsonb_build_object('found',false,'store_id',p_store_id);
  end if;
  select b.* into foto from public.catalog_stock_batches b
    where b.organization_id=org and b.source_id=p_source_id and b.store_id=p_store_id
    order by b.created_at desc,b.id desc limit 1;
  if not found then
    raise exception 'Fotografia de estoque não encontrada para esta loja.'
      using errcode='22023';
  end if;

  select count(*),coalesce(sum(d.observed_un),0),
         coalesce(jsonb_agg(jsonb_build_object(
           'product_id',d.product_id,
           'internal_code', CASE WHEN btrim(c.codigo_interno) ~ '^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,79}$' THEN btrim(c.codigo_interno) ELSE null END,
           'product_name',c.nome,
           'category',c.categoria,
           'observed_un',d.observed_un,
           'stock_snapshot',c.estoque
         ) order by d.observed_un desc,d.product_id),'[]'::jsonb)
    into qtd,soma,linhas
    from public.demand_observation_rows d
    left join public.catalog_stock_rows c on
         c.organization_id=org and c.batch_id=foto.id and c.product_id=d.product_id
    where d.organization_id=org and d.batch_id=obs.id;
  if qtd<>obs.product_count or soma<>obs.total_observed_un or qtd>1000 then
    raise exception 'A observação não confere com o histórico.'
      using errcode='22023';
  end if;

  return jsonb_build_object(
    'found',true,'store_id',p_store_id,
    'observed_at',obs.observed_at,
    'period_start',obs.period_start,
    'period_end',obs.period_end,
    'documents',obs.documents,
    'closed_documents',obs.closed_documents,
    'other_documents',obs.other_documents,
    'product_count',obs.product_count,
    'total_observed_un',obs.total_observed_un,
    'stock_snapshot_at',foto.created_at,
    'stock_snapshot_products',foto.product_count,
    'rows',linhas,
    'stock_is_live',false,
    'ready_for_replenishment',false,
    'fiscal_real_enabled',false
  );
end $function$
;
