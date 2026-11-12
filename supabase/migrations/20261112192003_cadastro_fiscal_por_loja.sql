-- Regra de 12/11/2026: API única e lojas distintas. Somente cadastro de NF-e.
-- Emissão exclusivamente manual no ERP. Nenhuma operação real é ativada.
-- Os campos source_account/destination_account anteriores são aliases legados.
-- Não exclui registros, eventos, escolhas ou resultados da versão anterior.
alter table public.fiscal_previews
 add column api_scope text, add column source_store text,
 add column destination_store text, add column policy_version integer;
alter table public.fiscal_requests
 add column api_scope text, add column source_store text,
 add column destination_store text, add column policy_version integer;
alter table public.fiscal_previews add constraint fiscal_preview_store_scope check(
 (policy_version is null and api_scope is null and source_store is null and destination_store is null)
 or (policy_version is not null and policy_version=2 and length(api_scope) between 1 and 120 and length(source_store) between 1 and 120
 and length(destination_store) between 1 and 120 and source_store<>destination_store
 and api_scope is not null and source_store is not null and destination_store is not null));
alter table public.fiscal_requests add constraint fiscal_request_store_scope check(
 (policy_version is null and api_scope is null and source_store is null and destination_store is null)
 or (policy_version is not null and policy_version=2 and length(api_scope) between 1 and 120 and length(source_store) between 1 and 120
 and length(destination_store) between 1 and 120 and source_store<>destination_store
 and api_scope is not null and source_store is not null and destination_store is not null));
-- Substitui a unicidade anterior por duas restrições que também preservam o legado.
alter table public.fiscal_requests drop constraint fiscal_requests_organization_id_source_account_order_id_des_key;
create unique index fiscal_legacy_identity on public.fiscal_requests
 (organization_id,source_account,order_id,destination_account,environment) where policy_version is null;
create unique index fiscal_store_identity on public.fiscal_requests
 (organization_id,api_scope,source_store,order_id,destination_store,environment) where policy_version=2;
alter table public.fiscal_requests drop constraint fiscal_requests_state_check;
alter table public.fiscal_requests add constraint fiscal_requests_state_check check(state in
 ('awaiting_approval','confirmed','processing','registered','authorized','rejected','needs_reconciliation','blocked'));
alter table public.fiscal_requests add constraint fiscal_registered_not_issued check(
 state<>'registered' or (policy_version is not null and policy_version=2 and external_id is not null and invoice_key is null and invoice_number is null));


create or replace function private.fiscal_order(p_number text) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid; pedido jsonb; existente jsonb;
begin
 org:=private.require_permission('fiscal.view');
 if exists(select 1 from public.fiscal_configuration where organization_id=org and not demo_enabled) then raise exception 'Modo DEMO desativado pelo Master.' using errcode='42501'; end if;
 if p_number is null or p_number !~ '^[0-9]{1,20}$' then raise exception 'Número de pedido inválido.' using errcode='22023'; end if;
 pedido:=private.fiscal_demo_catalog()->'pedidos'->p_number;
 if pedido is null then raise exception 'Pedido DEMO não encontrado. Use os cenários informados na tela.' using errcode='22023'; end if;
 select jsonb_build_object('state',state,'environment',environment) into existente from public.fiscal_requests
 where organization_id=org and order_id=pedido->>'id' and (policy_version is null or (api_scope=pedido->>'api_scope' and source_store=pedido->>'origem' and destination_store=pedido->>'destino'));
 return pedido||jsonb_build_object('solicitacao_existente',existente,'real_enabled',false);
end $$;

