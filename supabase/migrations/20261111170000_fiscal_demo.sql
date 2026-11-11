-- Módulo fiscal preparatório. Somente DEMO, sem endpoints ou credenciais do ERP.
insert into public.permissions(name) values ('fiscal.view'),('fiscal.create_request'),
 ('fiscal.approve_pending'),('fiscal.audit'),('fiscal.manage') on conflict do nothing;

create table public.fiscal_configuration (
 organization_id uuid primary key references public.organizations(id),
 demo_enabled boolean not null default true,
 updated_by uuid not null references auth.users(id), updated_at timestamptz not null default now()
);
alter table public.fiscal_configuration enable row level security;
revoke all on public.fiscal_configuration from public,anon,authenticated,service_role;
create table public.fiscal_previews (
 id uuid primary key default gen_random_uuid(),
 organization_id uuid not null references public.organizations(id),
 author_id uuid not null references auth.users(id),
 order_number text not null, order_id text not null,
 source_account text not null check(source_account='DEMO-ATACADO'),
 destination_account text not null check(destination_account='DEMO-VAREJO'),
 environment text not null default 'demo' check(environment='demo'),
 freight integer not null check(freight in (0,1,2,3,4,9)),
 taxpayer integer not null check(taxpayer in (1,2,9)),
 version integer not null default 1 check(version=1), fingerprint text not null,
 created_at timestamptz not null default now(), expires_at timestamptz not null default now()+interval '15 minutes',
 unique(organization_id,id), unique(organization_id,author_id,fingerprint,created_at)
);
create index fiscal_preview_owner on public.fiscal_previews(organization_id,author_id,created_at desc);
create table public.fiscal_requests (
 id uuid primary key default gen_random_uuid(),
 organization_id uuid not null references public.organizations(id),
 preview_id uuid not null, requester_id uuid not null references auth.users(id),
 requester_session uuid not null, requester_expiry timestamptz not null,
 approver_id uuid references auth.users(id), approver_session uuid, approved_at timestamptz,
 order_number text not null, order_id text not null,
 source_account text not null check(source_account='DEMO-ATACADO'),
 destination_account text not null check(destination_account='DEMO-VAREJO'),
 environment text not null check(environment='demo'),
 freight integer not null check(freight in (0,1,2,3,4,9)), taxpayer integer not null check(taxpayer in (1,2,9)),
 preview_version integer not null check(preview_version=1), fingerprint text not null,
 state text not null check(state in ('awaiting_approval','confirmed','processing','authorized','rejected','needs_reconciliation','blocked')),
 stage text not null default 'confirmed', official_state text check(official_state in ('Em aberto','Aprovada','Reprovada','Denegada','Cancelada','Enviando','Corrigida')),
 external_id text check(length(external_id)<=120), invoice_number text check(length(invoice_number)<=120), invoice_key text check(length(invoice_key)<=120),
 error_code text check(length(error_code)<=80), claim uuid, lease_until timestamptz,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 foreign key(organization_id,preview_id) references public.fiscal_previews(organization_id,id),
 unique(organization_id,source_account,order_id,destination_account,environment), unique(organization_id,id)
);
create index fiscal_requests_owner on public.fiscal_requests(organization_id,requester_id,created_at desc);
create table public.fiscal_events (
 id bigint generated always as identity primary key,
 organization_id uuid not null, request_id uuid not null, actor_id uuid references auth.users(id), actor_type text not null default 'user' check(actor_type in ('user','executor')),
 state text not null, event_code text not null check(length(event_code)<=80), created_at timestamptz not null default now(),
 foreign key(organization_id,request_id) references public.fiscal_requests(organization_id,id)
);
create index fiscal_events_request on public.fiscal_events(organization_id,request_id,id);
alter table public.fiscal_previews enable row level security;
alter table public.fiscal_requests enable row level security;
alter table public.fiscal_events enable row level security;
revoke all on public.fiscal_previews,public.fiscal_requests,public.fiscal_events from public,anon,authenticated,service_role;
create policy fiscal_request_scope on public.fiscal_requests for select to authenticated using(
 organization_id=private.current_organization() and private.has_permission('fiscal.view')
 and (requester_id=auth.uid() or private.has_permission('fiscal.audit')));

