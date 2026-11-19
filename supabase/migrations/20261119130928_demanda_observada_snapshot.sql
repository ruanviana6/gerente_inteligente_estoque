begin;

-- Histórico de observações comerciais do GestãoClick; NÃO é uma fonte
-- canônica de vendas, nem baixa o estoque, nem autoriza reposição ou NF-e.
create table public.demand_observation_batches (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  source_id uuid not null,
  store_id text not null check (length(btrim(store_id)) between 1 and 120),
  author_id uuid not null references auth.users(id),
  period_start date not null,
  period_end date not null,
  observed_at timestamptz not null default now(),
  documents integer not null check (documents between 0 and 1000),
  closed_documents integer not null check (closed_documents between 0 and 1000),
  separating_documents integer not null check (separating_documents between 0 and 1000),
  completed_documents integer not null check (completed_documents between 0 and 1000),
  other_documents integer not null check (other_documents between 0 and 1000),
  items integer not null check (items between 0 and 20000),
  unknown_unit_rows integer not null check (unknown_unit_rows between 0 and 20000),
  unsupported_unit_rows integer not null check (unsupported_unit_rows between 0 and 20000),
  total_observed_un numeric(20,6) not null check (total_observed_un between 0 and 1000000000000),
  product_count integer not null check (product_count between 0 and 1000),
  checksum text not null check (checksum ~ '^[0-9a-f]{64}$'),
  constraint demand_observation_week check (
    period_start <= period_end and period_end-period_start between 0 and 6
  ),
  constraint demand_observation_doc_counts check (
    closed_documents=separating_documents+completed_documents
    and documents=closed_documents+other_documents
    and items >= documents
    and unknown_unit_rows+unsupported_unit_rows <= items
  ),
  unique (organization_id,id),
  unique (organization_id,source_id,store_id,period_start,period_end,checksum),
  foreign key (organization_id,source_id)
    references public.integration_sources(organization_id,id)
);
create index demand_observation_scope_time on public.demand_observation_batches
  (organization_id,source_id,store_id,observed_at desc,id desc);

create table public.demand_observation_rows (
  organization_id uuid not null,
  batch_id uuid not null,
  product_id text not null check (product_id ~ '^[0-9]{1,30}$'),
  observed_un numeric(20,6) not null check (observed_un > 0 and observed_un <= 1000000000),
  primary key (organization_id,batch_id,product_id),
  foreign key (organization_id,batch_id)
    references public.demand_observation_batches(organization_id,id)
);
alter table public.demand_observation_batches enable row level security;
alter table public.demand_observation_rows enable row level security;
revoke all on public.demand_observation_batches, public.demand_observation_rows
  from public,anon,authenticated,service_role;

create function private.demand_observation_save(
  p_source_id uuid,p_store_id text,p_period_start date,p_period_end date,
  p_documents integer,p_separating integer,p_completed integer,p_other integer,
  p_items integer,p_unknown_unit integer,p_unsupported_unit integer,
  p_rows jsonb,p_confirmed boolean
) returns jsonb
language plpgsql security definer set search_path='' as $$
declare
  org uuid := private.sync_source(p_source_id,p_store_id);
  latest uuid;
  row_item jsonb;
  id_text text;
  qty_text text;
  counted integer := 0;
  quantity_sum numeric := 0;
  content_hash text;
  batch uuid;
  reused boolean := false;
  row_count integer;
  v_seen integer;
