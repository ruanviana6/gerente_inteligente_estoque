begin;
-- Leitura de fotografia já confirmada. Não expõe tabelas, clientes ou vendas.
create function private.catalog_stock_latest(p_source_id uuid, p_store_id text)
returns jsonb language plpgsql stable security definer set search_path='' as $$
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
    coalesce(jsonb_agg(jsonb_build_object('id',r.product_id,'estoque',r.estoque)
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
end $$;
create function public.catalog_stock_latest(p_source_id uuid,p_store_id text)
returns jsonb language sql stable security invoker set search_path='' as $$
  select private.catalog_stock_latest(p_source_id,p_store_id)
$$;
revoke all on function private.catalog_stock_latest(uuid,text),
  public.catalog_stock_latest(uuid,text) from public,anon,authenticated,service_role;
grant execute on function private.catalog_stock_latest(uuid,text),
  public.catalog_stock_latest(uuid,text) to authenticated;
notify pgrst,'reload schema';
commit;
