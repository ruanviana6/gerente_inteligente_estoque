-- Exclusivo do PostgreSQL descartável, com dados fictícios e rollback integral.
\set ON_ERROR_STOP on
begin;
do $$begin
 if (select count(*) from information_schema.columns where table_schema='auth' and table_name='users')<>5 then
 raise exception 'Exige Auth descartável.'; end if;
end $$;
create schema fiscal_test;
grant usage on schema fiscal_test to authenticated,anon,service_role;
create function fiscal_test.check(ok boolean,label text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'FALHOU FISCAL: %',label; end if;
raise notice 'APROVADO FISCAL: %',label; end $$;
create function fiscal_test.denied(statement text,state text,label text) returns void language plpgsql as $$
declare actual text;
begin begin execute statement; exception when others then get stacked diagnostics actual=returned_sqlstate;
 if actual=state then raise notice 'APROVADO FISCAL: %',label; return; end if;
 raise exception 'FALHOU FISCAL: % esperado %, recebido %',label,state,actual;
end; raise exception 'FALHOU FISCAL: % permitiu operação',label; end $$;
create function fiscal_test.login(i int,ttl int default 3600) returns void language plpgsql as $$
begin perform set_config('request.jwt.claims',jsonb_build_object('sub','d1000000-0000-4000-8000-'||lpad(i::text,12,'0'),
 'session_id','d2000000-0000-4000-8000-'||lpad(i::text,12,'0'),'role','authenticated','exp',extract(epoch from now())+ttl)::text,true); end $$;
create function fiscal_test.request(n text) returns jsonb language sql as $$
select public.fiscal_confirm((public.fiscal_preview(n,'{"modalidade_frete":1,"tipo_contribuinte":9}')->>'id')::uuid,true,false) $$;
insert into auth.users(id,email,email_confirmed_at)
select ('d1000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,'fiscal'||i||'@example.invalid',now() from generate_series(1,6) i;
insert into auth.sessions(id,user_id,not_after)
select ('d2000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
 ('d1000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,now()+interval '1 day' from generate_series(1,6) i;
insert into public.organizations(id,name) values('d3000000-0000-4000-8000-000000000001','Fiscal fictício1'),('d3000000-0000-4000-8000-000000000002','Fiscal fictício2');
insert into public.app_users(user_id,organization_id,display_name,role,active)
select ('d1000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
 case when i=2 then 'd3000000-0000-4000-8000-000000000002'::uuid else 'd3000000-0000-4000-8000-000000000001'::uuid end,
 'Pessoa fiscal fictícia'||i,case when i in(1,2) then 'Master' else 'Operador' end,i<>6 from generate_series(1,6) i;
insert into public.user_permissions(user_id,permission,allowed)
select ('d1000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,p,true
from generate_series(3,5) i cross join unnest(array['fiscal.view','fiscal.create_request']) p;
insert into public.user_permissions(user_id,permission,allowed) values
 ('d1000000-0000-4000-8000-000000000004','fiscal.approve_pending',true),
 ('d1000000-0000-4000-8000-000000000005','fiscal.audit',true);
set local role anon;
select fiscal_test.denied('select public.fiscal_order(''1001'')','42501','anônimo recusado');
set local role authenticated;
select fiscal_test.login(1);
select fiscal_test.check(public.fiscal_settings()->>'real_enabled'='false','real permanentemente desligado');
select fiscal_test.denied('select * from public.fiscal_requests','42501','leitura direta negada');
select fiscal_test.denied('delete from public.fiscal_events','42501','histórico não pode ser apagado');
select fiscal_test.denied('select public.fiscal_worker_finish(gen_random_uuid(),gen_random_uuid())','42501','JWT humano não conclui executor');
select fiscal_test.denied($q$select public.fiscal_preview('1001','{}')$q$,'22023','seleções obrigatórias');
select fiscal_test.denied($q$select public.fiscal_preview('1001','{"modalidade_frete":true,"tipo_contribuinte":9}')$q$,'22023','booleano recusado');
select fiscal_test.denied($q$select public.fiscal_preview('1001','{"modalidade_frete":1.0,"tipo_contribuinte":9}')$q$,'22023','decimal recusado');
select fiscal_test.denied($q$select public.fiscal_preview('1001','{"modalidade_frete":1,"tipo_contribuinte":9,"approved":true}')$q$,'22023','aprovação forjada recusada');
select fiscal_test.denied(format('select public.fiscal_preview(%L,''{"modalidade_frete":1,"tipo_contribuinte":9}'')',n),'22023','pedido bloqueado '||n)
from unnest(array['1004','1005','1006','1009','1010']) n;
select public.fiscal_preview('1001','{"modalidade_frete":1,"tipo_contribuinte":2}')->>'id' preview2 \gset
select public.fiscal_preview('1001','{"modalidade_frete":1,"tipo_contribuinte":9}')->>'id' preview9 \gset
select fiscal_test.check(:'preview2'<>:'preview9','isento e não contribuinte preservados separadamente');
select fiscal_test.check(public.fiscal_preview('1001','{"modalidade_frete":1,"tipo_contribuinte":9}')->>'id'=:'preview9','prévia repetida reutilizada');
select fiscal_test.denied(format('select public.fiscal_confirm(%L,false,false)',:'preview9'),'22023','confirmação consciente exigida');
select public.fiscal_confirm(:'preview9',true,false)->>'id' request1 \gset
select fiscal_test.check((public.fiscal_confirm(:'preview9',true,false)->>'duplicate')::boolean,'clique e F5 idempotentes');
select public.fiscal_begin(:'request1','execute_demo')->>'claim' claim1 \gset
select fiscal_test.check((public.fiscal_begin(:'request1','execute_demo')->>'busy')::boolean,'segunda execução não adquire concessão');
select fiscal_test.denied(format('select public.fiscal_begin(%L,''execute_real'')',:'request1'),'42501','ação real recusada');
select fiscal_test.login(2);
select fiscal_test.check(public.fiscal_list()='[]','outra organização não enumera pedidos');
select fiscal_test.denied(format('select public.fiscal_begin(%L,''execute_demo'')',:'request1'),'42501','IDOR entre organizações recusado');
select fiscal_test.denied(format('select public.fiscal_history(%L)',:'request1'),'42501','auditoria isolada');
select fiscal_test.request('1001')->>'id' other_request \gset
select fiscal_test.check(:'other_request'<>:'request1','mesmo número em outra organização tem identidade própria');
set local role service_role;
select set_config('request.jwt.claims','{"role":"service_role"}',true);
select fiscal_test.denied(format('select public.fiscal_worker_finish(%L,gen_random_uuid())',:'request1'),'42501','nonce incorreto recusado');
select fiscal_test.check(public.fiscal_worker_finish(:'request1',:'claim1')->>'state'='authorized','executor DEMO autorizado');
select fiscal_test.denied(format('select public.fiscal_worker_finish(%L,%L)',:'request1',:'claim1'),'42501','nonce consumido não reutilizável');
set local role authenticated;
select fiscal_test.login(3);
select fiscal_test.check(public.fiscal_list()='[]','operador não enumera solicitantes do mesmo grupo');
select fiscal_test.denied(format('select public.fiscal_preview_get(%L)',:'preview9'),'42501','prévia de outro operador protegida');
select fiscal_test.denied('select public.fiscal_configure(false)','42501','configuração exclusiva Master');
select fiscal_test.request('1003')->>'id' pending_request \gset
select fiscal_test.check(public.fiscal_begin(:'pending_request')->>'state'='awaiting_approval','andamento não executa sem aprovação');
select fiscal_test.denied(format('select public.fiscal_approve(%L,true)',:'pending_request'),'42501','autoelevação recusada');
select fiscal_test.login(4);
select fiscal_test.check(jsonb_array_length(public.fiscal_list())=1,'aprovador vê pendência sem histórico irrestrito');
select fiscal_test.check(public.fiscal_approve(:'pending_request',true)->>'state'='confirmed','aprovação Auth identificada');
select fiscal_test.login(3);
select public.fiscal_begin(:'pending_request')->>'claim' pending_claim \gset
reset role;
update public.user_permissions set allowed=false where user_id='d1000000-0000-4000-8000-000000000004' and permission='fiscal.approve_pending';
set local role service_role;
select set_config('request.jwt.claims','{"role":"service_role"}',true);
select fiscal_test.check(public.fiscal_worker_finish(:'pending_request',:'pending_claim')->>'state'='blocked','revogação do aprovador antes do resultado bloqueia');
set local role authenticated;
select fiscal_test.login(1);
select fiscal_test.request(n)->>'id' id,n from unnest(array['1002','1007','1008']) n;
-- Os três casos são processados sem POST ao ERP; somente transições DEMO locais.
reset role;
create function fiscal_test.finish(n text) returns text language plpgsql as $$
declare r uuid; nonce uuid; result jsonb;
begin
 perform fiscal_test.login(1);
 select id into r from public.fiscal_requests where organization_id='d3000000-0000-4000-8000-000000000001' and order_number=n;
 nonce:=(public.fiscal_begin(r)->>'claim')::uuid;
 perform set_config('request.jwt.claims','{"role":"service_role"}',true);
 result:=public.fiscal_worker_finish(r,nonce);
 return result->>'state';
end $$;
select fiscal_test.check(fiscal_test.finish('1002')='rejected','rejeição preservada sem repetir criação');
select fiscal_test.check(fiscal_test.finish('1007')='needs_reconciliation','timeout na criação exige conferência');
select fiscal_test.check(fiscal_test.finish('1008')='needs_reconciliation','timeout na emissão exige conferência');
select fiscal_test.check((select external_id is null from public.fiscal_requests where order_number='1007'),'timeout de criação não inventa identificador');
select fiscal_test.check((select external_id is not null from public.fiscal_requests where order_number='1008'),'identificador após criação preservado');
set local role authenticated;
select fiscal_test.login(1);
select fiscal_test.check(public.fiscal_begin((select (x->>'id')::uuid from jsonb_array_elements(public.fiscal_list()) x where x->>'order_number'='1007'))->>'state'='needs_reconciliation','nova tentativa comum não repete criação');
select public.fiscal_begin((select (x->>'id')::uuid from jsonb_array_elements(public.fiscal_list()) x where x->>'order_number'='1007'),'reconcile_demo')->>'claim' reconciliation_claim \gset
reset role;
select id reconciliation_id from public.fiscal_requests where order_number='1007' \gset
set local role service_role;
select set_config('request.jwt.claims','{"role":"service_role"}',true);
select fiscal_test.check(public.fiscal_worker_finish(:'reconciliation_id',:'reconciliation_claim')->>'state'='authorized','reconciliação explícita DEMO sem recriação');
set local role authenticated;
select fiscal_test.login(5);
select fiscal_test.check(jsonb_array_length(public.fiscal_audit_list())=5,'auditor consulta metadados da organização');
select fiscal_test.check(not (public.fiscal_list()->0 ?| array['claim','requester_session','requester_expiry']),'segredos operacionais fora da resposta');
select fiscal_test.login(6);
select fiscal_test.denied('select public.fiscal_list()','42501','conta inativa recusada');
select fiscal_test.login(1,-10);
select fiscal_test.denied('select public.fiscal_list()','42501','JWT expirado recusado');
select fiscal_test.login(2);
select public.fiscal_configure(false);
select fiscal_test.denied('select public.fiscal_order(''1001'')','42501','Master pode desligar DEMO sem apagar histórico');
reset role;
select fiscal_test.check(not exists(select 1 from public.role_permissions where permission like 'fiscal.%'),'nenhum acesso fiscal herdado automaticamente');
select fiscal_test.check((select bool_and(relrowsecurity) from pg_class where oid in('public.fiscal_requests'::regclass,'public.fiscal_previews'::regclass,'public.fiscal_events'::regclass,'public.fiscal_configuration'::regclass)),'RLS habilitada em todas as tabelas fiscais');
select fiscal_test.check(not has_table_privilege('service_role','public.fiscal_requests','SELECT,INSERT,UPDATE,DELETE'),'executor usa RPC específica, sem acesso direto');
rollback;
