-- Resumo recomputável. O histórico de estoque e vendas permanece integral.
create table public.dashboard_snapshots (
 organization_id uuid primary key references public.organizations(id),
 batch_id uuid not null, revision text not null, summary jsonb not null,
 author_id uuid not null references auth.users(id), calculated_at timestamptz not null default now(),
 check(octet_length(summary::text)<=32768),
 foreign key(organization_id,batch_id) references public.inventory_imports(organization_id,id)
);
alter table public.dashboard_snapshots enable row level security;
revoke all on public.dashboard_snapshots from public,anon,authenticated,service_role;

create function private.dashboard_revision(p_org uuid) returns text
language sql stable security definer set search_path='' as $$
 select encode(sha256(convert_to(jsonb_build_array(
 (select batch_id from public.inventory_current where organization_id=p_org),
 (select max(updated_at) from public.product_parameters where organization_id=p_org),
 (select max(created_at) from public.replenishment_packaging_versions where organization_id=p_org),
 (select max(updated_at) from public.replenishment_configuration where organization_id=p_org),
 (select max(finished_at) from public.sync_runs where organization_id=p_org and status='completed')
 )::text,'UTF8')),'hex')
$$;
revoke all on function private.dashboard_revision(uuid) from public,anon,authenticated,service_role;

create function private.dashboard_source() returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
 org:=private.require_master();
 return jsonb_build_object('batch_id',(select batch_id from public.inventory_current where organization_id=org),
 'revision',private.dashboard_revision(org));
end $$;