begin
  if p_confirmed is distinct from true or
     p_period_start is null or p_period_end is null or
     p_period_end-p_period_start not between 0 and 6 or
     p_period_end > (now() at time zone 'America/Fortaleza')::date or
     p_documents is null or p_documents not between 0 and 1000 or
     p_separating is null or p_separating not between 0 and 1000 or
     p_completed is null or p_completed not between 0 and 1000 or
     p_other is null or p_other not between 0 and 1000 or
     p_documents <> p_separating+p_completed+p_other or
     p_items is null or p_items not between p_documents and 20000 or
     p_unknown_unit is null or p_unknown_unit not between 0 and p_items or
     p_unsupported_unit is null or p_unsupported_unit not between 0 and p_items or
     p_unknown_unit+p_unsupported_unit > p_items or
     p_rows is null or jsonb_typeof(p_rows)<>'array' or
     jsonb_array_length(p_rows)>1000 or pg_column_size(p_rows)>100000 then
    raise exception 'Observação comercial inválida ou não confirmada.'
      using errcode='22023';
  end if;
  row_count:=jsonb_array_length(p_rows);
  if p_documents=0 and (p_items<>0 or row_count<>0) then
    raise exception 'Observação sem documentos não pode ter itens.' using errcode='22023';
  end if;
  if p_separating+p_completed=0 and row_count<>0 then
    raise exception 'A observação não tem vendas fechadas para agregar.' using errcode='22023';
  end if;
  select b.id into latest from public.catalog_stock_batches b
    where b.organization_id=org and b.source_id=p_source_id and b.store_id=p_store_id
    order by b.created_at desc,b.id desc limit 1;
  if latest is null then
    raise exception 'Salve e confira primeiro o catálogo desta loja.'
      using errcode='22023';
  end if;
  for row_item in select value from jsonb_array_elements(p_rows) loop
    if jsonb_typeof(row_item) <> 'object' or
       not (row_item ?& array['product_id','observed_un']) or
       (select count(*) from jsonb_object_keys(row_item)) <> 2 then
      raise exception 'Linha observada com campos inválidos.' using errcode='22023';
    end if;
    id_text:=row_item->>'product_id';
    qty_text:=row_item->>'observed_un';
    if jsonb_typeof(row_item->'product_id')<>'string' or
       id_text !~ '^[0-9]{1,30}$' or id_text::numeric <= 0 or
       jsonb_typeof(row_item->'observed_un')<>'string' or
       qty_text !~ '^[0-9]{1,10}([.][0-9]{1,6})?$' or
       qty_text::numeric<=0 or qty_text::numeric>1000000000 then
      raise exception 'Identificador ou quantidade incompatível.' using errcode='22023';
    end if;
    if not exists (
      select 1 from public.catalog_stock_rows c
      where c.organization_id=org and c.batch_id=latest and c.product_id=id_text
    ) then
      raise exception 'Produto observado fora do catálogo conferido.' using errcode='22023';
    end if;
    quantity_sum := quantity_sum+qty_text::numeric;
    counted := counted+1;
  end loop;
  select count(distinct x->>'product_id') into v_seen from jsonb_array_elements(p_rows) x;
  if v_seen <> counted then
    raise exception 'Produto repetido na observação.' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    org::text||':'||p_source_id::text||':'||p_store_id||':'||
    p_period_start::text||':'||p_period_end::text, 47
  ));
  -- O hash inclui totais e agregação; novas observações corrigidas são
  -- versões adicionais. Nenhuma ausência é convertida em cancelamento.
  content_hash:=encode(sha256(convert_to(
    jsonb_build_object('start',p_period_start,'end',p_period_end,
      'documents',p_documents,'separating',p_separating,
      'completed',p_completed,'other',p_other,'items',p_items,
      'unknown',p_unknown_unit,'unsupported',p_unsupported_unit,
      'rows',p_rows)::text, 'UTF8'
  )),'hex');
  select b.id into batch from public.demand_observation_batches b
    where b.organization_id=org and b.source_id=p_source_id
      and b.store_id=p_store_id and b.period_start=p_period_start
      and b.period_end=p_period_end and b.checksum=content_hash;
  if batch is not null then
    reused:=true;
  else
    if (select count(*) from public.demand_observation_batches b
        where b.organization_id=org and b.source_id=p_source_id
          and b.store_id=p_store_id)>=400 then
      raise exception 'Limite de observações atingido; revise retenção.'
        using errcode='54000';
    end if;
    insert into public.demand_observation_batches (
      organization_id,source_id,store_id,author_id,
      period_start,period_end,documents,closed_documents,
      separating_documents,completed_documents,other_documents,
      items,unknown_unit_rows,unsupported_unit_rows,total_observed_un,
      product_count,checksum
    ) values (
      org,p_source_id,p_store_id,auth.uid(),
      p_period_start,p_period_end,p_documents,p_separating+p_completed,
      p_separating,p_completed,p_other,p_items,p_unknown_unit,p_unsupported_unit,
      quantity_sum,counted,content_hash
    ) returning id into batch;
    insert into public.demand_observation_rows (
      organization_id,batch_id,product_id,observed_un
    ) select org,batch,x->>'product_id',(x->>'observed_un')::numeric
        from jsonb_array_elements(p_rows) x;
    insert into public.audit_events(organization_id,author_id,action,target,details)
      values(org,auth.uid(),'demand.observed',batch::text,
      jsonb_build_object('store_id',p_store_id,'documents',p_documents,
      'closed',p_separating+p_completed,'products',counted,
      'not_for_replenishment',true));
  end if;
  return jsonb_build_object(
    'id',batch,'reused',reused,'product_count',counted,
    'total_observed_un',quantity_sum,
    'ready_for_replenishment',false,'motor_atualizado',false,
    'fiscal_real_enabled',false
  );