-- Definição do catálogo DEMO gerada a partir do contrato compartilhado abaixo.
-- Nenhuma informação fiscal pessoal, documento, XML, PDF ou token é persistido.

create function private.fiscal_actor_has_permission(p_user uuid,p_org uuid,p_permission text) returns boolean
language sql stable security definer set search_path='' as $$
 select exists(select 1 from public.app_users u where u.user_id=p_user and u.organization_id=p_org and u.active
 and (u.role='Master' or coalesce((select allowed from public.user_permissions where user_id=p_user and permission=p_permission),
 exists(select 1 from public.role_permissions where role=u.role and permission=p_permission))))
$$;

create function private.fiscal_actor_valid(p_user uuid,p_session uuid,p_org uuid,p_permission text) returns boolean
language sql stable security definer set search_path='' as $$
 select exists(select 1 from public.app_users u join auth.sessions s on s.user_id=u.user_id
 left join private.account_access_controls g on g.user_id=u.user_id
 where u.user_id=p_user and u.organization_id=p_org and u.active and s.id=p_session
 and (s.not_after is null or s.not_after>now()) and not coalesce(g.pending,false)
 and (g.revoked_before is null or s.created_at>g.revoked_before)
 and private.fiscal_actor_has_permission(p_user,p_org,p_permission)
 and private.fiscal_actor_has_permission(p_user,p_org,'fiscal.view'))
$$;

create function private.fiscal_order(p_number text) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid; pedido jsonb; existente jsonb;
begin
 org:=private.require_permission('fiscal.view');
 if exists(select 1 from public.fiscal_configuration where organization_id=org and not demo_enabled) then raise exception 'Modo DEMO desativado pelo Master.' using errcode='42501'; end if;
 if p_number is null or p_number !~ '^[0-9]{1,20}$' then raise exception 'Número de pedido inválido.' using errcode='22023'; end if;
 pedido:=private.fiscal_demo_catalog()->'pedidos'->p_number;
 if pedido is null then raise exception 'Pedido DEMO não encontrado. Use os cenários informados na tela.' using errcode='22023'; end if;
 select jsonb_build_object('state',state,'environment',environment) into existente from public.fiscal_requests
 where organization_id=org and order_id=pedido->>'id';
 return pedido||jsonb_build_object('solicitacao_existente',existente,'real_enabled',false);
end $$;

create function private.fiscal_preview(p_number text,p_choices jsonb) returns jsonb
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
 and expires_at>now() order by created_at desc limit 1;
 if anterior.id is not null then return jsonb_build_object('id',anterior.id,'version',anterior.version,'fingerprint',impressao,'choices',p_choices,'order',pedido); end if;
 if (select count(*) from public.fiscal_previews where organization_id=org and author_id=auth.uid() and created_at>now()-interval '1 minute')>=10
 or (select count(*) from public.fiscal_previews where organization_id=org)>=10000 then
 raise exception 'Limite de prévias atingido.' using errcode='54000'; end if;
 insert into public.fiscal_previews(organization_id,author_id,order_number,order_id,source_account,destination_account,freight,taxpayer,fingerprint)
 values(org,auth.uid(),p_number,pedido->>'id','DEMO-ATACADO','DEMO-VAREJO',frete::integer,contribuinte::integer,impressao) returning id into resultado;
 return jsonb_build_object('id',resultado,'version',1,'fingerprint',impressao,'choices',p_choices,'order',pedido);
end $$;