create function private.dashboard_publish(p_batch uuid,p_revision text,p_summary jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; bloco record; permitidas text[]; entrada record; item jsonb;
begin
 org:=private.require_master();
 if p_batch is null or p_revision is null or p_revision is distinct from private.dashboard_revision(org)
 or not exists(select 1 from public.inventory_current where organization_id=org and batch_id=p_batch) then
 raise exception 'Os dados mudaram. Atualize o resumo novamente.' using errcode='40001'; end if;
 if jsonb_typeof(p_summary) is distinct from 'object' or octet_length(p_summary::text)>32768
 or p_summary->>'schema' is distinct from '1' or p_summary->>'mode' is null or p_summary->>'mode' not in ('demo','unclassified')
 or (select count(*) from jsonb_object_keys(p_summary))<>7
 or exists(select 1 from jsonb_object_keys(p_summary) k where k not in ('schema','mode','inventory','replenishment','sales','priorities','finance')) then
 raise exception 'Resumo fora do contrato.' using errcode='22023'; end if;
 for bloco in select key,value from jsonb_each(p_summary) where key in ('inventory','replenishment','sales') loop
 if jsonb_typeof(bloco.value) is distinct from 'object' then raise exception 'Bloco inválido.' using errcode='22023'; end if;
 permitidas:=case bloco.key when 'inventory' then array['products','stockouts','risk','review','excess','suggested']
 when 'replenishment' then array['boxes','unknown_packaging','primary','alternative','reference_boxes','next_request','cycles']
 else array['current_30','previous_30','change_pct','daily'] end;
 if not bloco.value ?& permitidas or exists(select 1 from jsonb_object_keys(bloco.value) k where not k=any(permitidas)) then raise exception 'Campo ausente ou não permitido no resumo.' using errcode='22023'; end if;
 for entrada in select key,value from jsonb_each(bloco.value) loop
 if entrada.key in ('daily','cycles') then continue; end if;
 if entrada.key='next_request' then
 if entrada.value<>'null'::jsonb and (entrada.value#>>'{}') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then raise exception 'Data inválida.' using errcode='22023'; end if;
 elsif jsonb_typeof(entrada.value) not in ('number','null') then raise exception 'Indicador deve ser numérico.' using errcode='22023'; end if;
 end loop;
 end loop;
 if jsonb_typeof(p_summary->'finance') is distinct from 'object' or (select count(*) from jsonb_object_keys(p_summary->'finance'))<>2
 or not (p_summary->'finance')?'capital' or not (p_summary->'finance')?'partial'
 or jsonb_typeof(p_summary->'finance'->'capital') not in ('number','null')
 or jsonb_typeof(p_summary->'finance'->'partial') is distinct from 'boolean' then raise exception 'Resumo financeiro inválido.' using errcode='22023'; end if;
 if jsonb_typeof(p_summary->'sales'->'daily') is distinct from 'array' or jsonb_array_length(p_summary->'sales'->'daily')>30
 or jsonb_typeof(p_summary->'replenishment'->'cycles') is distinct from 'array' or jsonb_array_length(p_summary->'replenishment'->'cycles')<>2
 or jsonb_typeof(p_summary->'priorities') is distinct from 'array' or jsonb_array_length(p_summary->'priorities')>5 then
 raise exception 'Séries ou prioridades inválidas.' using errcode='22023'; end if;
 for item in select value from jsonb_array_elements(p_summary->'sales'->'daily') loop
 if jsonb_typeof(item) is distinct from 'object' or (select count(*) from jsonb_object_keys(item))<>2
 or not item?'date' or not item?'units' or item->>'date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
 or jsonb_typeof(item->'units') is distinct from 'number' then raise exception 'Série diária inválida.' using errcode='22023'; end if;
 end loop;
 for item in select value from jsonb_array_elements(p_summary->'replenishment'->'cycles') loop
 if jsonb_typeof(item) is distinct from 'object' or (select count(*) from jsonb_object_keys(item))<>2
 or not item?'days' or not item?'boxes' or jsonb_typeof(item->'days') is distinct from 'number'
 or jsonb_typeof(item->'boxes') not in ('number','null') then raise exception 'Ciclo inválido.' using errcode='22023'; end if;
 end loop;
 for item in select value from jsonb_array_elements(p_summary->'priorities') loop
 if jsonb_typeof(item) is distinct from 'object' or (select count(*) from jsonb_object_keys(item))<>3
 or not item ?& array['kind','count','priority'] or item->>'kind' not in ('risk','stockout','production','excess','quality')
 or jsonb_typeof(item->'count') is distinct from 'number' or jsonb_typeof(item->'priority') is distinct from 'number' then
 raise exception 'Prioridade inválida.' using errcode='22023'; end if;
 end loop;
 insert into public.dashboard_snapshots(organization_id,batch_id,revision,summary,author_id)
 values(org,p_batch,p_revision,p_summary,auth.uid()) on conflict(organization_id) do update set
 batch_id=excluded.batch_id,revision=excluded.revision,summary=excluded.summary,author_id=excluded.author_id,calculated_at=now();
 return jsonb_build_object('published',true);
end $$;

-- Totais comerciais são atualizados na publicação/reversão, não na navegação.
create table public.dashboard_commercial_totals (
 organization_id uuid not null, source_id uuid not null, store_id text not null,
 batch_id uuid not null, customer_alerts integer not null, customer_decline integer not null,
 city_alerts integer not null, city_decline integer not null, pairs integer not null,
 period_start date not null, period_end date not null, computed_at timestamptz not null default now(),
 primary key(organization_id,source_id,store_id),
 foreign key(organization_id,batch_id) references public.commercial_batches(organization_id,id)
);
alter table public.dashboard_commercial_totals enable row level security;
revoke all on public.dashboard_commercial_totals from public,anon,authenticated,service_role;
create function private.dashboard_commercial_refresh(p_org uuid,p_source uuid,p_store text,p_batch uuid) returns void
language sql security definer set search_path='' as $$
 insert into public.dashboard_commercial_totals(organization_id,source_id,store_id,batch_id,
 customer_alerts,customer_decline,city_alerts,city_decline,pairs,period_start,period_end)
 select b.organization_id,b.source_id,b.store_id,b.id,
 count(distinct i.customer_ref) filter(where i.dimension='customer'),
 count(distinct i.customer_ref) filter(where i.alert_code in ('volume_reduzido','cadencia_atrasada')),
 count(distinct i.city_code) filter(where i.dimension='city'),
 count(distinct i.city_code) filter(where i.alert_code='cidade_queda'),
 count(*) filter(where i.dimension='pair'),b.period_start,b.period_end
 from public.commercial_batches b left join public.commercial_insights i on i.batch_id=b.id and i.organization_id=b.organization_id
 where b.organization_id=p_org and b.source_id=p_source and b.store_id=p_store and b.id=p_batch
 group by b.organization_id,b.source_id,b.store_id,b.id,b.period_start,b.period_end
 on conflict(organization_id,source_id,store_id) do update set batch_id=excluded.batch_id,
 customer_alerts=excluded.customer_alerts,customer_decline=excluded.customer_decline,
 city_alerts=excluded.city_alerts,city_decline=excluded.city_decline,pairs=excluded.pairs,
 period_start=excluded.period_start,period_end=excluded.period_end,computed_at=now();
$$;
create function private.dashboard_commercial_trigger() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 perform private.dashboard_commercial_refresh(new.organization_id,new.source_id,new.store_id,new.batch_id);
 return new;
end $$;
revoke all on function private.dashboard_commercial_refresh(uuid,uuid,text,uuid),private.dashboard_commercial_trigger() from public,anon,authenticated,service_role;
create trigger commercial_dashboard_refresh after insert or update on public.commercial_active_batches
 for each row execute function private.dashboard_commercial_trigger();
do $$declare b record; begin
 for b in select * from public.commercial_active_batches loop
 perform private.dashboard_commercial_refresh(b.organization_id,b.source_id,b.store_id,b.batch_id);
 end loop;
end $$;

create function private.dashboard_payload() returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid; r public.dashboard_snapshots; meta jsonb; resumo jsonb; resultado jsonb; fiscal jsonb; comercial jsonb;
begin
 org:=private.require_permission('dashboard.view');
 select * into r from public.dashboard_snapshots where organization_id=org;
 select jsonb_build_object('source',source,'snapshot_date',snapshot_date,'period_start',period_start,'period_end',period_end)
 into meta from public.inventory_imports where organization_id=org and id=coalesce(r.batch_id,(select batch_id from public.inventory_current where organization_id=org));
 resumo:=r.summary;
 if not private.has_permission('replenishment.view') then resumo:=resumo-'replenishment';
 resumo:=jsonb_set(resumo,'{priorities}',coalesce((select jsonb_agg(i) from jsonb_array_elements(resumo->'priorities') i where i->>'kind'<>'production'),'[]')); end if;
 if not private.has_permission('trends.view') then resumo:=resumo-'sales'; end if;
 if not private.has_permission('finance.view') then resumo:=resumo-'finance'; end if;
 resultado:=jsonb_build_object('summary',resumo,'metadata',meta,'calculated_at',r.calculated_at,
 'stale',r.organization_id is null,'missing',r.organization_id is null);
 -- A revisão identifica mudanças de lote, parâmetros, embalagens e vendas.
 resultado:=jsonb_set(resultado,'{stale}',to_jsonb(r.organization_id is null or r.revision is distinct from private.dashboard_revision(org)));
 if private.has_permission('commercial.view') then
 select jsonb_build_object('customers',sum(t.customer_alerts),'customer_decline',sum(t.customer_decline),
 'cities',sum(t.city_alerts),'city_decline',sum(t.city_decline),'pairs',sum(t.pairs),
 'period_start',min(t.period_start),'period_end',max(t.period_end),'scopes',count(*)) into comercial
 from public.dashboard_commercial_totals t join public.integration_sources src on src.id=t.source_id and src.organization_id=t.organization_id and src.active
 where t.organization_id=org;
 if (comercial->>'scopes')::integer>0 then resultado:=resultado||jsonb_build_object('commercial',comercial); end if;
 end if;
 if private.has_permission('fiscal.view') then
 select jsonb_build_object('total',count(*),'pending',count(*) filter(where state in ('confirmed','awaiting_approval','processing')),
 'conference',count(*) filter(where state='needs_reconciliation'),'environment','demo') into fiscal
 from public.fiscal_requests where organization_id=org and (requester_id=auth.uid() or private.has_permission('fiscal.audit'));
 if (fiscal->>'total')::integer>0 then resultado:=resultado||jsonb_build_object('fiscal',fiscal); end if;
 end if;
 return resultado;
end $$;

revoke all on function private.dashboard_source(),private.dashboard_publish(uuid,text,jsonb),private.dashboard_payload() from public,anon,authenticated,service_role;
create function public.dashboard_source() returns jsonb language sql stable security invoker set search_path='' as $$select private.dashboard_source()$$;
create function public.dashboard_publish(p_batch uuid,p_revision text,p_summary jsonb) returns jsonb language sql security invoker set search_path='' as $$select private.dashboard_publish(p_batch,p_revision,p_summary)$$;
create function public.dashboard_payload() returns jsonb language sql stable security invoker set search_path='' as $$select private.dashboard_payload()$$;
revoke all on function public.dashboard_source(),public.dashboard_publish(uuid,text,jsonb),public.dashboard_payload() from public,anon,authenticated,service_role;
grant execute on function private.dashboard_source(),private.dashboard_publish(uuid,text,jsonb),private.dashboard_payload(),
 public.dashboard_source(),public.dashboard_publish(uuid,text,jsonb),public.dashboard_payload() to authenticated;
notify pgrst,'reload schema';
