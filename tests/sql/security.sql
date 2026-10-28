-- Testes reais de PostgreSQL/RLS com identidades fictícias; tudo termina em rollback.
\set ON_ERROR_STOP on
begin;
create schema audit_test;
grant usage on schema audit_test to anon,authenticated;
create function audit_test.check(p_ok boolean,p_name text) returns void language plpgsql as $$
begin
  if p_ok is distinct from true then raise exception 'FALHOU: %',p_name; end if;
  raise notice 'APROVADO: %',p_name;
end $$;
create function audit_test.denied(p_statement text,p_state text,p_name text) returns void language plpgsql as $$
declare result text;
begin
  begin
    execute p_statement;
  exception when others then
    get stacked diagnostics result=returned_sqlstate;
    if result=p_state then raise notice 'APROVADO: %',p_name; return; end if;
    raise exception 'FALHOU: %; SQLSTATE esperado %, recebido %',p_name,p_state,result;
  end;
  raise exception 'FALHOU: %; operação indevida foi aceita',p_name;
end $$;
create function audit_test.login(p_index integer) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',jsonb_build_object(
    'sub','00000000-0000-4000-8000-'||lpad(p_index::text,12,'0'),
    'session_id','10000000-0000-4000-8000-'||lpad(p_index::text,12,'0'),
    'role','authenticated','exp',extract(epoch from now())+3600,
    'user_metadata',jsonb_build_object('role','Master'))::text,true);
end $$;
create function audit_test.products(p_code text default '001',p_stock integer default 10,p_cost numeric default 20)
returns jsonb language sql immutable as $$select jsonb_build_array(jsonb_build_object('codigo',p_code,'nome','Produto fictício',
  'fornecedor','Fornecedor fictício','estoque',p_stock,'custo_unitario',p_cost,'prazo_entrega_dias',7,
  'estoque_seguranca',2,'pedidos_pendentes',0,'reservas',0,'cobertura_alvo_dias',30))$$;

