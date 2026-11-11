-- Banco descartável apenas; a execução inteira termina em rollback.
\set ON_ERROR_STOP on
begin;
create schema sync_test;
grant usage on schema sync_test to anon,authenticated;
create function sync_test.check(ok boolean,label text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'FALHOU: %',label; end if;
raise notice 'APROVADO SYNC: %',label; end $$;
create function sync_test.denied(statement text,state text,label text) returns void language plpgsql as $$
declare actual text;
begin begin execute statement; exception when others then get stacked diagnostics actual=returned_sqlstate;
  if actual=state then raise notice 'APROVADO SYNC: %',label; return; end if;
  raise exception 'FALHOU: % esperado %, recebido %',label,state,actual;
end; raise exception 'FALHOU: % permitiu acesso',label; end $$;
create function sync_test.login(i int) returns void language plpgsql as $$
begin perform set_config('request.jwt.claims',jsonb_build_object('sub','71000000-0000-4000-8000-'||lpad(i::text,12,'0'),
 'session_id','72000000-0000-4000-8000-'||lpad(i::text,12,'0'),'role','authenticated','exp',extract(epoch from now())+3600)::text,true); end $$;
create function sync_test.line(src uuid,store text default 'loja1',status text default 'completed',updated text default '2020-01-11T00:00:00Z',qty numeric default 5)
returns jsonb language sql as $$ select jsonb_build_array(jsonb_build_object('source_id',src,'store_id',store,
 'document_id','0001','line_id','001','product_id','001','sku','001','product_name','Produto fictício',
 'category','Categoria fictícia','supplier','Fornecedor fictício','sale_date','2020-01-10','status',status,
 'quantity',qty,'returned_quantity',1,'source_updated_at',updated)) $$;
insert into auth.users(id,email,email_confirmed_at)
select ('71000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,'sync'||i||'@example.invalid',now() from generate_series(1,4) i;
insert into auth.sessions(id,user_id,not_after)
select ('72000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
 ('71000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,now()+interval '1 day' from generate_series(1,4) i;
insert into public.organizations(id,name) values('73000000-0000-4000-8000-000000000001','Sync fictício1'),('73000000-0000-4000-8000-000000000002','Sync fictício2');
insert into public.app_users(user_id,organization_id,display_name,role,active) values
 ('71000000-0000-4000-8000-000000000001','73000000-0000-4000-8000-000000000001','Master1','Master',true),
 ('71000000-0000-4000-8000-000000000002','73000000-0000-4000-8000-000000000002','Master2','Master',true),
 ('71000000-0000-4000-8000-000000000003','73000000-0000-4000-8000-000000000001','Operador','Operador',true),
 ('71000000-0000-4000-8000-000000000004','73000000-0000-4000-8000-000000000001','Inativo','Administrador',false);
set local role authenticated;
select sync_test.login(1);
select (public.integration_register_source('teste1','Fonte fictícia',array['loja1','loja2'])->>'id') source \gset
select sync_test.check(jsonb_array_length(public.integration_sources())=1,'fonte limitada à organização');
select sync_test.denied($q$select public.integration_register_source('teste1','Fonte',array['loja1','loja1'])$q$,'22023','lojas duplicadas recusadas');
select j->>'id' run,j->>'claim_token' token from public.sync_begin(:'source','loja1','74000000-0000-4000-8000-000000000001','2020-01-01','2020-01-31',1,4,repeat('a',64)) j \gset
select sync_test.denied(format('select public.sync_commit(%L,%L,true)',:'run',:'token'),'22023','commit exige prévia');
select sync_test.denied(format('select public.sync_begin(%L,%L,%L,%L,%L,1,4,repeat(''a'',64))',:'source','loja1','74000000-0000-4000-8000-000000000002','2020-01-01','2020-01-31'),'55000','concorrência por escopo recusada');
select public.sync_stage(:'run',:'token',1,sync_test.line(:'source'));
select sync_test.check((public.sync_stage(:'run',:'token',1,sync_test.line(:'source'))->>'row_count')::int=1,'página repetida idempotente');
select sync_test.denied(format('select public.sync_stage(%L,%L,1,%L::jsonb)',:'run',:'token',sync_test.line(:'source','loja1','completed','2020-01-11T00:00:00Z',6)::text),'22023','página alterada não substitui checkpoint');
select sync_test.check(jsonb_array_length(public.sales_payload(:'source','loja1','2020-01-01','2020-01-31')->'rows')=0,'staging invisível antes do commit');
select sync_test.check((public.sync_preview(:'run',:'token')->>'net_quantity')::numeric=4,'devolução descontada só da análise');
select sync_test.check(public.sync_commit(:'run',:'token',true)->>'status'='completed','confirmação atômica após prévia');
select sync_test.check(public.sync_commit(:'run',:'token',true)->>'changed_rows'='1','commit repetido idempotente');
select sync_test.denied(format('select public.sync_begin(%L,%L,%L,%L,%L,1,4,repeat(''b'',64))',:'source','loja1','74000000-0000-4000-8000-000000000001','2020-01-01','2020-01-31'),'22023','mesma chave com conteúdo diferente recusada');
select sync_test.check(public.sales_payload(:'source','loja1','2020-01-01','2020-01-31')->'rows'->0->>'sku'='001','identificadores preservam zeros');
select sync_test.check((public.sales_payload(:'source','loja1','2020-01-01','2020-01-31')->'metadata'->>'coverage_verified')::boolean,'cobertura explicitamente conferida');
select sync_test.check(not (public.sales_payload(:'source','loja1','2019-12-01','2020-01-31')->'metadata'->>'coverage_verified')::boolean,'sem inventar abrangência fora do intervalo');
select sync_test.check((public.sales_payload(:'source','loja1','2019-12-01','2020-01-31')->'metadata'->>'period_end_coverage_verified')::boolean,
 'abrangência do mês atual permanece disponível sem o mês anterior');
select sync_test.check(public.sales_payload(:'source','loja1','2019-12-01','2020-01-31')->'metadata'->>'coverage_start'='2020-01-01',
 'início da cobertura retorna a ilha confirmada sem inventar dados anteriores');
select sync_test.check(jsonb_array_length(public.sales_payload(:'source','loja2','2020-01-01','2020-01-31')->'rows')=0,'lojas não são consolidadas');
select sync_test.denied($q$select * from public.sales_line_versions$q$,'42501','nenhuma leitura bruta das versões');
select sync_test.denied($q$update public.sales_lines_current set store_id='outra'$q$,'42501','DML direto bloqueado');
select sync_test.check(jsonb_array_length(public.sync_history(:'source','loja1'))=1,'histórico expõe resultado sem claim token');
select sync_test.check(not (public.sync_history(:'source','loja1')->0 ? 'claim_token'),'histórico não expõe concessão');
-- Uma importação idêntica com chave nova preserva a versão existente.
select j->>'id' run2,j->>'claim_token' token2 from public.sync_begin(:'source','loja1','74000000-0000-4000-8000-000000000002','2020-01-01','2020-01-31',1,4,repeat('a',64)) j \gset
select public.sync_stage(:'run2',:'token2',1,sync_test.line(:'source'));
select public.sync_preview(:'run2',:'token2');
select sync_test.check(public.sync_commit(:'run2',:'token2',false)->>'changed_rows'='0','reextração idêntica não cria versão');
-- Cancelamento confirmado na origem cria nova versão; não altera estoque.
select j->>'id' run3,j->>'claim_token' token3 from public.sync_begin(:'source','loja1','74000000-0000-4000-8000-000000000003','2020-01-01','2020-01-31',1,0,repeat('a',64)) j \gset
select public.sync_stage(:'run3',:'token3',1,sync_test.line(:'source','loja1','cancelled','2020-01-12T00:00:00Z'));
select public.sync_preview(:'run3',:'token3');
select public.sync_commit(:'run3',:'token3',true);
select sync_test.check(public.sales_payload(:'source','loja1','2020-01-01','2020-01-31')->'rows'->0->>'status'='cancelled','cancelamento explícito versionado');
reset role;
select sync_test.check((select count(*)=2 from public.sales_line_versions where source_id=:'source'),'versões anterior e cancelada preservadas');
select sync_test.check((select count(*)=0 from public.inventory_imports where organization_id='73000000-0000-4000-8000-000000000001'),'sync não baixa nem substitui estoque');
set local role authenticated;
select sync_test.login(1);
-- Reextração velha não ressuscita a venda cancelada; toda a promoção falha.
select j->>'id' run4,j->>'claim_token' token4 from public.sync_begin(:'source','loja1','74000000-0000-4000-8000-000000000004','2020-01-01','2020-01-31',1,4,repeat('a',64)) j \gset
select public.sync_stage(:'run4',:'token4',1,sync_test.line(:'source'));
select public.sync_preview(:'run4',:'token4');
select sync_test.denied(format('select public.sync_commit(%L,%L,true)',:'run4',:'token4'),'22023','versão antiga não ressuscita cancelamento');
select public.sync_cancel(:'run4',:'token4');
select sync_test.denied(format('select public.sync_commit(%L,%L,true)',:'run4',:'token4'),'42501','concessão cancelada não permite promoção');
select sync_test.check(public.sales_payload(:'source','loja1','2020-01-01','2020-01-31')->'rows'->0->>'status'='cancelled','falha e cancelamento não removem dado consolidado');
-- Campos pessoais/financeiros, fonte/loja forjados e reconciliação falsa falham.
select j->>'id' run5,j->>'claim_token' token5 from public.sync_begin(:'source','loja1','74000000-0000-4000-8000-000000000005','2020-01-01','2020-01-31',1,99,repeat('a',64)) j \gset
select sync_test.denied(format('select public.sync_stage(%L,%L,1,%L::jsonb)',:'run5',:'token5',jsonb_build_array(sync_test.line(:'source')->0||'{"cost":20}'::jsonb)::text),'22023','campo financeiro fora do contrato rejeitado');
select sync_test.denied(format('select public.sync_stage(%L,%L,1,%L::jsonb)',:'run5',:'token5',sync_test.line(:'source','loja2')::text),'22023','loja forjada dentro da página recusada');
select public.sync_stage(:'run5',:'token5',1,sync_test.line(:'source'));
select sync_test.denied(format('select public.sync_preview(%L,%L)',:'run5',:'token5'),'22023','soma divergente não autoriza prévia');
select public.sync_cancel(:'run5',:'token5');
-- Uma resposta que omite linhas antigas não ganha cobertura completa.
select j->>'id' incomplete,j->>'claim_token' incomplete_token from public.sync_begin(:'source','loja1',
 '74000000-0000-4000-8000-000000000019','2020-01-01','2020-01-31',0,0,repeat('e',64)) j \gset
select public.sync_preview(:'incomplete',:'incomplete_token');
select sync_test.denied(format('select public.sync_commit(%L,%L,true)',:'incomplete',:'incomplete_token'),
 '22023','resposta vazia não apaga linhas nem declara abrangência completa');
select sync_test.check(public.sync_commit(:'incomplete',:'incomplete_token',false)->>'changed_rows'='0',
 'carga parcial sem confirmação conserva linhas antigas');
select sync_test.check(public.sales_payload(:'source','loja1','2020-01-01','2020-01-31')->'rows'->0->>'status'='cancelled',
 'ausência no incremental não remove cancelamento preservado');
-- O Master consegue liberar staging abandonado, sem alterar o consolidado.
select j->>'id' abandoned from public.sync_begin(:'source','loja1','74000000-0000-4000-8000-000000000009','2020-01-01','2020-01-31',1,4,repeat('a',64)) j \gset
reset role;
update public.sync_runs set status='interrupted',lease_until=now()-interval '1 minute' where id=:'abandoned';
set local role authenticated;
select sync_test.check(public.sync_cancel(:'abandoned',null)->>'status'='cancelled','limpeza de execução abandonada libera o escopo');
-- Coberturas só formam uma ilha se não há nenhum dia desconhecido.
reset role;
insert into public.sync_runs(organization_id,source_id,store_id,author_id,request_key,content_hash,status,
 period_start,period_end,expected_rows,expected_quantity,coverage_confirmed,finished_at) values
 ('73000000-0000-4000-8000-000000000001',:'source','loja1','71000000-0000-4000-8000-000000000001',
 '74000000-0000-4000-8000-000000000010',repeat('a',64),'completed','2020-02-02','2020-02-29',0,0,true,now()),
 ('73000000-0000-4000-8000-000000000001',:'source','loja2','71000000-0000-4000-8000-000000000001',
 '74000000-0000-4000-8000-000000000011',repeat('a',64),'completed','2020-02-01','2020-02-01',0,0,true,now()),
 ('73000000-0000-4000-8000-000000000001',:'source','loja1','71000000-0000-4000-8000-000000000001',
 '74000000-0000-4000-8000-000000000012',repeat('a',64),'completed','2020-02-01','2020-02-01',0,0,false,now());
set local role authenticated;
select sync_test.check(not (public.sales_payload(:'source','loja1','2020-01-01','2020-02-29')->'metadata'->>'coverage_verified')::boolean,
 'lacuna de um dia não é coberta por outra loja ou execução não conferida');
reset role;
update public.sync_runs set coverage_confirmed=true where request_key='74000000-0000-4000-8000-000000000012';
set local role authenticated;
select sync_test.check((public.sales_payload(:'source','loja1','2020-01-01','2020-02-29')->'metadata'->>'coverage_verified')::boolean,
 'intervalos adjacentes confirmados formam cobertura contínua');
reset role;
insert into public.sync_runs(organization_id,source_id,store_id,author_id,request_key,content_hash,status,
 period_start,period_end,expected_rows,expected_quantity,coverage_confirmed,finished_at) values
 ('73000000-0000-4000-8000-000000000001',:'source','loja1','71000000-0000-4000-8000-000000000001',
 '74000000-0000-4000-8000-000000000013',repeat('a',64),'completed','2020-02-15','2020-03-05',0,0,true,now()),
 ('73000000-0000-4000-8000-000000000001',:'source','loja1','71000000-0000-4000-8000-000000000001',
 '74000000-0000-4000-8000-000000000014',repeat('a',64),'completed','2020-02-16','2020-02-16',0,0,true,now());
set local role authenticated;
select sync_test.check((public.sales_payload(:'source','loja1','2020-01-01','2020-03-05')->'metadata'->>'coverage_verified')::boolean,
 'intervalos sobrepostos e aninhados não abrem lacuna falsa');
select sync_test.check(not (public.sales_payload(:'source','loja1','2020-01-01','2020-03-06')->'metadata'->>'coverage_verified')::boolean,
 'limite final não é ampliado sem observação');
select sync_test.login(2);
select sync_test.denied(format('select public.sales_payload(%L,%L,%L,%L)',:'source','loja1','2020-01-01','2020-01-31'),'42501','Master de outra organização não lê fonte');
select sync_test.denied(format('select public.sync_history(%L,%L)',:'source','loja1'),'42501','IDOR do histórico recusado');
select sync_test.check(jsonb_array_length(public.integration_sources())=0,'lista de fontes isolada');
select sync_test.login(3);
select sync_test.check(jsonb_array_length(public.sales_payload(:'source','loja1','2020-01-01','2020-01-31')->'rows')=1,'Operador autorizado lê análise sem custos');
select sync_test.denied($q$select public.integration_register_source('invadir','Fonte',array['loja1'])$q$,'42501','Operador não cria fonte');
select sync_test.denied(format('select public.sync_begin(%L,%L,%L,%L,%L,1,4,repeat(''a'',64))',:'source','loja1','74000000-0000-4000-8000-000000000006','2020-01-01','2020-01-31'),'42501','Operador não sincroniza');
reset role;
insert into public.user_permissions values('71000000-0000-4000-8000-000000000003','trends.view',false);
set local role authenticated;
select sync_test.denied(format('select public.sales_payload(%L,%L,%L,%L)',:'source','loja1','2020-01-01','2020-01-31'),'42501','revogação trends vale na próxima consulta');
select sync_test.login(4);
select sync_test.denied($q$select public.integration_sources()$q$,'42501','usuário desativado não lê fontes');
reset role;
update auth.sessions set not_after=now()-interval '1 minute' where user_id='71000000-0000-4000-8000-000000000001';
set local role authenticated;
select sync_test.login(1);
select sync_test.denied($q$select public.integration_sources()$q$,'42501','sessão expirada não lê fontes');
set local role anon;
select sync_test.denied($q$select public.integration_sources()$q$,'42501','anônimo não chama API');
select sync_test.denied($q$select * from public.sync_runs$q$,'42501','anônimo não lê lotes');
reset role;
select sync_test.check((select bool_and(relrowsecurity) from pg_class where oid in (
 'public.integration_sources'::regclass,'public.sync_runs'::regclass,'public.sync_pages'::regclass,
 'public.sync_staging'::regclass,'public.sales_line_versions'::regclass,'public.sales_lines_current'::regclass)),
 'RLS habilitada em todas as seis tabelas novas');
rollback;
