begin;

-- Fotografias imutáveis por lote, preservando os dados e as APIs existentes.
-- A tabela antiga permanece como projeção de compatibilidade; os cálculos e
-- relatórios passam a usar exclusivamente fotografias com origem comprovada.
lock table public.inventory_imports,public.inventory_products in share row exclusive mode;

-- Uma sequência distingue importações concluídas no mesmo timestamp. Nos
-- lotes legados sem essa informação, o desempate determinístico conserva created_at/id da seleção anterior.
-- A seleção anterior preferia o menor UUID nos empates de created_at.
-- Ordenar UUID decrescente aqui preserva essa mesma preferência no legado.
create sequence private.inventory_ingestion_order;
revoke all on sequence private.inventory_ingestion_order from public,anon,authenticated,service_role;
alter table public.inventory_imports add column ingestion_order bigint;
with numbered as (
  select id,row_number() over(order by created_at,id desc) as position from public.inventory_imports
)
update public.inventory_imports b set ingestion_order=n.position from numbered n where n.id=b.id;
select setval('private.inventory_ingestion_order',
  coalesce((select max(ingestion_order) from public.inventory_imports),0)+1,false);
alter table public.inventory_imports alter column ingestion_order
  set default nextval('private.inventory_ingestion_order');
alter table public.inventory_imports alter column ingestion_order set not null;
create unique index inventory_imports_ingestion_order on public.inventory_imports(ingestion_order);
create index inventory_imports_history_order on public.inventory_imports(organization_id,snapshot_date,ingestion_order desc);

create table public.inventory_snapshot_versions (
  organization_id uuid not null,
  batch_id uuid not null,
  codigo text not null,
  data date not null,
  estoque integer not null check(estoque>=0),
  primary key(organization_id,batch_id,codigo,data),
  foreign key(organization_id,batch_id,codigo)
    references public.inventory_products(organization_id,batch_id,codigo)
);
create index inventory_snapshot_versions_history
  on public.inventory_snapshot_versions(organization_id,codigo,data,batch_id);
alter table public.inventory_snapshot_versions enable row level security;
revoke all on public.inventory_snapshot_versions from public,anon,authenticated,service_role;
grant select on public.inventory_snapshot_versions to authenticated;
create policy authorized_inventory on public.inventory_snapshot_versions for select to authenticated
  using(organization_id=(select private.current_organization()) and (select private.can_read_inventory()));

-- O catálogo de cada lote guarda o saldo original, mesmo quando a projeção
-- diária antiga foi sobrescrita. Não associamos fotos órfãs a lotes inventados.
insert into public.inventory_snapshot_versions(organization_id,batch_id,codigo,data,estoque)
select p.organization_id,p.batch_id,p.codigo,b.snapshot_date,p.estoque
from public.inventory_products p
join public.inventory_imports b on b.organization_id=p.organization_id and b.id=p.batch_id;

create function private.capture_inventory_snapshot() returns trigger
language plpgsql security definer set search_path='' as $$
begin
  insert into public.inventory_snapshot_versions(organization_id,batch_id,codigo,data,estoque)
  select p.organization_id,p.batch_id,p.codigo,b.snapshot_date,p.estoque
  from imported_products p
  join public.inventory_imports b on b.organization_id=p.organization_id and b.id=p.batch_id;
  return null;
end $$;
revoke all on function private.capture_inventory_snapshot() from public,anon,authenticated,service_role;
create trigger capture_inventory_snapshot after insert on public.inventory_products
referencing new table as imported_products
for each statement execute function private.capture_inventory_snapshot();

create or replace function private.inventory_payload(p_org uuid,p_user uuid,p_month date default null) returns jsonb
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
      and b.period_start<=p_month and b.period_end>=month_end order by b.ingestion_order desc limit 1;
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
    into snapshots from (
      -- O corte por ingestão impede uma importação tardia de reescrever a
      -- disponibilidade que já sustentava um fechamento anterior. No dia do
      -- fechamento a fotografia sempre vem do próprio lote selecionado.
      select distinct on (v.codigo,v.data) v.codigo,v.data,v.estoque
      from public.inventory_snapshot_versions v
      join public.inventory_imports b on b.organization_id=v.organization_id and b.id=v.batch_id
      where v.organization_id=p_org and v.data<=batch.snapshot_date
        and b.ingestion_order<=batch.ingestion_order
        and exists(select 1 from public.inventory_products p
          where p.organization_id=p_org and p.batch_id=batch.id and p.codigo=v.codigo)
      order by v.codigo,v.data,b.ingestion_order desc
    ) s;
  select coalesce(jsonb_agg(jsonb_build_object('id',b.id,'snapshot_date',b.snapshot_date,'period_start',b.period_start,
    'period_end',b.period_end,'created_at',b.created_at,'source',b.source) order by b.created_at desc),'[]'::jsonb)
    into imports from public.inventory_imports b where b.organization_id=p_org;
  return jsonb_build_object('products',products,'sales',sales,'snapshots',snapshots,'imports',imports,
    'metadata',jsonb_build_object('batch_id',batch.id,'snapshot_date',batch.snapshot_date,'period_start',batch.period_start,
      'period_end',batch.period_end,'created_at',batch.created_at,'source',batch.source,'month_complete',p_month is not null));
end $$;

notify pgrst,'reload schema';

commit;