insert into auth.users(id,email,email_confirmed_at,raw_user_meta_data)
select ('00000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,'ficticio'||i||'@example.invalid',now(),'{"role":"Master"}'
from generate_series(1,6) i;
insert into auth.sessions(id,user_id,not_after)
select ('10000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
 ('00000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,now()+interval '1 day' from generate_series(1,6) i;
select private.bootstrap_master('00000000-0000-4000-8000-000000000001','Master fictício','Estoque fictício');
select audit_test.denied($q$select private.bootstrap_master('00000000-0000-4000-8000-000000000002','Outro Master')$q$,'22023','bootstrap não repete Master');
insert into public.organizations(id,name) values('20000000-0000-4000-8000-000000000002','Outra organização fictícia');
insert into public.app_users(user_id,organization_id,display_name,role,active) values
 ('00000000-0000-4000-8000-000000000006','20000000-0000-4000-8000-000000000002','Outra organização','Master',true);

set local role authenticated;
select audit_test.login(1);
select audit_test.check(public.app_context()->'profile'->>'role'='Master','Master inicial autorizado');
select public.admin_set_user('00000000-0000-4000-8000-000000000002','Operador fictício','Operador',true,'{}');
select public.admin_set_user('00000000-0000-4000-8000-000000000003','Visualizador fictício','Visualizador',true,'{}');
select public.admin_set_user('00000000-0000-4000-8000-000000000004','Conta desativada','Administrador',false,'{}');
select audit_test.check(jsonb_array_length(public.admin_list_users())=4,'Master vê somente contas da própria organização');
select audit_test.denied($q$select public.admin_set_user('00000000-0000-4000-8000-000000000002','Elevação','Master',true,'{}')$q$,'22023','UI não promove Master');
select audit_test.denied($q$select public.admin_set_user('00000000-0000-4000-8000-000000000001','Desativar Master','Operador',false,'{}')$q$,'42501','Master não perde acesso pelo painel');
select audit_test.denied($q$select public.admin_set_user('00000000-0000-4000-8000-000000000006','Mover organização','Operador',true,'{}')$q$,'42501','Master não captura usuário de outra organização');
select audit_test.denied($q$select public.admin_set_user('00000000-0000-4000-8000-000000000002','Operador','Operador',true,'{"users.manage":true}')$q$,'22023','gerenciamento não é delegável');

select public.import_inventory(audit_test.products(),'[{"codigo":"001","data":"2020-01-15","quantidade":5}]','2020-01-31','2020-01-01','2020-01-31');
select audit_test.check((public.import_inventory(audit_test.products(),'[{"codigo":"001","data":"2020-01-15","quantidade":5}]','2020-01-31','2020-01-01','2020-01-31')->>'reused')::boolean,'reimportação idempotente');
select audit_test.check((select count(*)=1 from public.inventory_imports),'lote idempotente não duplica histórico');
select public.import_inventory(audit_test.products('001',30,40),'[{"codigo":"001","data":"2020-02-15","quantidade":7}]','2020-02-29','2020-01-01','2020-02-29');
select audit_test.check(public.load_inventory('inventory.view')->'products'->0->>'estoque'='30','base atual usa último saldo');
select audit_test.check(public.load_inventory('reports.view','2020-01-01')->'products'->0->>'estoque'='10','relatório usa saldo histórico');
select audit_test.check(public.load_inventory('reports.view','2020-01-01')->'products'->0->>'custo_unitario'='20','relatório conserva custo histórico');
select audit_test.check((public.load_inventory('reports.view','2020-01-01')->'metadata'->>'month_complete')::boolean,'fechamento completo identificado');
select audit_test.check(not (public.load_inventory('reports.view','2019-12-01')->'metadata'->>'month_complete')::boolean,'mês sem fechamento é bloqueado');
select audit_test.denied($q$select public.import_inventory('[]','[]','2020-02-29','2020-01-01','2020-02-29')$q$,'22023','catálogo vazio rejeitado');
select audit_test.denied($q$select public.import_inventory(audit_test.products()||audit_test.products(),'[]','2020-02-29','2020-01-01','2020-02-29')$q$,'22023','SKU duplicado rejeitado');
select audit_test.denied($q$select public.import_inventory(audit_test.products(),'[{"codigo":"inexistente","data":"2020-01-02","quantidade":1}]','2020-02-29','2020-01-01','2020-02-29')$q$,'22023','venda sem produto rejeitada');
select audit_test.denied($q$select public.import_inventory(audit_test.products(),'[{"codigo":"001","data":"2020-01-02","quantidade":1.5}]','2020-02-29','2020-01-01','2020-02-29')$q$,'22023','quantidade fracionária rejeitada');
select audit_test.denied($q$select public.import_inventory(audit_test.products(),'[{"codigo":"001","data":"2020-01-02","quantidade":2147483647},{"codigo":"001","data":"2020-01-02","quantidade":1}]','2020-02-29','2020-01-01','2020-02-29')$q$,'22003','overflow agregado reverte lote');
select audit_test.check((select count(*)=2 from public.inventory_imports),'falha no final da importação faz rollback');
select public.update_parameters('001','{"prazo_entrega_dias":12,"estoque_seguranca":3,"cobertura_alvo_dias":40}');
select audit_test.check(public.load_inventory('inventory.view')->'products'->0->>'prazo_entrega_dias'='12','parâmetros atuais editáveis');
select audit_test.check(public.load_inventory('reports.view','2020-01-01')->'products'->0->>'prazo_entrega_dias'='7','edição preserva parâmetro histórico');
select audit_test.denied($q$select public.update_parameters('001','{"prazo_entrega_dias":-1,"estoque_seguranca":3,"cobertura_alvo_dias":40}')$q$,'22023','parâmetro negativo rejeitado');

select audit_test.login(2);
select audit_test.check(public.app_context()->'profile'->>'role'='Operador','user_metadata Master é ignorado');
select audit_test.check(not (public.load_inventory('inventory.view')->'products'->0 ? 'custo_unitario'),'RPC omite custos sem finance.view');
select audit_test.check((select count(*)=0 from public.inventory_costs),'RLS impede SELECT direto financeiro');
select audit_test.denied($q$select fingerprint from public.inventory_imports$q$,'42501','hash de importação não permite inferir custo');
select audit_test.check((select count(*)=1 from public.app_users),'operador não enumera outros usuários');
select audit_test.denied($q$select public.admin_list_users()$q$,'42501','operador não administra usuários');
select audit_test.denied($q$select public.import_inventory(audit_test.products(),'[]','2020-02-29','2020-01-01','2020-02-29')$q$,'42501','operador não importa por RPC');
select audit_test.denied($q$update public.app_users set role='Master' where user_id=auth.uid()$q$,'42501','DML direto não eleva papel');
select audit_test.denied($q$insert into public.user_permissions values(auth.uid(),'finance.view',true)$q$,'42501','DML direto não concede permissão');
select audit_test.denied($q$delete from public.inventory_products$q$,'42501','DELETE direto de estoque negado');
select audit_test.denied($q$select private.bootstrap_master(auth.uid(),'Master falso')$q$,'42501','bootstrap não executável por authenticated');
select audit_test.denied($q$select private.inventory_payload((select organization_id from public.app_users where user_id=auth.uid()),auth.uid(),null)$q$,'42501','helper interno não executável diretamente');
select public.save_decision('001','Avaliar compra',4,'Conferir fornecedor');
select audit_test.check(jsonb_array_length(public.list_decisions())=1,'decisão pertence ao autor');

select audit_test.login(3);
select audit_test.check(jsonb_array_length(public.load_inventory('dashboard.view')->'products')=1,'Visualizador pode abrir dashboard');
select audit_test.denied($q$select public.load_inventory('inventory.view')$q$,'42501','Visualizador sem inventory.view é negado');
select audit_test.denied($q$select public.load_inventory('reports.view','2020-01-01')$q$,'42501','Visualizador sem reports.view é negado');
select audit_test.denied($q$select public.save_decision('001','Ignorar',0,'')$q$,'42501','Visualizador não registra decisão');

select audit_test.login(4);
select audit_test.denied($q$select public.app_context()$q$,'42501','conta desativada não consulta contexto');
select audit_test.check((select count(*)=0 from public.inventory_products),'conta desativada não lê dados por RLS');
select audit_test.login(5);
select audit_test.denied($q$select public.app_context()$q$,'42501','conta sem perfil negada por padrão');

select audit_test.login(6);
select audit_test.check((select count(*)=0 from public.inventory_products),'isolamento entre organizações em tabela');
select audit_test.check(jsonb_array_length(public.load_inventory('inventory.view')->'products')=0,'isolamento entre organizações em RPC');
select public.import_inventory(audit_test.products('OUTRA',99,999),'[]','2020-01-31','2020-01-01','2020-01-31');
select audit_test.check((select count(*)=1 from public.inventory_products),'outra organização enxerga somente seu produto');
select audit_test.check(jsonb_array_length(public.list_decisions())=0,'decisões não atravessam organizações');

select audit_test.login(1);
select public.admin_set_user('00000000-0000-4000-8000-000000000002','Operador fictício','Operador',true,'{"finance.view":true,"reports.receive":true}');
select audit_test.login(2);
select audit_test.check((select count(*)=2 from public.inventory_costs),'concessão financeira passa a valer');
select audit_test.check(public.load_inventory('inventory.view')->'products'->0 ? 'custo_unitario','RPC libera custo autorizado');
select audit_test.login(1);
select public.admin_set_user('00000000-0000-4000-8000-000000000002','Operador fictício','Operador',true,'{"finance.view":false,"reports.receive":true}');
select audit_test.login(2);
select audit_test.check((select count(*)=0 from public.inventory_costs),'revogação financeira vale no mesmo JWT');
select audit_test.login(1);
select public.admin_set_user('00000000-0000-4000-8000-000000000002','Operador fictício','Operador',false,'{}');
select audit_test.login(2);
select audit_test.denied($q$select public.load_inventory('inventory.view')$q$,'42501','desativação vale em sessão antiga');

select audit_test.login(1);
select set_config('request.jwt.claims',jsonb_set(auth.jwt(),'{exp}',to_jsonb(extract(epoch from now())-1))::text,true);
select audit_test.denied($q$select public.app_context()$q$,'42501','token expirado recusado no banco');
select audit_test.login(1);
select set_config('request.jwt.claims',jsonb_set(auth.jwt(),'{session_id}','"10000000-0000-4000-8000-999999999999"')::text,true);
select audit_test.denied($q$select public.app_context()$q$,'42501','sessão encerrada não pode reutilizar JWT');
select audit_test.login(1);
select set_config('request.jwt.claims',jsonb_set(auth.jwt(),'{is_anonymous}','true')::text,true);
select audit_test.denied($q$select public.app_context()$q$,'42501','Auth anônimo não recebe privilégios de usuário');
reset role;
select audit_test.check(not exists(select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='public' and c.relkind='r' and not c.relrowsecurity),'RLS habilitado em todas as tabelas públicas');
select audit_test.check(not exists(select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='public' and p.prosecdef),'nenhuma função pública é SECURITY DEFINER');

set local role anon;
select audit_test.denied($q$select public.app_context()$q$,'42501','anon não consulta contexto');
select audit_test.denied($q$select * from public.inventory_products$q$,'42501','anon não consulta tabelas');
select audit_test.denied($q$select public.report_job_claim('segredo-errado','2020-01-01',true)$q$,'42501','job recusa segredo inválido');
reset role;

-- O segredo abaixo é exclusivamente fictício e jamais é usado fora desta transação.
select private.configure_job_secret(organization_id,'teste-ficticio-nao-utilizar-em-producao-123456789') from public.app_users where user_id='00000000-0000-4000-8000-000000000001';
set local role authenticated;
select audit_test.login(1);
select public.admin_set_user('00000000-0000-4000-8000-000000000002','Operador fictício','Operador',true,'{"reports.receive":true,"finance.view":true}');
select public.admin_report_settings(true,false,array['00000000-0000-4000-8000-000000000002'::uuid],array['estoque','financeiro'],1);
reset role;
create temporary table job_response(payload jsonb);
grant select,insert,delete on job_response to anon,authenticated;
set local role anon;
insert into job_response select public.report_job_claim('teste-ficticio-nao-utilizar-em-producao-123456789','2020-01-01',true);
select audit_test.check(jsonb_array_length(payload)=1,'job cria uma simulação autorizada') from job_response;
select audit_test.check(jsonb_array_length(public.report_job_claim('teste-ficticio-nao-utilizar-em-producao-123456789','2020-01-01',true))=0,'lease impede processamento simultâneo');
select audit_test.check((public.report_job_authorize('teste-ficticio-nao-utilizar-em-producao-123456789',(payload->0->>'id')::uuid,(payload->0->>'claim_token')::uuid)->>'allowed')::boolean,'destinatário autorizado antes do envio') from job_response;
reset role;
set local role authenticated;
select audit_test.login(1);
select public.admin_set_user('00000000-0000-4000-8000-000000000002','Operador fictício','Operador',true,'{"reports.receive":true,"finance.view":false}');
reset role;
set local role anon;
select audit_test.check(not (public.report_job_authorize('teste-ficticio-nao-utilizar-em-producao-123456789',(payload->0->>'id')::uuid,(payload->0->>'claim_token')::uuid)->'payload'->'products'->0 ? 'custo_unitario'),'custo revogado é removido antes do envio') from job_response;
reset role;
set local role authenticated;
select audit_test.login(1);
select public.admin_set_user('00000000-0000-4000-8000-000000000002','Operador fictício','Operador',false,'{}');
reset role;
set local role anon;
select audit_test.check(not (public.report_job_authorize('teste-ficticio-nao-utilizar-em-producao-123456789',(payload->0->>'id')::uuid,(payload->0->>'claim_token')::uuid)->>'allowed')::boolean,'destinatário desativado não recebe') from job_response;
select public.report_job_finish('teste-ficticio-nao-utilizar-em-producao-123456789',(payload->0->>'id')::uuid,(payload->0->>'claim_token')::uuid,'falha','ACESSO_REVOGADO') from job_response;
reset role;
set local role authenticated;
select audit_test.login(1);
select public.admin_set_user('00000000-0000-4000-8000-000000000002','Operador fictício','Operador',true,'{"reports.receive":true}');
reset role;
set local role anon;
delete from job_response;
insert into job_response select public.report_job_claim('teste-ficticio-nao-utilizar-em-producao-123456789','2020-01-01',true);
select audit_test.check(payload->0->>'attempts'='2','falha permite retry limitado') from job_response;
select public.report_job_finish('teste-ficticio-nao-utilizar-em-producao-123456789',(payload->0->>'id')::uuid,(payload->0->>'claim_token')::uuid,'simulado',null) from job_response;
select audit_test.check(jsonb_array_length(public.report_job_claim('teste-ficticio-nao-utilizar-em-producao-123456789','2020-01-01',true))=0,'simulação concluída não duplica');
select audit_test.check(jsonb_array_length(public.report_job_claim('teste-ficticio-nao-utilizar-em-producao-123456789','2020-01-01',false))=0,'envio real desligado não cria entregas');
reset role;
select audit_test.check((select count(*)>10 from public.audit_events),'operações sensíveis têm trilha de auditoria');
rollback;