create function private.fiscal_confirm(p_preview uuid,p_confirm boolean,p_approve_pending boolean default false) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; p public.fiscal_previews; pedido jsonb; existente public.fiscal_requests; resultado uuid; estado text; aprova boolean;
begin
 org:=private.require_permission('fiscal.create_request'); perform private.require_permission('fiscal.view');
 if p_confirm is distinct from true or p_approve_pending is null then raise exception 'Confirme conscientemente a simulação.' using errcode='22023'; end if;
 select * into p from public.fiscal_previews where id=p_preview and organization_id=org and author_id=auth.uid();
 if p.id is null or p.expires_at<=now() then raise exception 'Prévia ausente, expirada ou não autorizada.' using errcode='42501'; end if;
 perform pg_advisory_xact_lock(hashtextextended('fiscal:'||org::text||p.order_id,0));
 select * into existente from public.fiscal_requests where organization_id=org and source_account=p.source_account
 and order_id=p.order_id and destination_account=p.destination_account and environment=p.environment;
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
 order_number,order_id,source_account,destination_account,environment,freight,taxpayer,preview_version,fingerprint,state)
 values(org,p.id,auth.uid(),(auth.jwt()->>'session_id')::uuid,to_timestamp((auth.jwt()->>'exp')::numeric),
 case when aprova then auth.uid() end,case when aprova then (auth.jwt()->>'session_id')::uuid end,case when aprova then now() end,
 p.order_number,p.order_id,p.source_account,p.destination_account,p.environment,p.freight,p.taxpayer,p.version,p.fingerprint,estado) returning id into resultado;
 insert into public.fiscal_events(organization_id,request_id,actor_id,state,event_code) values(org,resultado,auth.uid(),estado,'demo_confirmed');
 return jsonb_build_object('id',resultado,'state',estado,'duplicate',false);
end $$;

create function private.fiscal_approve(p_request uuid,p_confirm boolean) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; r public.fiscal_requests;
begin
 org:=private.require_permission('fiscal.approve_pending'); perform private.require_permission('fiscal.view');
 if p_confirm is distinct from true then raise exception 'Confirme a autorização.' using errcode='22023'; end if;
 select * into r from public.fiscal_requests where id=p_request and organization_id=org for update;
 if r.id is null or r.state<>'awaiting_approval' or r.requester_expiry<=now()
 or not private.fiscal_actor_valid(r.requester_id,r.requester_session,org,'fiscal.create_request') then
 raise exception 'Solicitação não autorizável.' using errcode='42501'; end if;
 update public.fiscal_requests set state='confirmed',approver_id=auth.uid(),approver_session=(auth.jwt()->>'session_id')::uuid,approved_at=now(),updated_at=now() where id=r.id;
 insert into public.fiscal_events(organization_id,request_id,actor_id,state,event_code) values(org,r.id,auth.uid(),'confirmed','pending_order_approved');
 return jsonb_build_object('id',r.id,'state','confirmed');
end $$;

create function private.fiscal_list(p_offset integer default 0) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
 org:=private.require_permission('fiscal.view');
 if p_offset is null or p_offset<0 or p_offset>5000 then raise exception 'Página inválida.' using errcode='22023'; end if;
 return coalesce((select jsonb_agg(to_jsonb(r)-array['organization_id','requester_session','requester_expiry','approver_session','claim','lease_until','fingerprint'] order by r.created_at desc,r.id)
 from (select * from public.fiscal_requests where organization_id=org and
 (requester_id=auth.uid() or private.has_permission('fiscal.audit') or (state='awaiting_approval' and private.has_permission('fiscal.approve_pending')))
 order by created_at desc,id limit 25 offset p_offset) r),'[]'::jsonb);
end $$;

create function private.fiscal_history(p_request uuid) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
 org:=private.require_permission('fiscal.audit');
 if not exists(select 1 from public.fiscal_requests where id=p_request and organization_id=org) then raise exception 'Solicitação não autorizada.' using errcode='42501'; end if;
 return coalesce((select jsonb_agg(to_jsonb(e)-'organization_id' order by id) from public.fiscal_events e where organization_id=org and request_id=p_request),'[]'::jsonb);
end $$;

