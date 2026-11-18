begin;
-- Correção da métrica de DEMANDA COMERCIAL FECHADA:
-- Em separação e Concretizada contam como um único pedido em seu status atual.
-- Não altera estoque, histórico de linhas, permissões ou regras de exclusão.
create or replace function private.sync_preview(p_run_id uuid,p_claim_token uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare r public.sync_runs:=private.sync_lock_run(p_run_id,p_claim_token); q numeric;
begin
  select coalesce(sum(case when payload->>'status' in ('separating','completed')
    then (payload->>'quantity')::numeric-(payload->>'returned_quantity')::numeric else 0 end),0) into q
    from public.sync_staging where run_id=r.id;
  if r.row_count<>r.expected_rows or q<>r.expected_quantity then
    raise exception 'Reconciliação de quantidade ou registros não confere.' using errcode='22023'; end if;
  update public.sync_runs set status='previewed',net_quantity=q,lease_until=now()+interval '30 minutes'
    where id=r.id returning * into r;
  return private.sync_run_payload(r)||jsonb_build_object('sample',coalesce((select jsonb_agg(payload) from
    (select payload from public.sync_staging where run_id=r.id order by document_id,line_id limit 10) t),'[]'::jsonb));
end $$;
create or replace function private.sync_commit(p_run_id uuid,p_claim_token uuid,p_coverage_confirmed boolean default false) returns jsonb
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
    select count(*),coalesce(sum(case when v.payload->>'status' in ('separating','completed') then
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
commit;
