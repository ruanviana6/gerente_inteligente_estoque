-- Regras internas por organização e SKU. O catálogo atual ainda não tem loja
-- operacional identificada: nenhuma consolidação entre lojas é presumida.
insert into public.permissions(name) values('inventory.export') on conflict(name) do nothing;
create table public.replenishment_configuration (
  organization_id uuid primary key references public.organizations(id),
  cycle_primary integer not null default 14 check(cycle_primary between 1 and 365),
  cycle_alternative integer not null default 21 check(cycle_alternative between 1 and 365),
  reference_boxes integer not null default 80 check(reference_boxes between 1 and 100000),
  updated_at timestamptz not null default now()
);
create table public.replenishment_packaging_versions (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  sku text not null check(length(btrim(sku)) between 1 and 120),
  source_key text not null default 'catalogo_interno',
  store_key text,
  effective_on date not null,
  units_per_box integer not null check(units_per_box between 1 and 100000),
  minimum_lot integer not null default 0 check(minimum_lot>=0),
  production_multiple integer not null default 1 check(production_multiple>0),
  maximum_units integer check(maximum_units>0 and maximum_units>=minimum_lot),
  note text not null default '' check(length(note)<=500),
  author_id uuid not null references auth.users(id),
  created_at timestamptz not null default clock_timestamp()
);
create index packaging_current_lookup on public.replenishment_packaging_versions
  (organization_id,sku,effective_on desc,created_at desc,id desc);
create table public.replenishment_proposals (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  author_id uuid not null references auth.users(id),
  scenario_days integer not null check(scenario_days between 1 and 365),
  reference_date date not null,
  items jsonb not null check(jsonb_typeof(items)='array' and jsonb_array_length(items) between 1 and 500),
  created_at timestamptz not null default now()
);
create index replenishment_proposals_author on public.replenishment_proposals(organization_id,author_id,created_at desc);
alter table public.replenishment_configuration enable row level security;
alter table public.replenishment_packaging_versions enable row level security;
alter table public.replenishment_proposals enable row level security;
revoke all on public.replenishment_configuration,public.replenishment_packaging_versions,
  public.replenishment_proposals from public,anon,authenticated;

create function private.replenishment_rules(p_date date default current_date) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid; config jsonb; rules jsonb; revisions jsonb;
begin
  org := private.current_organization();
  if org is null or not (private.has_permission('replenishment.view') or private.has_permission('parameters.edit')) then
    raise exception 'Acesso negado.' using errcode='42501';
  end if;
  if p_date is null then raise exception 'Data inválida.' using errcode='22023'; end if;
  select jsonb_build_object('cycle_primary',cycle_primary,'cycle_alternative',cycle_alternative,
    'reference_boxes',reference_boxes) into config from public.replenishment_configuration where organization_id=org;
  select coalesce(jsonb_agg(jsonb_build_object('sku',v.sku,'id',v.id,'effective_on',v.effective_on,
    'units_per_box',v.units_per_box,'minimum_lot',v.minimum_lot,'production_multiple',v.production_multiple,
    'maximum_units',v.maximum_units,'note',v.note,'source_key',v.source_key,'store_key',v.store_key,
    'created_at',v.created_at,'revision',(select l.id from public.replenishment_packaging_versions l
      where l.organization_id=org and l.sku=v.sku order by l.created_at desc,l.id desc limit 1)) order by v.sku),'[]'::jsonb)
    into rules from (select distinct on (sku) * from public.replenishment_packaging_versions
      where organization_id=org and effective_on<=p_date order by sku,effective_on desc,created_at desc,id desc) v;
  select coalesce(jsonb_object_agg(sku,id),'{}'::jsonb) into revisions from (
    select distinct on (sku) sku,id from public.replenishment_packaging_versions where organization_id=org
      order by sku,created_at desc,id desc) latest;
  return jsonb_build_object('configuration',coalesce(config,'{"cycle_primary":14,"cycle_alternative":21,"reference_boxes":80}'::jsonb),
    'rules',rules,'revisions',revisions);
end $$;

