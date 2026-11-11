-- Exclusivo de banco descartável. Dados fictícios e rollback ao final.
\set ON_ERROR_STOP on
begin;
create schema commercial_test;
grant usage on schema commercial_test to authenticated,anon;
create function commercial_test.check(ok boolean,label text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'FALHOU: %',label; end if;
raise notice 'APROVADO COMERCIAL: %',label; end $$;
create function commercial_test.denied(statement text,state text,label text) returns void language plpgsql as $$
declare actual text;
begin begin execute statement; exception when others then get stacked diagnostics actual=returned_sqlstate;
  if actual=state then raise notice 'APROVADO COMERCIAL: %',label; return; end if;
  raise exception 'FALHOU: % esperado %, recebido %',label,state,actual;
end; raise exception 'FALHOU: % permitiu acesso',label; end $$;
create function commercial_test.login(i int) returns void language plpgsql as $$
begin perform set_config('request.jwt.claims',jsonb_build_object('sub','b1000000-0000-4000-8000-'||lpad(i::text,12,'0'),
 'session_id','b2000000-0000-4000-8000-'||lpad(i::text,12,'0'),'role','authenticated','exp',extract(epoch from now())+3600)::text,true); end $$;
create function commercial_test.item(units int default 10) returns jsonb language sql as $$
select jsonb_build_array(jsonb_build_object('dimension','customer','alert_code','volume_reduzido','customer_ref',repeat('a',64),
 'period_start','2020-02-01','period_end','2020-02-28','current_units',units,'previous_units',100,
 'current_orders',1,'previous_orders',4,'group_customers',0,'previous_customers',0,'total_orders',0,
 'median_gap_days',7,'recency_days',15)) $$;
create function commercial_test.manifest(hash text default repeat('a',64)) returns jsonb language sql as $$
select jsonb_build_object('model','media_7_dias','training_start','2019-01-01','training_end','2019-07-29',
 'data_hash',hash,'features','["lag_1","lag_7","media_7","media_28","dia_semana"]'::jsonb,'library_version','1.9.1',
 'evaluation',jsonb_build_object('selecionado','media_7_dias','cortes','["2019-05-01","2019-05-31","2019-06-30"]'::jsonb,
 'treino_ate','2019-06-29','limites','Validação temporal local; não comprova ganho em dados empresariais nem sazonalidade anual.',
 'metricas',(select jsonb_object_agg(h::text,jsonb_build_object('amostras',3*h,'mae_baseline',2,'mae_modelo',3,
 'wape_baseline',0.2,'wape_modelo',0.3,'vies_modelo',1)) from unnest(array[7,14,21,30]) h)),
 'predictions',(select jsonb_agg(jsonb_build_object('date',date '2019-07-29'+i,'units',5) order by i) from generate_series(1,30) i)) $$;
create function commercial_test.line(src uuid,dia text default '2020-01-10',estado text default 'completed',atualizado text default '2020-01-11T00:00:00Z')
returns jsonb language sql as $$ select jsonb_build_array(jsonb_build_object('source_id',src,'store_id','loja1',
 'document_id','DEMO-0001','line_id','001','product_id','001','sku','001','product_name','Produto fictício',
 'category','Categoria fictícia','supplier','Fornecedor fictício','sale_date',dia,'status',estado,
 'quantity',5,'returned_quantity',1,'source_updated_at',atualizado)) $$;
insert into auth.users(id,email,email_confirmed_at)
select ('b1000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,'comercial'||i||'@example.invalid',now() from generate_series(1,4) i;
insert into auth.sessions(id,user_id,not_after)
select ('b2000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
 ('b1000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,now()+interval '1 day' from generate_series(1,4) i;
insert into public.organizations(id,name) values('b3000000-0000-4000-8000-000000000001','Comercial fictício1'),('b3000000-0000-4000-8000-000000000002','Comercial fictício2');
insert into public.app_users(user_id,organization_id,display_name,role,active) values
 ('b1000000-0000-4000-8000-000000000001','b3000000-0000-4000-8000-000000000001','Master1','Master',true),
 ('b1000000-0000-4000-8000-000000000002','b3000000-0000-4000-8000-000000000002','Master2','Master',true),
 ('b1000000-0000-4000-8000-000000000003','b3000000-0000-4000-8000-000000000001','Visualizador','Visualizador',true),
 ('b1000000-0000-4000-8000-000000000004','b3000000-0000-4000-8000-000000000001','Inativo','Administrador',false);
set local role authenticated;
select commercial_test.login(1);
select commercial_test.check(not (public.app_context()->'permissions' ? 'commercial.view'),
 'cliente antigo continua com contrato compatível sem perder o login');
select commercial_test.check(public.app_context(array['commercial.view'])->'permissions'='["commercial.view"]'::jsonb,
 'cliente novo recebe somente concessões que compreende');
select commercial_test.check(public.app_context(array['permissao.inventada'])->'permissions'='[]'::jsonb,
 'declarar permissão no cliente não a concede');
select public.integration_register_source('comercial-ficticia','Fonte comercial DEMO',array['loja1'])->>'id' source \gset
select public.publish_commercial_batch(:'source','loja1','2020-02-01','2020-02-28','delivery',commercial_test.item())->>'id' first_batch \gset
select commercial_test.check((public.publish_commercial_batch(:'source','loja1','2020-02-01','2020-02-28','delivery',commercial_test.item())->>'reused')::boolean,'reprocessamento idempotente');
select commercial_test.check(jsonb_array_length(public.commercial_insights(:'source','loja1','customer'))=1,'resultado pré-calculado disponível');
select commercial_test.denied(format('select public.publish_commercial_batch(%L,''loja1'',''2020-02-01'',''2020-02-28'',''delivery'',%L::jsonb)',
 :'source',jsonb_build_array(commercial_test.item()->0||'{"email":"proibido@example.invalid"}'::jsonb)::text),'22023','PII fora do contrato recusada');
select public.publish_commercial_batch(:'source','loja1','2020-02-01','2020-02-28','delivery','[]');
select commercial_test.check(jsonb_array_length(public.commercial_insights(:'source','loja1','customer'))=0,'nova versão vazia substitui somente o resumo ativo');
select public.rollback_commercial_batch(:'source','loja1');
select commercial_test.check(jsonb_array_length(public.commercial_insights(:'source','loja1','customer'))=1,'rollback recupera resultado anterior');
select commercial_test.denied($q$select * from public.commercial_insights$q$,'42501','nenhum acesso direto aos indicadores');
select public.save_commercial_model(:'source','loja1','2304400','001',commercial_test.manifest())->>'id' first_model \gset
select commercial_test.check((public.save_commercial_model(:'source','loja1','2304400','001',commercial_test.manifest())->>'reused')::boolean,'modelo idempotente sem checkpoint duplicado');
select commercial_test.denied(format('select public.save_commercial_model(%L,''loja1'',''2304400'',''001'',%L::jsonb)',:'source',
 jsonb_set(jsonb_set(commercial_test.manifest(repeat('c',64)),'{model}','"arvore"'),'{evaluation,selecionado}','"arvore"')::text),
 '22023','ML que perde para baseline não é ativado');
select commercial_test.denied(format('select public.save_commercial_model(%L,''loja1'',''2304400'',''001'',%L::jsonb)',:'source',
 (commercial_test.manifest()||'{"pickle":"proibido"}'::jsonb)::text),'22023','artefato executável não entra no banco');
select public.save_commercial_model(:'source','loja1','2304400','001',commercial_test.manifest(repeat('b',64)));
select public.rollback_commercial_model(:'source','loja1','2304400','001');
select commercial_test.check(public.commercial_model_status()->0->>'id'=:'first_model','rollback de modelo preserva versão anterior');
-- Agregações só recebem linhas consolidadas e corrigem as datas atingidas.
select j->>'id' run,j->>'claim_token' token from public.sync_begin(:'source','loja1',
 'b4000000-0000-4000-8000-000000000001','2020-01-01','2020-01-31',1,4,repeat('a',64)) j \gset
select commercial_test.denied(format('select public.refresh_commercial_daily(%L)',:'run'),'22023','staging não alimenta agregados');
select public.sync_stage(:'run',:'token',1,commercial_test.line(:'source'));
select public.sync_preview(:'run',:'token');
select public.sync_commit(:'run',:'token',true);
select public.refresh_commercial_daily(:'run');
select commercial_test.check(public.commercial_daily(:'source','loja1','2020-01-01','2020-01-31')->0->>'units'='4','agregado desconta devolução confirmada');
select commercial_test.check((public.refresh_commercial_daily(:'run')->>'reused')::boolean,'checkpoint impede agregação duplicada');
select j->>'id' run2,j->>'claim_token' token2 from public.sync_begin(:'source','loja1',
 'b4000000-0000-4000-8000-000000000002','2020-01-01','2020-01-31',1,4,repeat('b',64)) j \gset
select public.sync_stage(:'run2',:'token2',1,commercial_test.line(:'source','2020-01-12','completed','2020-01-13T00:00:00Z'));
select public.sync_preview(:'run2',:'token2');
select public.sync_commit(:'run2',:'token2',true);
select public.refresh_commercial_daily(:'run2');
select commercial_test.check(jsonb_array_length(public.commercial_daily(:'source','loja1','2020-01-01','2020-01-31'))=1
 and public.commercial_daily(:'source','loja1','2020-01-01','2020-01-31')->0->>'date'='2020-01-12','correção tardia retira apenas resumo da data antiga');
reset role;
select commercial_test.check((select count(*)=2 from public.sales_line_versions where source_id=:'source'),'agregação não remove versões detalhadas');
set local role authenticated;
select j->>'id' run3,j->>'claim_token' token3 from public.sync_begin(:'source','loja1',
 'b4000000-0000-4000-8000-000000000003','2020-01-01','2020-01-31',1,0,repeat('c',64)) j \gset
select public.sync_stage(:'run3',:'token3',1,commercial_test.line(:'source','2020-01-12','cancelled','2020-01-14T00:00:00Z'));
select public.sync_preview(:'run3',:'token3');
select public.sync_commit(:'run3',:'token3',true);
select public.refresh_commercial_daily(:'run3');
select commercial_test.check(public.commercial_daily(:'source','loja1','2020-01-01','2020-01-31')='[]'::jsonb,'cancelamento confirmado corrige resumo sem baixar estoque');
select commercial_test.login(3);
select commercial_test.check(not (public.app_context(array['commercial.manage','users.manage'])->'permissions' ? 'commercial.manage'),
 'pedido do cliente não eleva privilégio');
select commercial_test.denied($q$select public.commercial_sources()$q$,'42501','tendências não concede permissão comercial');
reset role;
insert into public.user_permissions values('b1000000-0000-4000-8000-000000000003','commercial.view',true);
set local role authenticated;
select commercial_test.denied(format('select public.commercial_insights(%L,''loja1'',''customer'')',:'source'),'42501','detalhe de cliente exige permissão específica');
select commercial_test.check(jsonb_array_length(public.commercial_insights(:'source','loja1','city'))=0,'visão agregada não vaza cliente');
select commercial_test.denied($q$select public.storage_overview()$q$,'42501','ocupação somente Master');
select commercial_test.denied($q$select public.commercial_model_status()$q$,'42501','manifestos somente Master');
select commercial_test.denied(format('select public.rollback_commercial_batch(%L,''loja1'')',:'source'),'42501','Visualizador não publica nem reverte');
select commercial_test.login(2);
select commercial_test.denied(format('select public.commercial_insights(%L,''loja1'',''customer'')',:'source'),'42501','Master de outra organização não lê resultados');
select commercial_test.login(4);
select commercial_test.denied($q$select public.commercial_sources()$q$,'42501','conta desativada não lê fontes');
reset role;
update auth.sessions set not_after=now()-interval '1 minute' where user_id='b1000000-0000-4000-8000-000000000001';
set local role authenticated;
select commercial_test.login(1);
select commercial_test.denied($q$select public.commercial_sources()$q$,'42501','sessão expirada negada');
set local role anon;
select commercial_test.denied($q$select public.commercial_sources()$q$,'42501','anônimo negado');
reset role;
select commercial_test.check((select bool_and(relrowsecurity) from pg_class where relname in (
 'commercial_daily_sku','commercial_processed_runs','commercial_insights','commercial_batches','commercial_active_batches',
 'commercial_model_runs','commercial_active_models','storage_measurements')),'RLS em todas as tabelas comerciais');
rollback;