-- O início exige JWT individual. A conclusão só é concedida ao executor servidor.
create function private.fiscal_begin(p_request uuid,p_action text default 'execute_demo') returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid; r public.fiscal_requests; concessao uuid;
begin
 org:=private.require_permission('fiscal.create_request'); perform private.require_permission('fiscal.view');
 if exists(select 1 from public.fiscal_configuration where organization_id=org and not demo_enabled) then raise exception 'Modo DEMO desativado.' using errcode='42501'; end if;
 if p_action is null or p_action not in ('execute_demo','reconcile_demo') then raise exception 'Operação fiscal real desabilitada.' using errcode='42501'; end if;
 select * into r from public.fiscal_requests where organization_id=org and id=p_request for update;
 if r.id is null or (r.requester_id<>auth.uid() and not private.has_permission('fiscal.audit')) or r.environment<>'demo' then
 raise exception 'Solicitação não autorizada.' using errcode='42501'; end if;
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
 update public.fiscal_requests set state='processing',stage=case when p_action='execute_demo' then 'creation_started' else 'reconciliation_read' end,
 claim=concessao,lease_until=now()+interval '60 seconds',updated_at=now() where id=r.id;
 insert into public.fiscal_events(organization_id,request_id,actor_id,state,event_code) values(org,r.id,auth.uid(),'processing',case when p_action='execute_demo' then 'demo_creation_started' else 'demo_reconciliation_read' end);
 return jsonb_build_object('id',r.id,'claim',concessao,'number',r.order_number,'state','processing','action',p_action);
end $$;