create function private.save_replenishment_configuration(p_primary integer,p_alternative integer,p_reference integer)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_permission('parameters.edit');
  if p_primary not between 1 and 365 or p_alternative not between 1 and 365 or p_reference not between 1 and 100000
      or p_primary is null or p_alternative is null or p_reference is null then
    raise exception 'Configuração inválida.' using errcode='22023';
  end if;
  insert into public.replenishment_configuration(organization_id,cycle_primary,cycle_alternative,reference_boxes)
    values(org,p_primary,p_alternative,p_reference) on conflict(organization_id) do update
    set cycle_primary=excluded.cycle_primary,cycle_alternative=excluded.cycle_alternative,
        reference_boxes=excluded.reference_boxes,updated_at=now();
  insert into public.audit_events(organization_id,author_id,action,target)
    values(org,auth.uid(),'replenishment.configuration','configuration');
  return jsonb_build_object('updated',true);
end $$;

create function private.save_replenishment_packaging(p_sku text,p_effective date,p_units integer,p_minimum integer,
  p_multiple integer,p_maximum integer,p_note text,p_expected uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; previous public.replenishment_packaging_versions; new_id uuid;
begin
  org := private.require_permission('parameters.edit');
  if p_sku is null or length(btrim(p_sku)) not between 1 and 120 or p_effective is null
    or p_effective<'2000-01-01' or p_effective>current_date+365 or p_units not between 1 and 100000
    or p_minimum<0 or p_multiple<1 or (p_maximum is not null and (p_maximum<1 or p_maximum<p_minimum))
    or p_units is null or p_minimum is null or p_multiple is null or p_note is null or length(p_note)>500 then
    raise exception 'Regra de embalagem inválida.' using errcode='22023';
  end if;
  if not exists(select 1 from public.inventory_current c join public.inventory_products p
    on p.organization_id=c.organization_id and p.batch_id=c.batch_id
    where c.organization_id=org and p.codigo=p_sku) then
    raise exception 'Produto não consta no catálogo atual.' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('packaging:'||org::text||':'||p_sku,0));
  select * into previous from public.replenishment_packaging_versions where organization_id=org and sku=p_sku
    order by created_at desc,id desc limit 1;
  if previous.id is distinct from p_expected then
    raise exception 'A regra mudou; revise a prévia.' using errcode='40001';
  end if;
  if previous.id is not null and previous.effective_on=p_effective and previous.units_per_box=p_units
      and previous.minimum_lot=p_minimum and previous.production_multiple=p_multiple
      and previous.maximum_units is not distinct from p_maximum and previous.note=p_note then
    return jsonb_build_object('id',previous.id,'reused',true);
  end if;
  insert into public.replenishment_packaging_versions(organization_id,sku,effective_on,units_per_box,
    minimum_lot,production_multiple,maximum_units,note,author_id)
    values(org,p_sku,p_effective,p_units,p_minimum,p_multiple,p_maximum,p_note,auth.uid()) returning id into new_id;
  insert into public.audit_events(organization_id,author_id,action,target)
    values(org,auth.uid(),'replenishment.packaging',p_sku);
  return jsonb_build_object('id',new_id,'reused',false);
end $$;