create or replace function private.fiscal_preview(p_number text,p_choices jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; pedido jsonb; frete numeric; contribuinte numeric; impressao text; anterior public.fiscal_previews; resultado uuid;
begin
 org:=private.require_permission('fiscal.create_request'); pedido:=private.fiscal_order(p_number);
 if jsonb_typeof(p_choices) is distinct from 'object' or (select count(*) from jsonb_object_keys(p_choices))<>2
 or jsonb_typeof(p_choices->'modalidade_frete') is distinct from 'number'
 or jsonb_typeof(p_choices->'tipo_contribuinte') is distinct from 'number'
 or p_choices->>'modalidade_frete' !~ '^[0-9]$'
 or p_choices->>'tipo_contribuinte' !~ '^[0-9]$' then
 raise exception 'As duas seleções fiscais manuais são obrigatórias.' using errcode='22023'; end if;
 frete:=(p_choices->>'modalidade_frete')::numeric; contribuinte:=(p_choices->>'tipo_contribuinte')::numeric;
 if frete not in (0,1,2,3,4,9) or contribuinte not in (1,2,9) then raise exception 'Escolha fiscal inválida.' using errcode='22023'; end if;
 if pedido->>'uf' not in ('CE','Ceará','Ceara') or pedido->>'situacao' not in ('Em separação','Concretizada','Em andamento')
 or pedido->>'cenario' in ('incompleto','existente','denegada') or pedido->'solicitacao_existente' <> 'null'::jsonb then
 raise exception 'Pedido bloqueado, incompleto ou com nota/solicitação existente.' using errcode='22023'; end if;
 impressao:=encode(sha256(convert_to((pedido-'solicitacao_existente')::text||p_choices::text,'UTF8')),'hex');
 perform pg_advisory_xact_lock(hashtextextended('fiscal-preview:'||auth.uid()::text,0));
 select * into anterior from public.fiscal_previews where organization_id=org and author_id=auth.uid() and fingerprint=impressao
 and policy_version=2 and expires_at>now() order by created_at desc limit 1;
 if anterior.id is not null then return jsonb_build_object('id',anterior.id,'policy_version',2,'version',anterior.version,'fingerprint',impressao,'choices',p_choices,'order',pedido); end if;
 if (select count(*) from public.fiscal_previews where organization_id=org and author_id=auth.uid() and created_at>now()-interval '1 minute')>=10
 or (select count(*) from public.fiscal_previews where organization_id=org)>=10000 then
 raise exception 'Limite de prévias atingido.' using errcode='54000'; end if;
 insert into public.fiscal_previews(organization_id,author_id,order_number,order_id,source_account,destination_account,freight,taxpayer,fingerprint,api_scope,source_store,destination_store,policy_version)
 values(org,auth.uid(),p_number,pedido->>'id','DEMO-ATACADO','DEMO-VAREJO',frete::integer,contribuinte::integer,impressao,pedido->>'api_scope',pedido->>'origem',pedido->>'destino',2) returning id into resultado;
 return jsonb_build_object('id',resultado,'policy_version',2,'version',1,'fingerprint',impressao,'choices',p_choices,'order',pedido);
end $$;

create or replace function private.fiscal_confirm(p_preview uuid,p_confirm boolean,p_approve_pending boolean default false) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; p public.fiscal_previews; pedido jsonb; existente public.fiscal_requests; resultado uuid; estado text; aprova boolean;
begin
 org:=private.require_permission('fiscal.create_request'); perform private.require_permission('fiscal.view');
 if p_confirm is distinct from true or p_approve_pending is null then raise exception 'Confirme conscientemente a simulação.' using errcode='22023'; end if;
 select * into p from public.fiscal_previews where id=p_preview and organization_id=org and author_id=auth.uid();
 if p.id is null or p.expires_at<=now() or p.policy_version is distinct from 2 then raise exception 'Prévia ausente, expirada ou não autorizada.' using errcode='42501'; end if;
 perform pg_advisory_xact_lock(hashtextextended('fiscal:'||org::text||p.order_id,0));
 select * into existente from public.fiscal_requests where organization_id=org and order_id=p.order_id and environment=p.environment
 and (policy_version is null or (api_scope=p.api_scope and source_store=p.source_store and destination_store=p.destination_store));
 if existente.id is not null then return jsonb_build_object('duplicate',true,'id',case when existente.requester_id=auth.uid() or private.has_permission('fiscal.audit') then existente.id end,'state',existente.state); end if;
 pedido:=private.fiscal_order(p.order_number);
 if encode(sha256(convert_to((pedido-'solicitacao_existente')::text||jsonb_build_object('modalidade_frete',p.freight,'tipo_contribuinte',p.taxpayer)::text,'UTF8')),'hex')<>p.fingerprint then
 raise exception 'Os dados mudaram. Gere uma nova prévia.' using errcode='22023'; end if;
 aprova:=pedido->>'situacao'='Em andamento' and p_approve_pending;
 if p_approve_pending then perform private.require_permission('fiscal.approve_pending'); end if;
 estado:=case when pedido->>'situacao'='Em andamento' and not aprova then 'awaiting_approval' else 'confirmed' end;
 if (select count(*) from public.fiscal_requests where organization_id=org and requester_id=auth.uid() and created_at>now()-interval '1 minute')>=5
 or (select count(*) from public.fiscal_requests where organization_id=org)>=5000 then raise exception 'Limite de solicitações atingido.' using errcode='54000'; end if;
 insert into public.fiscal_requests(organization_id,preview_id,requester_id,requester_session,requester_expiry,approver_id,approver_session,approved_at,
 order_number,order_id,source_account,destination_account,environment,freight,taxpayer,preview_version,fingerprint,state,api_scope,source_store,destination_store,policy_version)
 values(org,p.id,auth.uid(),(auth.jwt()->>'session_id')::uuid,to_timestamp((auth.jwt()->>'exp')::numeric),
 case when aprova then auth.uid() end,case when aprova then (auth.jwt()->>'session_id')::uuid end,case when aprova then now() end,
 p.order_number,p.order_id,p.source_account,p.destination_account,p.environment,p.freight,p.taxpayer,p.version,p.fingerprint,estado,p.api_scope,p.source_store,p.destination_store,2) returning id into resultado;
 insert into public.fiscal_events(organization_id,request_id,actor_id,state,event_code) values(org,resultado,auth.uid(),estado,'demo_confirmed');
 return jsonb_build_object('id',resultado,'state',estado,'duplicate',false);
end $$;

create or replace function private.fiscal_preview_get(p_preview uuid) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid; p public.fiscal_previews;
begin
 org:=private.require_permission('fiscal.create_request');
 select * into p from public.fiscal_previews where id=p_preview and organization_id=org and author_id=auth.uid() and policy_version=2 and expires_at>now();
 if p.id is null then raise exception 'Prévia ausente, expirada ou não autorizada.' using errcode='42501'; end if;
 return jsonb_build_object('id',p.id,'policy_version',2,'version',p.version,'fingerprint',p.fingerprint,
 'choices',jsonb_build_object('modalidade_frete',p.freight,'tipo_contribuinte',p.taxpayer),'order',private.fiscal_order(p.order_number));
end $$;

create or replace function private.fiscal_approve(p_request uuid,p_confirm boolean) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; r public.fiscal_requests;
begin
 org:=private.require_permission('fiscal.approve_pending'); perform private.require_permission('fiscal.view');
 if p_confirm is distinct from true then raise exception 'Confirme a autorização.' using errcode='22023'; end if;
 select * into r from public.fiscal_requests where id=p_request and organization_id=org for update;
 if r.id is null or r.policy_version is distinct from 2 or r.state<>'awaiting_approval' or r.requester_expiry<=now()
 or not private.fiscal_actor_valid(r.requester_id,r.requester_session,org,'fiscal.create_request') then
 raise exception 'Solicitação não autorizável.' using errcode='42501'; end if;
 update public.fiscal_requests set state='confirmed',approver_id=auth.uid(),approver_session=(auth.jwt()->>'session_id')::uuid,approved_at=now(),updated_at=now() where id=r.id;
 insert into public.fiscal_events(organization_id,request_id,actor_id,state,event_code) values(org,r.id,auth.uid(),'confirmed','pending_order_approved');
 return jsonb_build_object('id',r.id,'state','confirmed');
end $$;

create or replace function private.fiscal_begin(p_request uuid,p_action text default 'execute_demo') returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; r public.fiscal_requests; concessao uuid;
begin
 org:=private.require_permission('fiscal.create_request'); perform private.require_permission('fiscal.view');
 if exists(select 1 from public.fiscal_configuration where organization_id=org and not demo_enabled) then raise exception 'Modo DEMO desativado.' using errcode='42501'; end if;
 if p_action is null or p_action not in ('execute_demo','reconcile_demo') then raise exception 'Operação fiscal real desabilitada.' using errcode='42501'; end if;
 select * into r from public.fiscal_requests where organization_id=org and id=p_request for update;
 if r.id is null or (r.requester_id<>auth.uid() and not private.has_permission('fiscal.audit')) or r.environment<>'demo' then
 raise exception 'Solicitação não autorizada.' using errcode='42501'; end if;
 -- O legado e os resultados finais são preservados; nunca são reexecutados.
 if r.policy_version is distinct from 2 or r.state in ('registered','authorized','rejected','blocked') then
 return jsonb_build_object('state',r.state,'busy',false,'legacy',r.policy_version is null,'automatic_issuance',false); end if;
 if r.api_scope<>'DEMO-ERP-UNICO' or r.source_store<>'DEMO-FLT-COMERCIO' or r.destination_store<>'DEMO-FLAGSHIP-FLATTER' then
 raise exception 'Escopo de lojas inválido.' using errcode='42501'; end if;
 if r.requester_expiry<=now() or not private.fiscal_actor_valid(r.requester_id,r.requester_session,org,'fiscal.create_request')
 or (r.approver_id is not null and not private.fiscal_actor_valid(r.approver_id,r.approver_session,org,'fiscal.approve_pending')) then
 update public.fiscal_requests set state='blocked',error_code='ACCESS_REVOKED',claim=null,lease_until=null,updated_at=now() where id=r.id;
 insert into public.fiscal_events(organization_id,request_id,actor_id,state,event_code) values(org,r.id,auth.uid(),'blocked','ACCESS_REVOKED');
 return jsonb_build_object('state','blocked'); end if;
 if r.state='processing' and r.lease_until<=now() then
 update public.fiscal_requests set state='needs_reconciliation',stage='unknown',error_code='EXECUTOR_INTERRUPTED',claim=null,updated_at=now() where id=r.id;
 insert into public.fiscal_events(organization_id,request_id,actor_id,state,event_code) values(org,r.id,auth.uid(),'needs_reconciliation','EXECUTOR_INTERRUPTED');
 return jsonb_build_object('state','needs_reconciliation'); end if;
 if r.state='processing' then return jsonb_build_object('state',r.state,'busy',true); end if;
 if not ((r.state='confirmed' and p_action='execute_demo') or (r.state='needs_reconciliation' and p_action='reconcile_demo')) then
 return jsonb_build_object('state',r.state,'busy',false); end if;
 concessao:=gen_random_uuid();
 update public.fiscal_requests set state='processing',stage=case when p_action='execute_demo' then 'registration_started' else 'reconciliation_read' end,
 claim=concessao,lease_until=now()+interval '60 seconds',updated_at=now() where id=r.id;
 insert into public.fiscal_events(organization_id,request_id,actor_id,state,event_code) values(org,r.id,auth.uid(),'processing',case when p_action='execute_demo' then 'demo_registration_started' else 'demo_reconciliation_read' end);
 return jsonb_build_object('id',r.id,'claim',concessao,'number',r.order_number,'state','processing','action',p_action);
end $$;

create or replace function private.fiscal_worker_finish(p_request uuid,p_claim uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare r public.fiscal_requests; estado text; codigo text; cenario text;
begin
 if auth.jwt()->>'role' is distinct from 'service_role' then raise exception 'Executor não autorizado.' using errcode='42501'; end if;
 select * into r from public.fiscal_requests where id=p_request for update;
 if r.id is null or r.environment<>'demo' or r.policy_version is distinct from 2 or r.state<>'processing'
 or p_claim is null or r.claim is distinct from p_claim or r.lease_until<=now()
 or r.api_scope<>'DEMO-ERP-UNICO' or r.source_store<>'DEMO-FLT-COMERCIO' or r.destination_store<>'DEMO-FLAGSHIP-FLATTER'
 or r.stage not in ('registration_started','reconciliation_read') then
 raise exception 'Concessão inválida ou expirada.' using errcode='42501'; end if;
 if exists(select 1 from public.fiscal_configuration where organization_id=r.organization_id and not demo_enabled)
 or r.requester_expiry<=now() or not private.fiscal_actor_valid(r.requester_id,r.requester_session,r.organization_id,'fiscal.create_request')
 or (r.approver_id is not null and not private.fiscal_actor_valid(r.approver_id,r.approver_session,r.organization_id,'fiscal.approve_pending')) then
 estado:='blocked'; codigo:='ACCESS_REVOKED';
 else
 cenario:=private.fiscal_demo_catalog()->'pedidos'->r.order_number->>'cenario';
 if r.stage='reconciliation_read' then estado:='registered';
 elsif cenario in ('timeout_criacao','timeout_apos_cadastro') then estado:='needs_reconciliation';
 codigo:=case when cenario='timeout_criacao' then 'DEMO_REGISTRATION_TIMEOUT' else 'DEMO_AFTER_REGISTRATION_TIMEOUT' end;
 elsif cenario='cadastro_recusado' then estado:='rejected'; codigo:='DEMO_REGISTRATION_REJECTED';
 else estado:='registered'; end if;
 end if;
 -- Não existe etapa de emissão. Número autorizado, chave e protocolo não são inventados.
 update public.fiscal_requests set state=estado,
 official_state=case when estado='registered' then 'Em aberto' end,error_code=codigo,
 external_id=case when estado in ('registered','needs_reconciliation') and codigo is distinct from 'DEMO_REGISTRATION_TIMEOUT'
 then coalesce(external_id,'DEMO-CADASTRO-'||id::text) else external_id end,
 stage=case when estado='needs_reconciliation' then 'unknown' else 'finished' end,
 claim=null,lease_until=null,updated_at=now() where id=r.id;
 insert into public.fiscal_events(organization_id,request_id,actor_id,actor_type,state,event_code)
 values(r.organization_id,r.id,null,'executor',estado,coalesce(codigo,'demo_'||estado));
 return jsonb_build_object('id',r.id,'state',estado,'environment','demo','real_enabled',false,
 'operation','register_only','automatic_issuance',false);
end $$;

create or replace function private.fiscal_settings() returns jsonb
language plpgsql stable security definer set search_path='' as $$
begin
 perform private.require_master();
 return jsonb_build_object('environment','demo','real_enabled',false,'policy_version',2,'operation','register_only','automatic_issuance',false,'api_mode','single','source_store_name','FLT Comércio','destination_store_name','Flagship - Flatter','executor','Supabase Edge Functions','documents_stored',false,'demo_enabled',coalesce((select demo_enabled from public.fiscal_configuration where organization_id=private.current_organization()),true));
end $$;

create or replace function private.fiscal_status() returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
 org:=private.require_permission('fiscal.view');
 return jsonb_build_object('environment','demo','real_enabled',false,'policy_version',2,'operation','register_only','automatic_issuance',false,'api_mode','single','source_store_name','FLT Comércio','destination_store_name','Flagship - Flatter','demo_enabled',coalesce((select demo_enabled from public.fiscal_configuration where organization_id=org),true));
end $$;

create or replace function private.fiscal_audit_list(p_offset integer default 0) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
 org:=private.require_permission('fiscal.audit');
 if p_offset is null or p_offset<0 or p_offset>5000 then raise exception 'Página inválida.' using errcode='22023'; end if;
 return coalesce((select jsonb_agg(jsonb_build_object('id',id,'order_number',order_number,'requester_id',requester_id,
 'approver_id',approver_id,'state',state,'environment',environment,'freight',freight,'taxpayer',taxpayer,'preview_version',preview_version,'policy_version',policy_version,'api_scope',api_scope,'source_store',source_store,'destination_store',destination_store,
 'created_at',created_at,'updated_at',updated_at) order by created_at desc,id)
 from (select * from public.fiscal_requests where organization_id=org order by created_at desc,id limit 25 offset p_offset) r),'[]'::jsonb);
end $$;

-- Contrato v2 SHA256: 918b42b3fdf584203d06e673e20c65883489cc4d770c5d3b4e3330c76508d121
create or replace function private.fiscal_demo_catalog() returns jsonb language sql immutable set search_path='' as $catalogo$ select '{"versao": 2, "ambiente": "demo", "origem": "DEMO-FLT-COMERCIO", "destino": "DEMO-FLAGSHIP-FLATTER", "modalidades_frete": {"0": "CIF: por conta do remetente", "1": "FOB: por conta do destinatário", "2": "Por conta de terceiros", "3": "Transporte próprio do remetente", "4": "Transporte próprio do destinatário", "9": "Sem ocorrência de transporte"}, "tipos_contribuinte": {"1": "Contribuinte do ICMS", "2": "Contribuinte isento de inscrição estadual", "9": "Não contribuinte do ICMS"}, "estados_fiscais": ["Em aberto", "Aprovada", "Reprovada", "Denegada", "Cancelada", "Enviando", "Corrigida"], "pedidos": {"1001": {"numero": "1001", "id": "DEMO-ATACADO-1001", "origem": "DEMO-FLT-COMERCIO", "destino": "DEMO-FLAGSHIP-FLATTER", "ambiente": "demo", "cliente": "Cliente DEMO 1001", "uf": "CE", "situacao": "Em separação", "cenario": "cadastrada", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Somente cadastro fictício. Emissão exclusivamente manual no ERP; tributação real não homologada."], "nota_existente": false, "api_scope": "DEMO-ERP-UNICO"}, "1002": {"numero": "1002", "id": "DEMO-ATACADO-1002", "origem": "DEMO-FLT-COMERCIO", "destino": "DEMO-FLAGSHIP-FLATTER", "ambiente": "demo", "cliente": "Cliente DEMO 1002", "uf": "Ceará", "situacao": "Concretizada", "cenario": "cadastro_recusado", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Somente cadastro fictício. Emissão exclusivamente manual no ERP; tributação real não homologada."], "nota_existente": false, "api_scope": "DEMO-ERP-UNICO"}, "1003": {"numero": "1003", "id": "DEMO-ATACADO-1003", "origem": "DEMO-FLT-COMERCIO", "destino": "DEMO-FLAGSHIP-FLATTER", "ambiente": "demo", "cliente": "Cliente DEMO 1003", "uf": "Ceara", "situacao": "Em andamento", "cenario": "cadastrada", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Somente cadastro fictício. Emissão exclusivamente manual no ERP; tributação real não homologada."], "nota_existente": false, "api_scope": "DEMO-ERP-UNICO"}, "1004": {"numero": "1004", "id": "DEMO-ATACADO-1004", "origem": "DEMO-FLT-COMERCIO", "destino": "DEMO-FLAGSHIP-FLATTER", "ambiente": "demo", "cliente": "Cliente DEMO 1004", "uf": "SP", "situacao": "Em separação", "cenario": "fora_uf", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Somente cadastro fictício. Emissão exclusivamente manual no ERP; tributação real não homologada."], "nota_existente": false, "api_scope": "DEMO-ERP-UNICO"}, "1005": {"numero": "1005", "id": "DEMO-ATACADO-1005", "origem": "DEMO-FLT-COMERCIO", "destino": "DEMO-FLAGSHIP-FLATTER", "ambiente": "demo", "cliente": "Cliente DEMO 1005", "uf": "CE", "situacao": "Cancelada", "cenario": "bloqueado", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Somente cadastro fictício. Emissão exclusivamente manual no ERP; tributação real não homologada."], "nota_existente": false, "api_scope": "DEMO-ERP-UNICO"}, "1006": {"numero": "1006", "id": "DEMO-ATACADO-1006", "origem": "DEMO-FLT-COMERCIO", "destino": "DEMO-FLAGSHIP-FLATTER", "ambiente": "demo", "cliente": "Cliente DEMO 1006", "uf": "CE", "situacao": "Concretizada", "cenario": "existente", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Somente cadastro fictício. Emissão exclusivamente manual no ERP; tributação real não homologada."], "nota_existente": true, "api_scope": "DEMO-ERP-UNICO"}, "1007": {"numero": "1007", "id": "DEMO-ATACADO-1007", "origem": "DEMO-FLT-COMERCIO", "destino": "DEMO-FLAGSHIP-FLATTER", "ambiente": "demo", "cliente": "Cliente DEMO 1007", "uf": "CE", "situacao": "Em separação", "cenario": "timeout_criacao", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Somente cadastro fictício. Emissão exclusivamente manual no ERP; tributação real não homologada."], "nota_existente": false, "api_scope": "DEMO-ERP-UNICO"}, "1008": {"numero": "1008", "id": "DEMO-ATACADO-1008", "origem": "DEMO-FLT-COMERCIO", "destino": "DEMO-FLAGSHIP-FLATTER", "ambiente": "demo", "cliente": "Cliente DEMO 1008", "uf": "CE", "situacao": "Em separação", "cenario": "timeout_apos_cadastro", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Somente cadastro fictício. Emissão exclusivamente manual no ERP; tributação real não homologada."], "nota_existente": false, "api_scope": "DEMO-ERP-UNICO"}, "1009": {"numero": "1009", "id": "DEMO-ATACADO-1009", "origem": "DEMO-FLT-COMERCIO", "destino": "DEMO-FLAGSHIP-FLATTER", "ambiente": "demo", "cliente": "Cliente DEMO 1009", "uf": "CE", "situacao": "Em separação", "cenario": "incompleto", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Somente cadastro fictício. Emissão exclusivamente manual no ERP; tributação real não homologada.", "Cadastro obrigatório incompleto no cenário DEMO."], "nota_existente": false, "api_scope": "DEMO-ERP-UNICO"}, "1010": {"numero": "1010", "id": "DEMO-ATACADO-1010", "origem": "DEMO-FLT-COMERCIO", "destino": "DEMO-FLAGSHIP-FLATTER", "ambiente": "demo", "cliente": "Cliente DEMO 1010", "uf": "CE", "situacao": "Concretizada", "cenario": "denegada", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Somente cadastro fictício. Emissão exclusivamente manual no ERP; tributação real não homologada."], "nota_existente": true, "api_scope": "DEMO-ERP-UNICO"}}, "api_unica": true, "api_scope": "DEMO-ERP-UNICO", "loja_origem_nome": "FLT Comércio", "loja_destino_nome": "Flagship - Flatter", "operacao": "cadastro_nfe_sem_emissao", "emissao_automatica": false}'::jsonb $catalogo$;

notify pgrst,'reload schema';
