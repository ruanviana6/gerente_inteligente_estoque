-- Somente banco descartável; nenhuma chamada ao ERP e rollback integral.
\set ON_ERROR_STOP on
begin;
create schema sync_auto_test;
grant usage on schema sync_auto_test to anon,authenticated,service_role;
create function sync_auto_test.check(ok boolean,label text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'FALHOU AUTO: %',label; end if;
raise notice 'APROVADO AUTO: %',label; end $$;
create function sync_auto_test.denied(statement text,state text,label text) returns void language plpgsql as $$
declare actual text;
begin begin execute statement; exception when others then get stacked diagnostics actual=returned_sqlstate;
  if actual=state then raise notice 'APROVADO AUTO: %',label; return; end if;
  raise exception 'FALHOU AUTO: % esperado %, recebido %',label,state,actual;
end; raise exception 'FALHOU AUTO: % permitiu acesso',label; end $$;
create function sync_auto_test.login(i int) returns void language plpgsql as $$
begin perform set_config('request.jwt.claims',jsonb_build_object('sub','a1000000-0000-4000-8000-'||lpad(i::text,12,'0'),
 'session_id','a2000000-0000-4000-8000-'||lpad(i::text,12,'0'),'role','authenticated','exp',extract(epoch from now())+3600)::text,true); end $$;
insert into auth.users(id,email,email_confirmed_at)
select ('a1000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,'auto'||i||'@example.invalid',now() from generate_series(1,3) i;
insert into auth.sessions(id,user_id,not_after)
select ('a2000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
 ('a1000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,now()+interval '1 day' from generate_series(1,3) i;
insert into public.organizations(id,name) values('a3000000-0000-4000-8000-000000000001','Automação fictícia1'),('a3000000-0000-4000-8000-000000000002','Automação fictícia2');
insert into public.app_users(user_id,organization_id,display_name,role,active) values
 ('a1000000-0000-4000-8000-000000000001','a3000000-0000-4000-8000-000000000001','Master1','Master',true),
 ('a1000000-0000-4000-8000-000000000002','a3000000-0000-4000-8000-000000000002','Master2','Master',true),
 ('a1000000-0000-4000-8000-000000000003','a3000000-0000-4000-8000-000000000001','Operador','Operador',true);
set local role authenticated;
select sync_auto_test.login(1);
select (public.integration_register_source('auto-teste','Fonte fictícia',array['loja1','loja2'])->>'id') source \gset
select sync_auto_test.denied(format('select public.sync_begin(%L,%L,gen_random_uuid(),current_date-732,current_date,0,0,repeat(''a'',64))',:'source','loja1'),'22023','RPC manual também recusa 733 dias');
select sync_auto_test.denied(format('select public.sync_automation_configure(%L,%L,repeat(''a'',64),current_date-732,current_date,24,true,true)',:'source','loja1'),'22023','733 dias recusados');
select sync_auto_test.denied(format('select public.sync_automation_configure(%L,%L,repeat(''a'',64),current_date-10,current_date+1,24,true,true)',:'source','loja1'),'22023','futuro recusado');
select sync_auto_test.denied(format('select public.sync_automation_configure(%L,%L,repeat(''a'',64),current_date-10,current_date,24,true,false)',:'source','loja1'),'22023','aprovação obrigatória');
select j->>'automation_id' automation,j->>'secret' secret
 from public.sync_automation_configure(:'source','loja1',repeat('a',64),
 (now() at time zone 'America/Fortaleza')::date-731,(now() at time zone 'America/Fortaleza')::date,24,true,true) j \gset
select sync_auto_test.check(public.sync_automation_status(:'source','loja1')->>'phase'='backfill','732 dias aceitos');
select sync_auto_test.check(not public.sync_automation_status(:'source','loja1') ? 'secret','status sem credencial');
select sync_auto_test.check(not public.sync_automation_configure(:'source','loja1',repeat('a',64),
 (now() at time zone 'America/Fortaleza')::date-731,(now() at time zone 'America/Fortaleza')::date,24,true,true) ? 'secret','configuração comum não rotaciona nem reexpõe segredo');
select sync_auto_test.login(2);
select sync_auto_test.denied(format('select public.sync_automation_status(%L,%L)',:'source','loja1'),'42501','outra organização não consulta configuração');
select sync_auto_test.denied(format('select public.sync_automation_pause(%L,%L)',:'source','loja1'),'42501','outra organização não pausa');
select sync_auto_test.login(3);
select sync_auto_test.denied(format('select public.sync_automation_configure(%L,%L,repeat(''a'',64),current_date-10,current_date,24,true,true)',:'source','loja1'),'42501','operador não ativa mesmo com aprovação forjada');
select sync_auto_test.denied($q$select * from private.sync_automations$q$,'42501','tabela privada sem leitura direta');
select sync_auto_test.denied($q$select private.sync_begin_core(null,null,null,null,null,null,null,0,0,repeat('a',64))$q$,'42501','núcleo inacessível diretamente');
reset role;
-- O job continua quando o aprovador sai: não depende de sessão nem inventa JWT.
delete from auth.sessions where user_id='a1000000-0000-4000-8000-000000000001';
set local role anon;
select set_config('request.jwt.claims','{}',true);
select sync_auto_test.denied(format('select public.sync_job_claim(%L,repeat(''a'',64))',repeat('f',64)),'42501','segredo errado negado');
select sync_auto_test.denied(format('select public.sync_job_claim(%L,repeat(''b'',64))',:'secret'),'42501','contrato alterado negado');
select j->>'claim' claim,j->>'start' start,j->>'end' finish,j->>'request_key' request
 from public.sync_job_claim(:'secret',repeat('a',64)) j \gset
select sync_auto_test.check((:'finish')::date-(:'start')::date=29,'histórico começa nos trinta dias mais recentes');
select sync_auto_test.check(public.sync_job_claim(:'secret',repeat('a',64)) is null,'concorrente não recebe segunda concessão');
select sync_auto_test.check((public.sync_job_step(:'secret',:'claim','authorize',jsonb_build_object('p_source_id',:'source','p_store_id','loja1'))->>'allowed')::boolean,'delegação funciona após logout');
select sync_auto_test.denied(format('select public.sync_job_step(%L,%L,%L,%L::jsonb)',:'secret',:'claim','admin_manage_user','{}'),'22023','RPC arbitrária negada');
select sync_auto_test.denied(format('select public.sync_job_step(%L,%L,%L,%L::jsonb)',:'secret',:'claim','authorize',jsonb_build_object('p_source_id',:'source','p_store_id','loja2')),'42501','outra loja negada');
select sync_auto_test.denied(format('select public.sync_job_step(%L,%L,%L,%L::jsonb)',:'secret',:'claim','finish','{}'),'22023','cursor não avança antes do commit');
select j->>'id' run,j->>'claim_token' run_token from public.sync_job_step(:'secret',:'claim','begin',jsonb_build_object(
 'p_source_id',:'source','p_store_id','loja1','p_request_key',:'request','p_period_start',:'start','p_period_end',:'finish',
 'p_expected_rows',1,'p_expected_quantity','4','p_content_hash',repeat('a',64))) j \gset
select sync_auto_test.denied(format('select public.sync_job_step(%L,%L,%L,%L::jsonb)',:'secret',:'claim','stage',jsonb_build_object(
 'p_run_id',gen_random_uuid(),'p_claim_token',:'run_token','p_page',1,'p_rows','[]'::jsonb)),'42501','run forjado negado');
select jsonb_build_array(jsonb_build_object('source_id',:'source','store_id','loja1','document_id','0001','line_id','001','product_id','001','sku','001',
 'sale_date',:'start','status','completed','quantity',5,'returned_quantity',1,'source_updated_at',to_char(now() at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')))::text rows \gset
select sync_auto_test.denied(format('select public.sync_job_step(%L,%L,%L,%L::jsonb)',:'secret',:'claim','stage',jsonb_build_object(
 'p_run_id',:'run','p_claim_token',:'run_token','p_page',1,'p_rows',jsonb_build_array((:'rows'::jsonb)->0||'{"cost":20}'::jsonb))),'22023','campo financeiro injetado recusado');
select public.sync_job_step(:'secret',:'claim','stage',jsonb_build_object('p_run_id',:'run','p_claim_token',:'run_token','p_page',1,'p_rows',:'rows'::jsonb));
select sync_auto_test.check(public.sync_job_step(:'secret',:'claim','stage',jsonb_build_object('p_run_id',:'run','p_claim_token',:'run_token','p_page',1,'p_rows',:'rows'::jsonb))->>'row_count'='1','reenvio de página não duplica');
select public.sync_job_step(:'secret',:'claim','preview',jsonb_build_object('p_run_id',:'run','p_claim_token',:'run_token'));
select sync_auto_test.denied(format('select public.sync_job_step(%L,%L,%L,%L::jsonb)',:'secret',:'claim','commit',jsonb_build_object(
 'p_run_id',:'run','p_claim_token',:'run_token','p_coverage_confirmed',true)),'22023','cron não declara cobertura humana');
select sync_auto_test.check(public.sync_job_step(:'secret',:'claim','commit',jsonb_build_object(
 'p_run_id',:'run','p_claim_token',:'run_token','p_coverage_confirmed',false))->>'status'='completed','commit automático reconcilia e consolida');
select sync_auto_test.check(public.sync_job_step(:'secret',:'claim','commit',jsonb_build_object(
 'p_run_id',:'run','p_claim_token',:'run_token','p_coverage_confirmed',false))->>'changed_rows'='1','commit repetido não duplica');
reset role;
select sync_auto_test.check((select count(*)=1 from public.sales_line_versions where source_id=:'source'),'uma versão somente');
select sync_auto_test.check((select not coverage_confirmed from public.sync_runs where id=:'run'),'sem cobertura fabricada');
select sync_auto_test.check((select cursor_end=history_end from private.sync_automations where id=:'automation'),'cursor ainda aguarda finish');
-- Simula queda depois do commit e antes do finish. Retomada não precisa reler ERP.
update private.sync_automations set lease_until=now()-interval '1 minute' where id=:'automation';
set local role anon;
select sync_auto_test.denied(format('select public.sync_job_step(%L,%L,%L,%L::jsonb)',:'secret',:'claim','authorize',jsonb_build_object('p_source_id',:'source','p_store_id','loja1')),'42501','concessão vencida não opera');
select j->>'claim' new_claim, j->'completed_run'->>'status' completed from public.sync_job_claim(:'secret',repeat('a',64)) j \gset
select sync_auto_test.check(:'completed'='completed','retomada reconhece commit concluído');
select sync_auto_test.denied(format('select public.sync_job_step(%L,%L,%L,%L::jsonb)',:'secret',:'claim','finish','{}'),'42501','concessão anterior invalidada');
select sync_auto_test.check(public.sync_job_step(:'secret',:'new_claim','finish','{}')->>'status'='completed','finish avança só lote confirmado');
select sync_auto_test.check(public.sync_job_claim(:'secret',repeat('a',64)) is null,'agenda impede reconsulta imediata');
reset role;
select sync_auto_test.check((select cursor_end=history_end-30 from private.sync_automations where id=:'automation'),'cursor recua trinta dias');
update private.sync_automations set next_due=now()-interval '1 minute' where id=:'automation';
set local role anon;
select j->>'claim' fail_claim,j->>'start' fail_start from public.sync_job_claim(:'secret',repeat('a',64)) j \gset
select public.sync_job_step(:'secret',:'fail_claim','fail','{}');
reset role;
select sync_auto_test.check((select cursor_end=history_end-30 and last_error_code='SYNC_FAILED' from private.sync_automations where id=:'automation'),'falha registra código seguro sem avançar cursor');
update private.sync_automations set next_due=now()-interval '1 minute' where id=:'automation';
update public.app_users set active=false where user_id='a1000000-0000-4000-8000-000000000001';
set local role anon;
select sync_auto_test.denied(format('select public.sync_job_claim(%L,repeat(''a'',64))',:'secret'),'42501','Master desativado revoga job');
reset role;
update public.app_users set active=true,role='Operador' where user_id='a1000000-0000-4000-8000-000000000001';
set local role anon;
select sync_auto_test.denied(format('select public.sync_job_claim(%L,repeat(''a'',64))',:'secret'),'42501','rebaixamento revoga job');
reset role;
update public.app_users set role='Master' where user_id='a1000000-0000-4000-8000-000000000001';
insert into auth.sessions(id,user_id,not_after) values('a2000000-0000-4000-8000-000000000001','a1000000-0000-4000-8000-000000000001',now()+interval '1 day');
set local role authenticated;
select sync_auto_test.login(1);
select j->>'secret' new_secret from public.sync_automation_configure(:'source','loja1',repeat('a',64),
 (now() at time zone 'America/Fortaleza')::date-731,(now() at time zone 'America/Fortaleza')::date,24,true,true,true) j \gset
set local role anon;
select sync_auto_test.denied(format('select public.sync_job_claim(%L,repeat(''a'',64))',:'secret'),'42501','rotação revoga segredo anterior');
reset role;
update public.integration_sources set active=false where id=:'source';
set local role anon;
select sync_auto_test.denied(format('select public.sync_job_claim(%L,repeat(''a'',64))',:'new_secret'),'42501','fonte desativada revoga job ainda habilitado');
reset role;
set local role authenticated;
select sync_auto_test.login(1);
select sync_auto_test.check(not (public.sync_automation_pause(:'source','loja1')->>'enabled')::boolean,'pausa funciona com fonte desativada sem Secrets ERP');
set local role anon;
select sync_auto_test.denied(format('select public.sync_job_claim(%L,repeat(''a'',64))',:'new_secret'),'42501','automação pausada nega execução');
set local role service_role;
select sync_auto_test.denied($q$select * from private.sync_automations$q$,'42501','service_role sem acesso direto ao segredo');
select sync_auto_test.denied($q$select private.sync_commit_core(null,null,null,null,true)$q$,'42501','service_role não recebe núcleo');
reset role;
-- Uma história curta termina e passa a alternar recente e reconciliação histórica.
update public.integration_sources set active=true where id=:'source';
set local role authenticated;
select sync_auto_test.login(1);
select j->>'automation_id' short_automation,j->>'secret' short_secret
 from public.sync_automation_configure(:'source','loja2',repeat('b',64),
 (now() at time zone 'America/Fortaleza')::date-19,(now() at time zone 'America/Fortaleza')::date,24,true,true) j \gset
reset role;
create function sync_auto_test.complete_empty(secret text,job jsonb) returns jsonb
language plpgsql as $$
declare run jsonb; control jsonb;
begin
 run:=public.sync_job_step(secret,(job->>'claim')::uuid,'begin',jsonb_build_object(
  'p_source_id',job->>'source_id','p_store_id',job->>'store_id','p_request_key',job->>'request_key',
  'p_period_start',job->>'start','p_period_end',job->>'end','p_expected_rows',0,'p_expected_quantity','0','p_content_hash',repeat('b',64)));
 control:=jsonb_build_object('p_run_id',run->>'id','p_claim_token',run->>'claim_token');
 perform public.sync_job_step(secret,(job->>'claim')::uuid,'preview',control);
 perform public.sync_job_step(secret,(job->>'claim')::uuid,'commit',control||'{"p_coverage_confirmed":false}'::jsonb);
 return public.sync_job_step(secret,(job->>'claim')::uuid,'finish','{}');
end $$;
set local role anon;
select sync_auto_test.complete_empty(:'short_secret',public.sync_job_claim(:'short_secret',repeat('b',64)));
reset role;
select sync_auto_test.check((select cursor_end is null from private.sync_automations where id=:'short_automation'),'última janela conclui carga histórica');
update private.sync_automations set next_due=now()-interval '1 minute' where id=:'short_automation';
set local role anon;
select public.sync_job_claim(:'short_secret',repeat('b',64))::text recent_job \gset
select sync_auto_test.check((:'recent_job'::jsonb->>'end')::date-(:'recent_job'::jsonb->>'start')::date=13,'janela recente inclui quatorze dias');
select sync_auto_test.complete_empty(:'short_secret',:'recent_job');
reset role;
update private.sync_automations set next_due=now()-interval '1 minute' where id=:'short_automation';
set local role anon;
select public.sync_job_claim(:'short_secret',repeat('b',64))::text historical_job \gset
select sync_auto_test.check((:'historical_job'::jsonb->>'end')::date-(:'historical_job'::jsonb->>'start')::date=19,'reconciliação histórica revisita período aprovado');
select sync_auto_test.complete_empty(:'short_secret',:'historical_job');
reset role;
select sync_auto_test.check((select recent_next and rotation_end=history_end from private.sync_automations where id=:'short_automation'),'fim da rotação volta ao recente sem excluir histórico');
-- Pausa maior que quatorze dias não cria buraco entre histórico e dados atuais.
update private.sync_automations set history_start=(now() at time zone 'America/Fortaleza')::date-100,
 history_end=(now() at time zone 'America/Fortaleza')::date-80,recent_through=(now() at time zone 'America/Fortaleza')::date-80,
 rotation_end=(now() at time zone 'America/Fortaleza')::date-80,recent_next=true,next_due=now()-interval '1 minute'
 where id=:'short_automation';
set local role anon;
select public.sync_job_claim(:'short_secret',repeat('b',64))::text catchup_job \gset
select sync_auto_test.check((:'catchup_job'::jsonb->>'start')::date=(now() at time zone 'America/Fortaleza')::date-93,
 'retomada começa antes da última marca preservando sobreposição');
select sync_auto_test.check((:'catchup_job'::jsonb->>'end')::date=(now() at time zone 'America/Fortaleza')::date-64,
 'retomada avança somente trinta dias por concessão');
select sync_auto_test.complete_empty(:'short_secret',:'catchup_job');
reset role;
select sync_auto_test.check((select recent_next and recent_through=(now() at time zone 'America/Fortaleza')::date-64
 from private.sync_automations where id=:'short_automation'),'marca recente só avança após finish e continua recuperação');
update private.sync_automations set next_due=now()-interval '1 minute' where id=:'short_automation';
set local role anon;
select public.sync_job_claim(:'short_secret',repeat('b',64))::text catchup_next \gset
select sync_auto_test.check((:'catchup_next'::jsonb->>'start')::date<=(:'catchup_job'::jsonb->>'end')::date,
 'próxima janela de retomada não deixa lacuna');
reset role;
-- Cota de execuções é verificada antes de autorizar qualquer consulta ao ERP.
insert into public.sync_runs(organization_id,source_id,store_id,author_id,request_key,content_hash,status,
 period_start,period_end,expected_rows,expected_quantity,finished_at)
 select 'a3000000-0000-4000-8000-000000000001',:'source','loja2','a1000000-0000-4000-8000-000000000001',
 gen_random_uuid(),repeat('e',64),'completed',current_date-10,current_date-9,0,0,now()
 from generate_series(1,1000-(select count(*)::int from public.sync_runs where organization_id='a3000000-0000-4000-8000-000000000001'));
update private.sync_automations set next_due=now()-interval '1 minute',lease_until=now()-interval '1 minute'
 where id=:'short_automation';
set local role anon;
select sync_auto_test.check(public.sync_job_claim(:'short_secret',repeat('b',64)) is null,'limite impede concessão e leitura externa');
reset role;
select sync_auto_test.check((select not enabled and last_error_code='RUN_LIMIT' from private.sync_automations where id=:'short_automation'),
 'capacidade esgotada pausa com motivo visível');
select sync_auto_test.check((select count(*)=1000 from public.sync_runs where organization_id='a3000000-0000-4000-8000-000000000001'),
 'limite não apaga histórico automaticamente');
select sync_auto_test.check((select count(*)=0 from public.inventory_imports where organization_id='a3000000-0000-4000-8000-000000000001'),'automação não troca catálogo nem saldo');
select sync_auto_test.check((select relrowsecurity from pg_class where oid='private.sync_automations'::regclass),'RLS habilitada na configuração privada');
rollback;