create function private.fiscal_worker_finish(p_request uuid,p_claim uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare r public.fiscal_requests; estado text; oficial text; codigo text; cenario text;
begin
 if auth.jwt()->>'role' is distinct from 'service_role' then raise exception 'Executor não autorizado.' using errcode='42501'; end if;
 select * into r from public.fiscal_requests where id=p_request for update;
 if r.id is null or r.environment<>'demo' or r.state<>'processing' or p_claim is null or r.claim is distinct from p_claim or r.lease_until<=now() then
 raise exception 'Concessão inválida ou expirada.' using errcode='42501'; end if;
 if exists(select 1 from public.fiscal_configuration where organization_id=r.organization_id and not demo_enabled) or r.requester_expiry<=now() or not private.fiscal_actor_valid(r.requester_id,r.requester_session,r.organization_id,'fiscal.create_request')
 or (r.approver_id is not null and not private.fiscal_actor_valid(r.approver_id,r.approver_session,r.organization_id,'fiscal.approve_pending')) then
 estado:='blocked'; codigo:='ACCESS_REVOKED';
 else
 cenario:=private.fiscal_demo_catalog()->'pedidos'->r.order_number->>'cenario';
 if r.stage='reconciliation_read' then estado:='authorized'; oficial:='Aprovada'; codigo:=null;
 elsif cenario in ('timeout_criacao','timeout_emissao') then estado:='needs_reconciliation'; codigo:=case when cenario='timeout_criacao' then 'DEMO_CREATION_TIMEOUT' else 'DEMO_EMISSION_TIMEOUT' end;
 elsif cenario='rejeitada' then estado:='rejected'; oficial:='Reprovada'; else estado:='authorized'; oficial:='Aprovada'; end if;
 end if;
 update public.fiscal_requests set state=estado,official_state=oficial,error_code=codigo,
 external_id=case when codigo='DEMO_CREATION_TIMEOUT' or estado='blocked' then external_id else coalesce(external_id,'DEMO-NFE-'||id::text) end,
 invoice_number=case when estado in ('authorized','rejected') then 'DEMO-'||order_number else invoice_number end,
 invoice_key=case when estado='authorized' then 'DEMO-'||replace(id::text,'-','') else invoice_key end,
 stage=case when estado='needs_reconciliation' then 'unknown' else 'finished' end,claim=null,lease_until=null,updated_at=now() where id=r.id;
 insert into public.fiscal_events(organization_id,request_id,actor_id,actor_type,state,event_code) values(r.organization_id,r.id,null,'executor',estado,coalesce(codigo,'demo_'||estado));
 return jsonb_build_object('id',r.id,'state',estado,'environment','demo','real_enabled',false);
end $$;

create function private.fiscal_settings() returns jsonb
language plpgsql stable security definer set search_path='' as $$
begin
 perform private.require_master();
 return jsonb_build_object('environment','demo','real_enabled',false,'executor','Supabase Edge Functions','documents_stored',false,'demo_enabled',coalesce((select demo_enabled from public.fiscal_configuration where organization_id=private.current_organization()),true));
end $$;

-- Contrato compartilhado SHA256: 400ab19bf26309a7f24798da21dc09e4170acab57af8460a508247625e72f5b9
create function private.fiscal_demo_catalog() returns jsonb language sql immutable set search_path='' as $catalogo$ select '{"versao": 1, "ambiente": "demo", "origem": "DEMO-ATACADO", "destino": "DEMO-VAREJO", "modalidades_frete": {"0": "CIF: por conta do remetente", "1": "FOB: por conta do destinatário", "2": "Por conta de terceiros", "3": "Transporte próprio do remetente", "4": "Transporte próprio do destinatário", "9": "Sem ocorrência de transporte"}, "tipos_contribuinte": {"1": "Contribuinte do ICMS", "2": "Contribuinte isento de inscrição estadual", "9": "Não contribuinte do ICMS"}, "estados_fiscais": ["Em aberto", "Aprovada", "Reprovada", "Denegada", "Cancelada", "Enviando", "Corrigida"], "pedidos": {"1001": {"numero": "1001", "id": "DEMO-ATACADO-1001", "origem": "DEMO-ATACADO", "destino": "DEMO-VAREJO", "ambiente": "demo", "cliente": "Cliente DEMO 1001", "uf": "CE", "situacao": "Em separação", "cenario": "autorizada", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Cadastro e tributação fictícios; não representam homologação fiscal real."], "nota_existente": false}, "1002": {"numero": "1002", "id": "DEMO-ATACADO-1002", "origem": "DEMO-ATACADO", "destino": "DEMO-VAREJO", "ambiente": "demo", "cliente": "Cliente DEMO 1002", "uf": "Ceará", "situacao": "Concretizada", "cenario": "rejeitada", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Cadastro e tributação fictícios; não representam homologação fiscal real."], "nota_existente": false}, "1003": {"numero": "1003", "id": "DEMO-ATACADO-1003", "origem": "DEMO-ATACADO", "destino": "DEMO-VAREJO", "ambiente": "demo", "cliente": "Cliente DEMO 1003", "uf": "Ceara", "situacao": "Em andamento", "cenario": "autorizada", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Cadastro e tributação fictícios; não representam homologação fiscal real."], "nota_existente": false}, "1004": {"numero": "1004", "id": "DEMO-ATACADO-1004", "origem": "DEMO-ATACADO", "destino": "DEMO-VAREJO", "ambiente": "demo", "cliente": "Cliente DEMO 1004", "uf": "SP", "situacao": "Em separação", "cenario": "fora_uf", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Cadastro e tributação fictícios; não representam homologação fiscal real."], "nota_existente": false}, "1005": {"numero": "1005", "id": "DEMO-ATACADO-1005", "origem": "DEMO-ATACADO", "destino": "DEMO-VAREJO", "ambiente": "demo", "cliente": "Cliente DEMO 1005", "uf": "CE", "situacao": "Cancelada", "cenario": "bloqueado", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Cadastro e tributação fictícios; não representam homologação fiscal real."], "nota_existente": false}, "1006": {"numero": "1006", "id": "DEMO-ATACADO-1006", "origem": "DEMO-ATACADO", "destino": "DEMO-VAREJO", "ambiente": "demo", "cliente": "Cliente DEMO 1006", "uf": "CE", "situacao": "Concretizada", "cenario": "existente", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Cadastro e tributação fictícios; não representam homologação fiscal real."], "nota_existente": true}, "1007": {"numero": "1007", "id": "DEMO-ATACADO-1007", "origem": "DEMO-ATACADO", "destino": "DEMO-VAREJO", "ambiente": "demo", "cliente": "Cliente DEMO 1007", "uf": "CE", "situacao": "Em separação", "cenario": "timeout_criacao", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Cadastro e tributação fictícios; não representam homologação fiscal real."], "nota_existente": false}, "1008": {"numero": "1008", "id": "DEMO-ATACADO-1008", "origem": "DEMO-ATACADO", "destino": "DEMO-VAREJO", "ambiente": "demo", "cliente": "Cliente DEMO 1008", "uf": "CE", "situacao": "Em separação", "cenario": "timeout_emissao", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Cadastro e tributação fictícios; não representam homologação fiscal real."], "nota_existente": false}, "1009": {"numero": "1009", "id": "DEMO-ATACADO-1009", "origem": "DEMO-ATACADO", "destino": "DEMO-VAREJO", "ambiente": "demo", "cliente": "Cliente DEMO 1009", "uf": "CE", "situacao": "Em separação", "cenario": "incompleto", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Cadastro e tributação fictícios; não representam homologação fiscal real.", "Cadastro obrigatório incompleto no cenário DEMO."], "nota_existente": false}, "1010": {"numero": "1010", "id": "DEMO-ATACADO-1010", "origem": "DEMO-ATACADO", "destino": "DEMO-VAREJO", "ambiente": "demo", "cliente": "Cliente DEMO 1010", "uf": "CE", "situacao": "Concretizada", "cenario": "denegada", "itens": [{"sku": "DEMO-01", "produto": "Produto DEMO", "quantidade": 2, "valor_unitario": 50, "total": 100}], "valor_total": 100, "pendencias": ["Cadastro e tributação fictícios; não representam homologação fiscal real."], "nota_existente": true}}}'::jsonb $catalogo$;
grant usage on schema private to service_role;
notify pgrst,'reload schema';

create function private.fiscal_preview_get(p_preview uuid) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid; p public.fiscal_previews;
begin
 org:=private.require_permission('fiscal.create_request');
 select * into p from public.fiscal_previews where id=p_preview and organization_id=org and author_id=auth.uid() and expires_at>now();
 if p.id is null then raise exception 'Prévia ausente, expirada ou não autorizada.' using errcode='42501'; end if;
 return jsonb_build_object('id',p.id,'version',p.version,'fingerprint',p.fingerprint,
 'choices',jsonb_build_object('modalidade_frete',p.freight,'tipo_contribuinte',p.taxpayer),'order',private.fiscal_order(p.order_number));
end $$;
notify pgrst,'reload schema';

revoke all on sequence public.fiscal_events_id_seq from public,anon,authenticated,service_role;

create function private.fiscal_configure(p_demo_enabled boolean) returns jsonb
language plpgsql security definer set search_path='' as $$
declare org uuid;
begin
 org:=private.require_master();
 if p_demo_enabled is null then raise exception 'Configuração inválida.' using errcode='22023'; end if;
 insert into public.fiscal_configuration(organization_id,demo_enabled,updated_by) values(org,p_demo_enabled,auth.uid())
 on conflict(organization_id) do update set demo_enabled=excluded.demo_enabled,updated_by=auth.uid(),updated_at=now();
 insert into public.audit_events(organization_id,author_id,action,target,details) values(org,auth.uid(),'fiscal.manage','demo',jsonb_build_object('demo_enabled',p_demo_enabled));
 return private.fiscal_settings();
end $$;
create function private.fiscal_status() returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
 org:=private.require_permission('fiscal.view');
 return jsonb_build_object('environment','demo','real_enabled',false,'demo_enabled',coalesce((select demo_enabled from public.fiscal_configuration where organization_id=org),true));
end $$;
revoke all on function private.fiscal_actor_has_permission(uuid,uuid,text) from public,anon,authenticated,service_role;
revoke all on function private.fiscal_actor_valid(uuid,uuid,uuid,text) from public,anon,authenticated,service_role;
revoke all on function private.fiscal_order(text) from public,anon,authenticated,service_role;
create function public.fiscal_order(p_number text) returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_order(p_number) $$;
revoke all on function public.fiscal_order(text) from public,anon,authenticated,service_role;
grant execute on function private.fiscal_order(text),public.fiscal_order(text) to authenticated;
revoke all on function private.fiscal_preview(text,jsonb) from public,anon,authenticated,service_role;
create function public.fiscal_preview(p_number text,p_choices jsonb) returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_preview(p_number,p_choices) $$;
revoke all on function public.fiscal_preview(text,jsonb) from public,anon,authenticated,service_role;
grant execute on function private.fiscal_preview(text,jsonb),public.fiscal_preview(text,jsonb) to authenticated;
revoke all on function private.fiscal_confirm(uuid,boolean,boolean) from public,anon,authenticated,service_role;
create function public.fiscal_confirm(p_preview uuid,p_confirm boolean,p_approve_pending boolean default false) returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_confirm(p_preview,p_confirm,p_approve_pending) $$;
revoke all on function public.fiscal_confirm(uuid,boolean,boolean) from public,anon,authenticated,service_role;
grant execute on function private.fiscal_confirm(uuid,boolean,boolean),public.fiscal_confirm(uuid,boolean,boolean) to authenticated;
revoke all on function private.fiscal_approve(uuid,boolean) from public,anon,authenticated,service_role;
create function public.fiscal_approve(p_request uuid,p_confirm boolean) returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_approve(p_request,p_confirm) $$;
revoke all on function public.fiscal_approve(uuid,boolean) from public,anon,authenticated,service_role;
grant execute on function private.fiscal_approve(uuid,boolean),public.fiscal_approve(uuid,boolean) to authenticated;
revoke all on function private.fiscal_list(integer) from public,anon,authenticated,service_role;
create function public.fiscal_list(p_offset integer default 0) returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_list(p_offset) $$;
revoke all on function public.fiscal_list(integer) from public,anon,authenticated,service_role;
grant execute on function private.fiscal_list(integer),public.fiscal_list(integer) to authenticated;
revoke all on function private.fiscal_history(uuid) from public,anon,authenticated,service_role;
create function public.fiscal_history(p_request uuid) returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_history(p_request) $$;
revoke all on function public.fiscal_history(uuid) from public,anon,authenticated,service_role;
grant execute on function private.fiscal_history(uuid),public.fiscal_history(uuid) to authenticated;
revoke all on function private.fiscal_begin(uuid,text) from public,anon,authenticated,service_role;
create function public.fiscal_begin(p_request uuid,p_action text default 'execute_demo') returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_begin(p_request,p_action) $$;
revoke all on function public.fiscal_begin(uuid,text) from public,anon,authenticated,service_role;
grant execute on function private.fiscal_begin(uuid,text),public.fiscal_begin(uuid,text) to authenticated;
revoke all on function private.fiscal_worker_finish(uuid,uuid) from public,anon,authenticated,service_role;
create function public.fiscal_worker_finish(p_request uuid,p_claim uuid) returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_worker_finish(p_request,p_claim) $$;
revoke all on function public.fiscal_worker_finish(uuid,uuid) from public,anon,authenticated,service_role;
grant execute on function private.fiscal_worker_finish(uuid,uuid),public.fiscal_worker_finish(uuid,uuid) to service_role;
revoke all on function private.fiscal_settings() from public,anon,authenticated,service_role;
create function public.fiscal_settings() returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_settings() $$;
revoke all on function public.fiscal_settings() from public,anon,authenticated,service_role;
grant execute on function private.fiscal_settings(),public.fiscal_settings() to authenticated;
revoke all on function private.fiscal_demo_catalog() from public,anon,authenticated,service_role;
revoke all on function private.fiscal_preview_get(uuid) from public,anon,authenticated,service_role;
create function public.fiscal_preview_get(p_preview uuid) returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_preview_get(p_preview) $$;
revoke all on function public.fiscal_preview_get(uuid) from public,anon,authenticated,service_role;
grant execute on function private.fiscal_preview_get(uuid),public.fiscal_preview_get(uuid) to authenticated;
revoke all on function private.fiscal_configure(boolean) from public,anon,authenticated,service_role;
create function public.fiscal_configure(p_demo_enabled boolean) returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_configure(p_demo_enabled) $$;
revoke all on function public.fiscal_configure(boolean) from public,anon,authenticated,service_role;
grant execute on function private.fiscal_configure(boolean),public.fiscal_configure(boolean) to authenticated;
revoke all on function private.fiscal_status() from public,anon,authenticated,service_role;
create function public.fiscal_status() returns jsonb language sql security invoker set search_path='' as $$ select private.fiscal_status() $$;
revoke all on function public.fiscal_status() from public,anon,authenticated,service_role;
grant execute on function private.fiscal_status(),public.fiscal_status() to authenticated;
notify pgrst,'reload schema';

create function private.fiscal_audit_list(p_offset integer default 0) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare org uuid;
begin
 org:=private.require_permission('fiscal.audit');
 if p_offset is null or p_offset<0 or p_offset>5000 then raise exception 'Página inválida.' using errcode='22023'; end if;
 return coalesce((select jsonb_agg(jsonb_build_object('id',id,'order_number',order_number,'requester_id',requester_id,
 'approver_id',approver_id,'state',state,'environment',environment,'freight',freight,'taxpayer',taxpayer,'preview_version',preview_version,
 'created_at',created_at,'updated_at',updated_at) order by created_at desc,id)
 from (select * from public.fiscal_requests where organization_id=org order by created_at desc,id limit 25 offset p_offset) r),'[]'::jsonb);
end $$;
revoke all on function private.fiscal_audit_list(integer) from public,anon,authenticated,service_role;
create function public.fiscal_audit_list(p_offset integer default 0) returns jsonb language sql stable security invoker set search_path='' as $$select private.fiscal_audit_list(p_offset)$$;
revoke all on function public.fiscal_audit_list(integer) from public,anon,authenticated,service_role;
grant execute on function private.fiscal_audit_list(integer),public.fiscal_audit_list(integer) to authenticated;

create or replace function private.admin_set_user(p_user_id uuid,p_display_name text,p_role text,p_active boolean,p_permissions jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare org uuid; previous public.app_users; entry record;
begin
  org := private.require_master();
  perform pg_advisory_xact_lock(hashtextextended('users:'||org::text,0));
  if p_user_id is null or p_display_name is null or length(btrim(p_display_name)) not between 1 and 120 or p_active is null
    or p_role is null or p_role not in ('Administrador','Operador','Visualizador')
    or jsonb_typeof(p_permissions) is distinct from 'object' then raise exception 'Perfil ou permissões inválidos.' using errcode='22023'; end if;
  perform pg_advisory_xact_lock(hashtextextended('user:'||p_user_id::text,0));
  select * into previous from public.app_users where user_id=p_user_id for update;
  if previous.role='Master' or (previous.organization_id is not null and previous.organization_id<>org)
    or not exists(select 1 from auth.users where id=p_user_id) then raise exception 'Conta não gerenciável.' using errcode='42501'; end if;
  for entry in select * from jsonb_each(p_permissions) loop
    if jsonb_typeof(entry.value) <> 'boolean' or not exists(select 1 from public.permissions where name=entry.key)
      or entry.key in ('users.manage','permissions.manage','reports.manage','integrations.manage','commercial.manage','fiscal.manage') then
      raise exception 'Permissão não delegável ou inválida.' using errcode='22023';
    end if;
  end loop;
  insert into public.app_users(user_id,organization_id,display_name,role,active)
    values(p_user_id,org,btrim(p_display_name),p_role,p_active)
    on conflict(user_id) do update set display_name=excluded.display_name,role=excluded.role,active=excluded.active,updated_at=now();
  delete from public.user_permissions where user_id=p_user_id;
  insert into public.user_permissions(user_id,permission,allowed)
    select p_user_id,key,(value::text)::boolean from jsonb_each(p_permissions);
  insert into public.audit_events(organization_id,author_id,action,target,details)
    values(org,auth.uid(),'users.manage',p_user_id::text,jsonb_build_object('role',p_role,'active',p_active,'permissions',p_permissions));
  return jsonb_build_object('updated',true);
end $$;
notify pgrst,'reload schema';