end $$;

create function private.demand_observation_history(
  p_source_id uuid,p_store_id text
) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid:=private.sync_source(p_source_id,p_store_id);
begin
  return coalesce((
    select jsonb_agg(jsonb_build_object(
      'id',x.id,'observed_at',x.observed_at,'period_start',x.period_start,
      'period_end',x.period_end,'documents',x.documents,
      'closed_documents',x.closed_documents,
      'products',x.product_count,'total_observed_un',x.total_observed_un,
      'unknown_unit_rows',x.unknown_unit_rows,
      'unsupported_unit_rows',x.unsupported_unit_rows,
      'ready_for_replenishment',false
    ) order by x.observed_at desc,x.id desc)
    from (select * from public.demand_observation_batches b
      where b.organization_id=org and b.source_id=p_source_id and b.store_id=p_store_id
      order by b.observed_at desc,b.id desc limit 12) x
  ),'[]'::jsonb);
end $$;

create function public.demand_observation_save(
  p_source_id uuid,p_store_id text,p_period_start date,p_period_end date,
  p_documents integer,p_separating integer,p_completed integer,p_other integer,
  p_items integer,p_unknown_unit integer,p_unsupported_unit integer,
  p_rows jsonb,p_confirmed boolean
) returns jsonb language sql security invoker set search_path='' as $$
  select private.demand_observation_save(
    p_source_id,p_store_id,p_period_start,p_period_end,
    p_documents,p_separating,p_completed,p_other,p_items,
    p_unknown_unit,p_unsupported_unit,p_rows,p_confirmed
  )
$$;
create function public.demand_observation_history(
  p_source_id uuid,p_store_id text
) returns jsonb language sql stable security invoker set search_path='' as $$
  select private.demand_observation_history(p_source_id,p_store_id)
$$;

revoke all on function
  private.demand_observation_save(uuid,text,date,date,integer,integer,integer,integer,integer,integer,integer,jsonb,boolean),
  public.demand_observation_save(uuid,text,date,date,integer,integer,integer,integer,integer,integer,integer,jsonb,boolean),
  private.demand_observation_history(uuid,text),
  public.demand_observation_history(uuid,text)
from public,anon,authenticated,service_role;
grant execute on function
  private.demand_observation_save(uuid,text,date,date,integer,integer,integer,integer,integer,integer,integer,jsonb,boolean),
  public.demand_observation_save(uuid,text,date,date,integer,integer,integer,integer,integer,integer,integer,jsonb,boolean),
  private.demand_observation_history(uuid,text),
  public.demand_observation_history(uuid,text)
to authenticated;

notify pgrst,'reload schema';
commit;
