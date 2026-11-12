-- Exclusivo do banco descartável. Não importa dados ou credenciais empresariais.
\set ON_ERROR_STOP on
begin;
create schema dashboard_test;
grant usage on schema dashboard_test to authenticated,anon;
create function dashboard_test.check(ok boolean,label text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'FALHOU DASHBOARD: %',label; end if;
raise notice 'APROVADO DASHBOARD: %',label; end $$;
create function dashboard_test.denied(statement text,state text,label text) returns void language plpgsql as $$
declare actual text;
begin begin execute statement; exception when others then get stacked diagnostics actual=returned_sqlstate;
 if actual=state then raise notice 'APROVADO DASHBOARD: %',label; return; end if;
 raise exception 'FALHOU DASHBOARD: % esperado %, recebido %',label,state,actual;
end; raise exception 'FALHOU DASHBOARD: % permitiu operação',label; end $$;
create function dashboard_test.login(i int) returns void language plpgsql as $$
begin perform set_config('request.jwt.claims',jsonb_build_object('sub','e1000000-0000-4000-8000-'||lpad(i::text,12,'0'),
 'session_id','e2000000-0000-4000-8000-'||lpad(i::text,12,'0'),'role','authenticated','exp',extract(epoch from now())+3600)::text,true); end $$;
create function dashboard_test.summary() returns jsonb language sql as $$
select '{"schema":1,"mode":"demo","inventory":{"products":1,"stockouts":0,"risk":1,"review":1,"excess":0,"suggested":1},
"replenishment":{"boxes":null,"unknown_packaging":1,"primary":14,"alternative":21,"reference_boxes":80,"next_request":null,"cycles":[{"days":14,"boxes":null},{"days":21,"boxes":null}]},
"sales":{"current_30":null,"previous_30":null,"change_pct":null,"daily":[]},"priorities":[{"kind":"production","count":1,"priority":1}],"finance":{"capital":100,"partial":false}}'::jsonb $$;
insert into auth.users(id,email,email_confirmed_at)
select ('e1000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,'dashboard'||i||'@example.invalid',now() from generate_series(1,3) i;
insert into auth.sessions(id,user_id,not_after)
select ('e2000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
 ('e1000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,now()+interval '1 day' from generate_series(1,3) i;
insert into public.organizations(id,name) values('e3000000-0000-4000-8000-000000000001','Dashboard fictício1'),('e3000000-0000-4000-8000-000000000002','Dashboard fictício2');
insert into public.app_users(user_id,organization_id,display_name,role,active)
select ('e1000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
 case when i=3 then 'e3000000-0000-4000-8000-000000000002'::uuid else 'e3000000-0000-4000-8000-000000000001'::uuid end,
 'Pessoa fictícia'||i,case when i=1 then 'Master' else 'Visualizador' end,true from generate_series(1,3) i;
set local role anon;
select dashboard_test.denied('select public.dashboard_payload()','42501','anônimo sem resumo');
set local role authenticated;
select dashboard_test.login(1);
select dashboard_test.check((public.dashboard_payload()->>'missing')::boolean,'sem histórico não inventa indicadores');
select public.import_inventory('[{"codigo":"001","nome":"Produto DEMO","fornecedor":"Fornecedor fictício","estoque":10,"prazo_entrega_dias":7,"estoque_seguranca":2,"pedidos_pendentes":0,"reservas":0,"cobertura_alvo_dias":30}]','[]','2020-01-31','2020-01-01','2020-01-31');
select public.dashboard_source()->>'batch_id' batch,public.dashboard_source()->>'revision' revision \gset
select public.dashboard_publish(:'batch',:'revision',dashboard_test.summary());
select dashboard_test.check(not (public.dashboard_payload()->>'stale')::boolean,'revisão coerente');
select dashboard_test.check(public.dashboard_payload()->'summary'->'finance'->>'capital'='100','Master financeiro autorizado');
select dashboard_test.denied(format('select public.dashboard_publish(%L,%L,%L)',:'batch','revisao-antiga',dashboard_test.summary()),'40001','resumo de revisão antiga recusado');
select dashboard_test.denied(format('select public.dashboard_publish(%L,%L,%L)',:'batch',:'revision',dashboard_test.summary()||'{"cliente":"PII indevida"}'::jsonb),'22023','campo identificável recusado');
select dashboard_test.denied(format('select public.dashboard_publish(%L,%L,%L)',:'batch',:'revision',jsonb_set(dashboard_test.summary(),'{inventory}','{}')),'22023','campos obrigatórios ausentes recusados');
select dashboard_test.login(2);
select dashboard_test.check(not (public.dashboard_payload()->'summary' ?| array['finance','sales','replenishment']),'financeiro e módulos não concedidos filtrados no banco');
select dashboard_test.check(public.dashboard_payload()->'summary'->'priorities'='[]','prioridade de reposição não concedida removida');
select dashboard_test.denied('select public.dashboard_source()','42501','fonte de recálculo exclusiva Master');
select dashboard_test.denied(format('select public.dashboard_publish(%L,%L,%L)',:'batch',:'revision',dashboard_test.summary()),'42501','nãoMaster não publica resumo');
select dashboard_test.denied('select * from public.dashboard_snapshots','42501','cache bruto não é enumerável');
select dashboard_test.login(3);
select dashboard_test.check(public.dashboard_payload()->'summary'='null','outra organização não recebe cache');
select dashboard_test.login(1);
select public.import_inventory('[{"codigo":"001","nome":"Produto DEMO","fornecedor":"Fornecedor fictício","estoque":12,"prazo_entrega_dias":7,"estoque_seguranca":2,"pedidos_pendentes":0,"reservas":0,"cobertura_alvo_dias":30}]','[]','2020-02-29','2020-01-01','2020-02-29');
select dashboard_test.check((public.dashboard_payload()->>'stale')::boolean,'novo lote sinaliza desatualização sem reler histórico bruto');
select dashboard_test.check(public.dashboard_payload()->'metadata'->>'snapshot_date'='2020-01-31','resumo antigo preserva data original');
reset role;
select dashboard_test.check((select count(*)=1 from public.dashboard_snapshots),'uma versão recomputável por organização');
rollback;
