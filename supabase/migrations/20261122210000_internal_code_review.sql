begin;
-- Código interno da MESMA fotografia da loja, sem consultar ou alterar ERP.
-- Mantém autorização via private.sync_source e todas as flags de bloqueio.
create or replace function private.demand_observation_review(
    p_source_id uuid, p_store_id text
) returns jsonb
language plpgsql stable security definer set search_path='' as $$
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
           'internal_code', case
              when btrim(c.codigo_interno) ~ '^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,79},d.observed_un,
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
end $$;
              then btrim(c.codigo_interno) else null end,
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
end $$;
notify pgrst,'reload schema';
commit;