create function private.save_replenishment_packaging_batch(p_rows jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare entry jsonb; count_rows integer := 0; seen text[] := '{}'; sku text;
begin
  perform private.require_permission('parameters.edit');
  if jsonb_typeof(p_rows) is distinct from 'array' or jsonb_array_length(p_rows) not between 1 and 500 then
    raise exception 'Lote inválido.' using errcode='22023';
  end if;
  for entry in select value from jsonb_array_elements(p_rows) order by value->>'sku' loop
    sku := entry->>'sku';
    if jsonb_typeof(entry) is distinct from 'object' or sku is null or sku=any(seen)
      or jsonb_typeof(entry->'units_per_box') is distinct from 'number'
      or jsonb_typeof(entry->'minimum_lot') is distinct from 'number'
      or jsonb_typeof(entry->'production_multiple') is distinct from 'number' then
      raise exception 'Linha duplicada ou inválida.' using errcode='22023';
    end if;
    seen := array_append(seen,sku);
    perform private.save_replenishment_packaging(sku,(entry->>'effective_on')::date,
      (entry->>'units_per_box')::integer,(entry->>'minimum_lot')::integer,
      (entry->>'production_multiple')::integer,(entry->>'maximum_units')::integer,
      coalesce(entry->>'note',''),(entry->>'expected')::uuid);
    count_rows := count_rows+1;
  end loop;
  return jsonb_build_object('count',count_rows);
exception when invalid_text_representation or datetime_field_overflow or numeric_value_out_of_range then
  raise exception 'Linha de embalagem inválida.' using errcode='22023';
end $$;

create function public.replenishment_rules(p_date date default current_date) returns jsonb
language sql stable security invoker set search_path='' as $$select private.replenishment_rules(p_date)$$;
create function public.save_replenishment_configuration(p_primary integer,p_alternative integer,p_reference integer) returns jsonb
language sql security invoker set search_path='' as $$select private.save_replenishment_configuration(p_primary,p_alternative,p_reference)$$;
create function public.save_replenishment_packaging(p_sku text,p_effective date,p_units integer,p_minimum integer,
  p_multiple integer,p_maximum integer,p_note text,p_expected uuid) returns jsonb language sql security invoker set search_path=''
  as $$select private.save_replenishment_packaging(p_sku,p_effective,p_units,p_minimum,p_multiple,p_maximum,p_note,p_expected)$$;
create function public.save_replenishment_packaging_batch(p_rows jsonb) returns jsonb
language sql security invoker set search_path='' as $$select private.save_replenishment_packaging_batch(p_rows)$$;

create function private.save_replenishment_proposal(p_cycle integer,p_reference date,p_items jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; entry jsonb; rule public.replenishment_packaging_versions; item_sku text; seen text[] := '{}';
  units integer; boxes integer; saved uuid; normalized jsonb := '[]'::jsonb;
begin
  org := private.require_permission('decisions.write');
  if not private.has_permission('replenishment.view') or p_cycle is null or p_cycle not between 1 and 365 or p_reference is null
    or jsonb_typeof(p_items) is distinct from 'array' or jsonb_array_length(p_items) not between 1 and 500 then
    raise exception 'Proposta inválida.' using errcode='22023';
  end if;
  for entry in select value from jsonb_array_elements(p_items) order by value->>'sku' loop
    item_sku := entry->>'sku';
    if item_sku is null or item_sku=any(seen) or jsonb_typeof(entry->'units') is distinct from 'number'
      or jsonb_typeof(entry->'boxes') is distinct from 'number' then
      raise exception 'Produto duplicado ou inválido.' using errcode='22023';
    end if;
    seen := array_append(seen,item_sku);
    perform pg_advisory_xact_lock(hashtextextended('packaging:'||org::text||':'||item_sku,0));
    select v.* into rule from public.replenishment_packaging_versions v where v.organization_id=org and v.sku=item_sku
      and v.effective_on<=current_date order by v.effective_on desc,v.created_at desc,v.id desc limit 1;
    if rule.id is null or rule.id is distinct from (entry->>'packaging_id')::uuid then
      raise exception 'Embalagem alterada; revise a proposta.' using errcode='40001';
    end if;
    units := (entry->>'units')::integer;
    boxes := (entry->>'boxes')::integer;
    if units<=0 or boxes<=0 or units::bigint<>boxes::bigint*rule.units_per_box
      or units<rule.minimum_lot or units%rule.production_multiple<>0
      or (rule.maximum_units is not null and units>rule.maximum_units) then
      raise exception 'Quantidade incompatível com a embalagem.' using errcode='22023';
    end if;
    if not exists(select 1 from public.inventory_current c join public.inventory_products p
      on p.organization_id=c.organization_id and p.batch_id=c.batch_id
      where c.organization_id=org and p.codigo=item_sku) then
      raise exception 'Produto não consta no catálogo.' using errcode='22023';
    end if;
    normalized := normalized||jsonb_build_array(jsonb_build_object('sku',item_sku,'units',units,'boxes',boxes,
      'packaging_id',rule.id,'units_per_box',rule.units_per_box));
  end loop;
  insert into public.replenishment_proposals(organization_id,author_id,scenario_days,reference_date,items)
    values(org,auth.uid(),p_cycle,p_reference,normalized) returning id into saved;
  insert into public.audit_events(organization_id,author_id,action,target)
    values(org,auth.uid(),'replenishment.proposal',saved::text);
  return jsonb_build_object('id',saved);
exception when invalid_text_representation or numeric_value_out_of_range then
  raise exception 'Proposta inválida.' using errcode='22023';
end $$;
create function public.save_replenishment_proposal(p_cycle integer,p_reference date,p_items jsonb) returns jsonb
language sql security invoker set search_path='' as $$select private.save_replenishment_proposal(p_cycle,p_reference,p_items)$$;
create function private.list_replenishment_proposals() returns jsonb language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
  org := private.require_permission('decisions.write');
  if not private.has_permission('replenishment.view') then raise exception 'Acesso negado.' using errcode='42501'; end if;
  return coalesce((select jsonb_agg(to_jsonb(p)-'organization_id' order by p.created_at desc)
    from (select * from public.replenishment_proposals where organization_id=org
      and (author_id=auth.uid() or private.is_master()) order by created_at desc limit 100) p),'[]'::jsonb);
end $$;
create function public.list_replenishment_proposals() returns jsonb language sql stable security invoker set search_path=''
  as $$select private.list_replenishment_proposals()$$;

revoke all on function private.replenishment_rules(date),private.save_replenishment_configuration(integer,integer,integer),
  private.save_replenishment_packaging(text,date,integer,integer,integer,integer,text,uuid),
  private.save_replenishment_packaging_batch(jsonb),private.save_replenishment_proposal(integer,date,jsonb),
  private.list_replenishment_proposals() from public,anon,authenticated;
revoke all on function public.replenishment_rules(date),public.save_replenishment_configuration(integer,integer,integer),
  public.save_replenishment_packaging(text,date,integer,integer,integer,integer,text,uuid),
  public.save_replenishment_packaging_batch(jsonb),public.save_replenishment_proposal(integer,date,jsonb),
  public.list_replenishment_proposals() from public,anon;
grant execute on function private.replenishment_rules(date),private.save_replenishment_configuration(integer,integer,integer),
  private.save_replenishment_packaging(text,date,integer,integer,integer,integer,text,uuid),
  private.save_replenishment_packaging_batch(jsonb),private.save_replenishment_proposal(integer,date,jsonb),
  private.list_replenishment_proposals() to authenticated;
grant execute on function public.replenishment_rules(date),public.save_replenishment_configuration(integer,integer,integer),
  public.save_replenishment_packaging(text,date,integer,integer,integer,integer,text,uuid),
  public.save_replenishment_packaging_batch(jsonb),public.save_replenishment_proposal(integer,date,jsonb),
  public.list_replenishment_proposals() to authenticated;

-- As decisões antigas continuam válidas e cada novo estado é um evento histórico.
alter table public.decisions drop constraint decisions_acao_check;
alter table public.decisions add constraint decisions_acao_check check(acao in
  ('Avaliar compra','Ignorar','Em análise','Adiada','Solicitar à indústria','Descartada','Concluída'));
create or replace function private.save_decision(p_codigo text,p_acao text,p_quantidade integer,p_observacao text default '')
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; result uuid;
begin
  org := private.require_permission('decisions.write');
  if p_acao is null or p_acao not in ('Avaliar compra','Ignorar','Em análise','Adiada','Solicitar à indústria','Descartada','Concluída')
    or p_quantidade is null or p_quantidade<0 or p_observacao is null or length(p_observacao)>500 then
    raise exception 'Decisão inválida.' using errcode='22023';
  end if;
  if not exists(select 1 from public.inventory_products p join public.inventory_current c
    on p.batch_id=c.batch_id and p.organization_id=c.organization_id
    where p.organization_id=org and p.codigo=p_codigo) then
    raise exception 'Produto não encontrado.' using errcode='22023';
  end if;
  insert into public.decisions(organization_id,author_id,codigo,acao,quantidade_sugerida,observacao)
    values(org,auth.uid(),p_codigo,p_acao,p_quantidade,p_observacao) returning id into result;
  insert into public.audit_events(organization_id,author_id,action,target) values(org,auth.uid(),'decisions.write',result::text);
  return jsonb_build_object('id',result);
end $$;
