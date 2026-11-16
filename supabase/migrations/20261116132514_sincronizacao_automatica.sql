begin;
-- Agendamento restrito da cópia analítica. Nenhuma função acessa o ERP.
-- Os núcleos abaixo conservam reconciliação/idempotência do fluxo manual;
-- somente wrappers com sessão ou delegação validada podem executá-los.
create function private.sync_actor_scope(p_org uuid,p_actor uuid,p_source_id uuid,p_store_id text)
returns uuid language plpgsql stable security definer set search_path='' as $$
begin
  if not exists(select 1 from public.app_users where user_id=p_actor and organization_id=p_org
    and active and role='Master') or not private.user_has_permission(p_actor,'integrations.manage')
    or not exists(select 1 from public.integration_sources where id=p_source_id and organization_id=p_org
      and active and p_store_id=any(stores)) then
    raise exception 'Delegação ou fonte não autorizada.' using errcode='42501';
  end if;
  return p_org;
end $$;

create function private.sync_begin_core(p_org uuid,p_actor uuid,p_source_id uuid,p_store_id text,p_request_key uuid,p_period_start date,
  p_period_end date,p_expected_rows integer,p_expected_quantity numeric,p_content_hash text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.sync_actor_scope(p_org,p_actor,p_source_id,p_store_id); r public.sync_runs;
begin
  if p_content_hash is null or p_content_hash !~ '^[0-9a-f]{64}$'
    or p_request_key is null or p_period_start is null or p_period_end is null or p_period_start>p_period_end
    or p_period_end-p_period_start>731
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
      values(org,p_source_id,p_store_id,p_actor,p_request_key,'staging',p_period_start,p_period_end,p_expected_rows,p_expected_quantity,p_content_hash)
      returning * into r;
  end if;
  return private.sync_run_payload(r);
end $$;

create function private.sync_lock_run_core(p_org uuid,p_actor uuid,p_run_id uuid,p_claim_token uuid) returns public.sync_runs
language plpgsql security definer set search_path='' as $$
declare org uuid:=p_org; r public.sync_runs;
begin
  select * into r from public.sync_runs where id=p_run_id and organization_id=org for update;
  if not found or r.claim_token is distinct from p_claim_token or r.status not in ('staging','previewed')
    or r.lease_until<now() then raise exception 'Execução não autorizada ou concessão expirada.' using errcode='42501'; end if;
  perform private.sync_actor_scope(p_org,p_actor,r.source_id,r.store_id);
  return r;
end $$;

create function private.sync_stage_core(p_org uuid,p_actor uuid,p_run_id uuid,p_claim_token uuid,p_page integer,p_rows jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare r public.sync_runs:=private.sync_lock_run_core(p_org,p_actor,p_run_id,p_claim_token); fp text; row jsonb; n integer;
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

create function private.sync_preview_core(p_org uuid,p_actor uuid,p_run_id uuid,p_claim_token uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare r public.sync_runs:=private.sync_lock_run_core(p_org,p_actor,p_run_id,p_claim_token); q numeric;
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

create function private.sync_commit_core(p_org uuid,p_actor uuid,p_run_id uuid,p_claim_token uuid,p_coverage_confirmed boolean default false) returns jsonb
language plpgsql security definer set search_path='' as $$
declare r public.sync_runs; row record; old jsonb; v bigint; changed integer:=0; reconciled_rows integer; reconciled_quantity numeric; org uuid:=p_org;
begin
  select * into r from public.sync_runs where id=p_run_id and organization_id=org for update;
  if found and r.status='completed' and r.claim_token=p_claim_token then return private.sync_run_payload(r); end if;
  r:=private.sync_lock_run_core(p_org,p_actor,p_run_id,p_claim_token);
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
    values(org,p_actor,'integrations.sync',r.id::text,jsonb_build_object('rows',r.row_count,'changed',changed));
  return private.sync_run_payload(r);
end $$;

create function private.sync_cancel_core(p_org uuid,p_actor uuid,p_run_id uuid,p_claim_token uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=p_org; r public.sync_runs;
begin
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

create or replace function private.sync_begin(p_source_id uuid,p_store_id text,p_request_key uuid,p_period_start date,
  p_period_end date,p_expected_rows integer,p_expected_quantity numeric,p_content_hash text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.require_master();
begin
  perform private.require_permission('integrations.manage');
  return private.sync_begin_core(org,auth.uid(),p_source_id,p_store_id,p_request_key,p_period_start,p_period_end,p_expected_rows,p_expected_quantity,p_content_hash);
end $$;

create or replace function private.sync_stage(p_run_id uuid,p_claim_token uuid,p_page integer,p_rows jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.require_master();
begin
  perform private.require_permission('integrations.manage');
  return private.sync_stage_core(org,auth.uid(),p_run_id,p_claim_token,p_page,p_rows);
end $$;

create or replace function private.sync_preview(p_run_id uuid,p_claim_token uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.require_master();
begin
  perform private.require_permission('integrations.manage');
  return private.sync_preview_core(org,auth.uid(),p_run_id,p_claim_token);
end $$;

create or replace function private.sync_commit(p_run_id uuid,p_claim_token uuid,p_coverage_confirmed boolean default false) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.require_master();
begin
  perform private.require_permission('integrations.manage');
  return private.sync_commit_core(org,auth.uid(),p_run_id,p_claim_token,p_coverage_confirmed);
end $$;

create or replace function private.sync_cancel(p_run_id uuid,p_claim_token uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.require_master();
begin
  perform private.require_permission('integrations.manage');
  return private.sync_cancel_core(org,auth.uid(),p_run_id,p_claim_token);
end $$;

-- Configuração privada: sem credenciais ERP, linhas de venda ou acesso direto.
create table private.sync_automations (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id),
  source_id uuid not null,
  store_id text not null check(length(btrim(store_id)) between 1 and 120),
  approved_by uuid not null references auth.users(id),
  approved_at timestamptz not null default now(),
  contract_hash text not null check(contract_hash ~ '^[0-9a-f]{64}$'),
  secret_hash text not null unique check(secret_hash ~ '^[0-9a-f]{64}$'),
  enabled boolean not null default false,
  interval_hours integer not null default 24 check(interval_hours between 6 and 168),
  history_start date not null,
  history_end date not null,
  cursor_end date,
  rotation_end date not null,
  recent_through date not null,
  recent_next boolean not null default true,
  next_due timestamptz not null default now(),
  claim uuid,
  lease_until timestamptz,
  job_start date,
  job_end date,
  job_kind text check(job_kind in ('backfill','recent','historical')),
  request_key uuid,
  run_id uuid,
  run_claim_token uuid,
  last_success timestamptz,
  last_error_code text,
  updated_at timestamptz not null default now(),
  check(history_end>=history_start and history_end-history_start<=731),
  check(job_end is null or (job_start is not null and job_end between job_start and job_start+29)),
  unique(organization_id,source_id,store_id),
  foreign key(organization_id,source_id) references public.integration_sources(organization_id,id),
  foreign key(organization_id,run_id) references public.sync_runs(organization_id,id)
);
alter table private.sync_automations enable row level security;
revoke all on private.sync_automations from public,anon,authenticated,service_role;

create function private.sync_automation_payload(a private.sync_automations) returns jsonb
language sql stable security definer set search_path='' as $$
  select jsonb_build_object('automation_id',a.id,'enabled',a.enabled,'source_id',a.source_id,
    'store_id',a.store_id,'contract_hash',a.contract_hash,'history_start',a.history_start,
    'history_end',a.history_end,'interval_hours',a.interval_hours,'overlap_days',14,
    'phase',case when a.cursor_end is not null then 'backfill' else 'incremental' end,
    'cursor_end',a.cursor_end,'recent_through',a.recent_through,'next_due',a.next_due,'last_success',a.last_success,
    'last_error_code',a.last_error_code,'credential_configured',true,
    'running',coalesce(a.lease_until>now(),false),
    'run_count',(select count(*) from public.sync_runs where organization_id=a.organization_id),
    'max_runs',1000)
$$;

create function private.sync_automation_status(p_source_id uuid,p_store_id text) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid:=private.sync_source(p_source_id,p_store_id); a private.sync_automations;
begin
  select * into a from private.sync_automations where organization_id=org and source_id=p_source_id and store_id=p_store_id;
  if not found then return jsonb_build_object('enabled',false,'credential_configured',false,
    'source_id',p_source_id,'store_id',p_store_id,'phase','not_configured','max_runs',1000,
    'run_count',(select count(*) from public.sync_runs where organization_id=org)); end if;
  return private.sync_automation_payload(a);
end $$;

create function private.sync_automation_stop(a private.sync_automations) returns void
language plpgsql security definer set search_path='' as $$
declare r public.sync_runs;
begin
  if a.run_id is not null then
    select * into r from public.sync_runs where id=a.run_id and organization_id=a.organization_id;
    if found and r.status in ('staging','previewed','interrupted') then
      -- Somente staging descartável. Histórico consolidado nunca é excluído.
      perform private.sync_cancel_core(a.organization_id,a.approved_by,r.id,r.claim_token);
    end if;
  end if;
end $$;

create function private.sync_automation_configure(p_source_id uuid,p_store_id text,p_contract_hash text,
  p_history_start date,p_history_end date,p_interval_hours integer,p_enabled boolean,p_approved boolean,
  p_rotate_secret boolean default false) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.sync_source(p_source_id,p_store_id); a private.sync_automations;
  token text; changed boolean; result jsonb;
begin
  if p_contract_hash is null or p_contract_hash !~ '^[0-9a-f]{64}$' or p_history_start is null
    or p_history_end is null or p_history_end<p_history_start or p_history_end-p_history_start>731
    or p_history_end>(now() at time zone 'America/Fortaleza')::date
    or p_interval_hours is null or p_interval_hours not between 6 and 168
    or p_enabled is null or p_rotate_secret is null or p_approved is not true then
    raise exception 'Confira e aprove contrato, período e agendamento.' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_source_id::text||'/'||p_store_id,99));
  select * into a from private.sync_automations where organization_id=org and source_id=p_source_id and store_id=p_store_id for update;
  if not found then
    -- Dois UUIDs criptográficos fornecem credencial aleatória; somente o hash persiste.
    token:=replace(gen_random_uuid()::text||gen_random_uuid()::text,'-','');
    insert into private.sync_automations(organization_id,source_id,store_id,approved_by,contract_hash,
      secret_hash,enabled,interval_hours,history_start,history_end,cursor_end,rotation_end,recent_through)
    values(org,p_source_id,p_store_id,auth.uid(),p_contract_hash,
      encode(sha256(convert_to(token,'UTF8')),'hex'),p_enabled,p_interval_hours,
      p_history_start,p_history_end,p_history_end,p_history_end,p_history_end) returning * into a;
  else
    changed:=a.contract_hash<>p_contract_hash or a.history_start<>p_history_start or a.history_end<>p_history_end;
    a.approved_by:=auth.uid();
    perform private.sync_automation_stop(a);
    if p_rotate_secret then token:=replace(gen_random_uuid()::text||gen_random_uuid()::text,'-',''); end if;
    update private.sync_automations set approved_by=auth.uid(),approved_at=now(),contract_hash=p_contract_hash,
      secret_hash=case when token is null then secret_hash else encode(sha256(convert_to(token,'UTF8')),'hex') end,
      enabled=p_enabled,interval_hours=p_interval_hours,history_start=p_history_start,history_end=p_history_end,
      cursor_end=case when changed then p_history_end else cursor_end end,
      rotation_end=case when changed then p_history_end else rotation_end end,
      recent_through=case when changed then p_history_end else recent_through end,
      next_due=now(),claim=null,lease_until=null,job_start=null,job_end=null,job_kind=null,
      request_key=null,run_id=null,run_claim_token=null,updated_at=now(),last_error_code=null
      where id=a.id returning * into a;
  end if;
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'integrations.automation.configure',a.id::text,
      jsonb_build_object('enabled',a.enabled,'rotated',p_rotate_secret,'interval_hours',a.interval_hours));
  result:=private.sync_automation_payload(a);
  if token is not null then result:=result||jsonb_build_object('secret',token); end if;
  return result;
end $$;

create function private.sync_automation_pause(p_source_id uuid,p_store_id text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid:=private.require_master(); a private.sync_automations;
begin
  perform private.require_permission('integrations.manage');
  select * into a from private.sync_automations where organization_id=org and source_id=p_source_id and store_id=p_store_id for update;
  if not found then raise exception 'Automação não encontrada.' using errcode='42501'; end if;
  -- A pausa permanece disponível mesmo se a fonte, aprovador ou contrato foram revogados.
  update private.sync_automations set enabled=false,claim=null,lease_until=null,updated_at=now()
    where id=a.id returning * into a;
  insert into public.audit_events(organization_id,author_id,action,target)
    values(org,auth.uid(),'integrations.automation.pause',a.id::text);
  return private.sync_automation_payload(a);
end $$;

create function private.sync_job_authorize(p_secret text,p_claim uuid default null)
returns private.sync_automations language plpgsql security definer set search_path='' as $$
declare a private.sync_automations;
begin
  if p_secret is null or p_secret !~ '^[0-9a-f]{64}$' then
    raise exception 'Agendador não autorizado.' using errcode='42501'; end if;
  select * into a from private.sync_automations
    where secret_hash=encode(sha256(convert_to(p_secret,'UTF8')),'hex') for update;
  if not found or not a.enabled then raise exception 'Agendador não autorizado.' using errcode='42501'; end if;
  perform private.sync_actor_scope(a.organization_id,a.approved_by,a.source_id,a.store_id);
  if p_claim is not null and (a.claim is distinct from p_claim or a.lease_until is null or a.lease_until<=now()) then
    raise exception 'Concessão do agendador inválida ou expirada.' using errcode='42501'; end if;
  return a;
end $$;

create function private.sync_job_claim(p_secret text,p_contract_hash text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare a private.sync_automations:=private.sync_job_authorize(p_secret); today date:=(now() at time zone 'America/Fortaleza')::date;
  result jsonb; r public.sync_runs;
begin
  if p_contract_hash is null or p_contract_hash is distinct from a.contract_hash then
    raise exception 'Contrato diferente da aprovação vigente.' using errcode='42501'; end if;
  if a.lease_until>now() or a.next_due>now() then return null; end if;
  if a.run_id is null and (select count(*) from public.sync_runs where organization_id=a.organization_id)>=1000 then
    -- Pausar antes da consulta externa. Não apagar histórico para liberar cota.
    update private.sync_automations set enabled=false,last_error_code='RUN_LIMIT',updated_at=now() where id=a.id;
    return null;
  end if;
  if a.run_id is null and (select count(*) from public.sync_runs where organization_id=a.organization_id
      and status not in ('completed','cancelled'))>=20 then
    update private.sync_automations set next_due=now()+interval '60 minutes',last_error_code='RUNS_PENDING',updated_at=now() where id=a.id;
    return null;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(a.source_id::text||'/'||a.store_id,12));
  if exists(select 1 from public.sync_runs where source_id=a.source_id and store_id=a.store_id
    and status in ('staging','previewed') and lease_until>now() and id is distinct from a.run_id) then return null; end if;
  -- Uma tentativa interrompida conserva a janela e a chave. Nunca avança em erro.
  if a.job_start is null then
    if a.cursor_end is not null then
      a.job_kind:='backfill'; a.job_end:=a.cursor_end; a.job_start:=greatest(a.history_start,a.job_end-29);
    elsif a.recent_next then
      -- Marca contínua: após uma pausa longa, recuperar lacunas em blocos,
      -- com sobreposição de quatorze dias e sem pular direto para hoje.
      a.job_kind:='recent'; a.job_start:=greatest(a.history_start,least(a.recent_through,today)-13);
      a.job_end:=least(today,a.job_start+29);
    else
      a.job_kind:='historical'; a.job_end:=least(a.rotation_end,a.recent_through);
      a.job_start:=greatest(a.history_start,a.job_end-29);
    end if;
  end if;
  update private.sync_automations set claim=gen_random_uuid(),lease_until=now()+interval '15 minutes',
    job_start=a.job_start,job_end=a.job_end,job_kind=a.job_kind,request_key=coalesce(a.request_key,gen_random_uuid()),updated_at=now()
    where id=a.id returning * into a;
  result:=jsonb_build_object('automation_id',a.id,'claim',a.claim,'source_id',a.source_id,'store_id',a.store_id,
    'organization_id',a.organization_id,'actor_id',a.approved_by,'contract_hash',a.contract_hash,
    'start',a.job_start,'end',a.job_end,'request_key',a.request_key);
  if a.run_id is not null then
    select * into r from public.sync_runs where id=a.run_id and organization_id=a.organization_id;
    if found and r.status='completed' then result:=result||jsonb_build_object('completed_run',private.sync_run_payload(r)); end if;
  end if;
  return result;
end $$;

create function private.sync_job_step(p_secret text,p_claim uuid,p_action text,p_payload jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare a private.sync_automations; result jsonb; r public.sync_runs; permitted text[]; error_code text;
begin
  if p_claim is null or p_action is null or p_action not in ('authorize','begin','stage','preview','commit','cancel','finish','fail')
    or jsonb_typeof(p_payload) is distinct from 'object' or octet_length(p_payload::text)>1100000 then
    raise exception 'Etapa do agendador inválida.' using errcode='22023'; end if;
  a:=private.sync_job_authorize(p_secret,p_claim);
  permitted:=case p_action
    when 'authorize' then array['p_source_id','p_store_id']
    when 'begin' then array['p_source_id','p_store_id','p_request_key','p_period_start','p_period_end','p_expected_rows','p_expected_quantity','p_content_hash']
    when 'stage' then array['p_run_id','p_claim_token','p_page','p_rows']
    when 'preview' then array['p_run_id','p_claim_token']
    when 'commit' then array['p_run_id','p_claim_token','p_coverage_confirmed']
    when 'cancel' then array['p_run_id','p_claim_token']
    when 'fail' then array['error_code'] else array[]::text[] end;
  if exists(select 1 from jsonb_object_keys(p_payload) k where not k=any(permitted)) then
    raise exception 'Parâmetro fora do contrato do agendador.' using errcode='22023'; end if;
  if p_action in ('authorize','begin') and
    ((p_payload->>'p_source_id') is distinct from a.source_id::text or (p_payload->>'p_store_id') is distinct from a.store_id) then
    raise exception 'Escopo do agendador divergente.' using errcode='42501'; end if;
  if p_action in ('stage','preview','commit','cancel') and
    (a.run_id is null or (p_payload->>'p_run_id') is distinct from a.run_id::text
      or (p_payload->>'p_claim_token') is distinct from a.run_claim_token::text) then
    raise exception 'Execução não pertence à concessão.' using errcode='42501'; end if;
  case p_action
  when 'authorize' then
    result:=jsonb_build_object('allowed',true,'organization_id',a.organization_id,'actor_id',a.approved_by,
      'source_id',a.source_id,'store_id',a.store_id,'contract_hash',a.contract_hash);
  when 'begin' then
    if (p_payload->>'p_request_key') is distinct from a.request_key::text
      or (p_payload->>'p_period_start') is distinct from a.job_start::text
      or (p_payload->>'p_period_end') is distinct from a.job_end::text then
      raise exception 'Janela ou repetição divergente da concessão.' using errcode='42501'; end if;
    result:=private.sync_begin_core(a.organization_id,a.approved_by,a.source_id,a.store_id,a.request_key,
      a.job_start,a.job_end,(p_payload->>'p_expected_rows')::integer,(p_payload->>'p_expected_quantity')::numeric,p_payload->>'p_content_hash');
    update private.sync_automations set run_id=(result->>'id')::uuid,run_claim_token=(result->>'claim_token')::uuid
      where id=a.id;
  when 'stage' then
    result:=private.sync_stage_core(a.organization_id,a.approved_by,a.run_id,a.run_claim_token,
      (p_payload->>'p_page')::integer,p_payload->'p_rows');
  when 'preview' then
    result:=private.sync_preview_core(a.organization_id,a.approved_by,a.run_id,a.run_claim_token);
  when 'commit' then
    if p_payload->'p_coverage_confirmed' is distinct from 'false'::jsonb then
      raise exception 'Agendamento não declara conferência humana de abrangência.' using errcode='22023'; end if;
    result:=private.sync_commit_core(a.organization_id,a.approved_by,a.run_id,a.run_claim_token,false);
  when 'cancel' then
    result:=private.sync_cancel_core(a.organization_id,a.approved_by,a.run_id,a.run_claim_token);
    update private.sync_automations set run_id=null,run_claim_token=null,request_key=null where id=a.id;
  when 'finish' then
    select * into r from public.sync_runs where id=a.run_id and organization_id=a.organization_id
      and source_id=a.source_id and store_id=a.store_id and request_key=a.request_key and status='completed';
    if not found then raise exception 'Lote ainda não consolidado.' using errcode='22023'; end if;
    update private.sync_automations set
      cursor_end=case when a.job_kind='backfill' then case when a.job_start>a.history_start then a.job_start-1 else null end else cursor_end end,
      rotation_end=case when a.job_kind='historical' then case when a.job_start>a.history_start then a.job_start-1 else recent_through end else rotation_end end,
      recent_through=case when a.job_kind='recent' then greatest(recent_through,a.job_end) else recent_through end,
      recent_next=case when a.job_kind='recent' then a.job_end<(now() at time zone 'America/Fortaleza')::date when a.job_kind='historical' then true else recent_next end,
      next_due=now()+case when (a.job_kind='backfill' and a.job_start>a.history_start)
        or (a.job_kind='recent' and a.job_end<(now() at time zone 'America/Fortaleza')::date) then interval '60 minutes'
        else make_interval(hours=>a.interval_hours) end,
      claim=null,lease_until=null,job_start=null,job_end=null,job_kind=null,request_key=null,run_id=null,run_claim_token=null,
      last_success=now(),last_error_code=null,updated_at=now() where id=a.id;
    insert into public.audit_events(organization_id,author_id,action,target,details)
      values(a.organization_id,a.approved_by,'integrations.automation.finish',a.id::text,
        jsonb_build_object('run_id',r.id,'rows',r.row_count,'changed',r.changed_rows,'coverage_confirmed',false));
    return jsonb_build_object('status','completed','finished',true);
  when 'fail' then
    error_code:=coalesce(p_payload->>'error_code','SYNC_FAILED');
    if error_code not in ('SYNC_FAILED','SOURCE_UNAVAILABLE','CONTRACT_INVALID','AUTHORIZATION_DENIED','BUDGET_EXCEEDED') then
      raise exception 'Código de falha não permitido.' using errcode='22023'; end if;
    select * into r from public.sync_runs where id=a.run_id and organization_id=a.organization_id;
    if found and r.status='completed' then
      -- Resposta perdida após commit: preservar vínculo para finish sem reler ERP.
      update private.sync_automations set next_due=now()+interval '60 minutes',claim=null,lease_until=null,
        last_error_code=error_code,updated_at=now() where id=a.id;
    else
      perform private.sync_automation_stop(a);
      update private.sync_automations set next_due=now()+interval '60 minutes',claim=null,lease_until=null,
        run_id=null,run_claim_token=null,request_key=null,last_error_code=error_code,updated_at=now() where id=a.id;
    end if;
    return jsonb_build_object('failed',true);
  end case;
  update private.sync_automations set lease_until=now()+interval '15 minutes',updated_at=now() where id=a.id;
  return result;
end $$;

-- Wrappers públicos só encaminham argumentos aos controles privados fechados.
create function public.sync_automation_configure(p_source_id uuid,p_store_id text,p_contract_hash text,
  p_history_start date,p_history_end date,p_interval_hours integer,p_enabled boolean,p_approved boolean,
  p_rotate_secret boolean default false) returns jsonb language sql security invoker set search_path='' as $$
  select private.sync_automation_configure(p_source_id,p_store_id,p_contract_hash,p_history_start,p_history_end,
    p_interval_hours,p_enabled,p_approved,p_rotate_secret)
$$;
create function public.sync_automation_status(p_source_id uuid,p_store_id text) returns jsonb
language sql stable security invoker set search_path='' as $$select private.sync_automation_status(p_source_id,p_store_id)$$;
create function public.sync_automation_pause(p_source_id uuid,p_store_id text) returns jsonb
language sql security invoker set search_path='' as $$select private.sync_automation_pause(p_source_id,p_store_id)$$;
create function public.sync_job_claim(p_secret text,p_contract_hash text) returns jsonb
language sql security invoker set search_path='' as $$select private.sync_job_claim(p_secret,p_contract_hash)$$;
create function public.sync_job_step(p_secret text,p_claim uuid,p_action text,p_payload jsonb) returns jsonb
language sql security invoker set search_path='' as $$select private.sync_job_step(p_secret,p_claim,p_action,p_payload)$$;

-- Nenhum executor pode chamar diretamente os núcleos ou ler a credencial armazenada.
revoke all on function private.sync_actor_scope(uuid,uuid,uuid,text),
  private.sync_begin_core(uuid,uuid,uuid,text,uuid,date,date,integer,numeric,text),
  private.sync_lock_run_core(uuid,uuid,uuid,uuid), private.sync_stage_core(uuid,uuid,uuid,uuid,integer,jsonb),
  private.sync_preview_core(uuid,uuid,uuid,uuid), private.sync_commit_core(uuid,uuid,uuid,uuid,boolean),
  private.sync_cancel_core(uuid,uuid,uuid,uuid),private.sync_automation_payload(private.sync_automations),
  private.sync_automation_stop(private.sync_automations),private.sync_job_authorize(text,uuid)
  from public,anon,authenticated,service_role;
revoke all on function public.sync_automation_configure(uuid,text,text,date,date,integer,boolean,boolean,boolean),
  private.sync_automation_configure(uuid,text,text,date,date,integer,boolean,boolean,boolean),
  public.sync_automation_status(uuid,text),private.sync_automation_status(uuid,text),
  public.sync_automation_pause(uuid,text),private.sync_automation_pause(uuid,text),
  public.sync_job_claim(text,text),private.sync_job_claim(text,text),
  public.sync_job_step(text,uuid,text,jsonb),private.sync_job_step(text,uuid,text,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.sync_automation_configure(uuid,text,text,date,date,integer,boolean,boolean,boolean),
  private.sync_automation_configure(uuid,text,text,date,date,integer,boolean,boolean,boolean),
  public.sync_automation_status(uuid,text),private.sync_automation_status(uuid,text),
  public.sync_automation_pause(uuid,text),private.sync_automation_pause(uuid,text) to authenticated;
grant execute on function public.sync_job_claim(text,text),private.sync_job_claim(text,text),
  public.sync_job_step(text,uuid,text,jsonb),private.sync_job_step(text,uuid,text,jsonb) to anon,authenticated;
notify pgrst,'reload schema';
commit;
