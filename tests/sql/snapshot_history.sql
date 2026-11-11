-- Exclusivo do PostgreSQL descartável com Auth mínimo, nunca do Supabase real.
\set ON_ERROR_STOP on
begin;
do $$begin
  if (select count(*) from information_schema.columns where table_schema='auth' and table_name='users')<>5 then
    raise exception 'Fixture Auth descartável exigida.';
  end if;
end $$;
create schema snapshot_test;
grant usage on schema snapshot_test to authenticated;
create function snapshot_test.check(p_ok boolean,p_name text) returns void language plpgsql as $$
begin
  if p_ok is distinct from true then raise exception 'FALHOU: %',p_name; end if;
  raise notice 'APROVADO: %',p_name;
end $$;
create function snapshot_test.products(p_stock integer) returns jsonb language sql immutable as $$
select jsonb_build_array(jsonb_build_object('codigo','001','nome','Produto fictício','fornecedor','Fonte fictícia',
'estoque',p_stock,'prazo_entrega_dias',7,'estoque_seguranca',2,'pedidos_pendentes',0,'reservas',0,'cobertura_alvo_dias',30))
$$;
insert into auth.users(id,email,email_confirmed_at) values
 ('80000000-0000-4000-8000-000000000001','snapshots-master@example.invalid',now()),
 ('80000000-0000-4000-8000-000000000002','snapshots-visualizador@example.invalid',now());
insert into auth.sessions(id,user_id,not_after) values
 ('81000000-0000-4000-8000-000000000001','80000000-0000-4000-8000-000000000001',now()+interval '1 hour'),
 ('81000000-0000-4000-8000-000000000002','80000000-0000-4000-8000-000000000002',now()+interval '1 hour');
insert into public.organizations(id,name) values
 ('82000000-0000-4000-8000-000000000001','Organização fictícia snapshots');
insert into public.app_users(user_id,organization_id,display_name,role,active) values
 ('80000000-0000-4000-8000-000000000001','82000000-0000-4000-8000-000000000001','Master fictício','Master',true),
 ('80000000-0000-4000-8000-000000000002','82000000-0000-4000-8000-000000000001','Visualizador fictício','Visualizador',true);
create temp table expected_snapshot_history(payload jsonb);
grant all on expected_snapshot_history to authenticated;
set local role authenticated;
select set_config('request.jwt.claims',jsonb_build_object('sub','80000000-0000-4000-8000-000000000001',
  'session_id','81000000-0000-4000-8000-000000000001','role','authenticated','exp',extract(epoch from now())+3600)::text,true);
select public.import_inventory(snapshot_test.products(20),'[]','2020-01-15','2020-01-01','2020-01-15');
select public.import_inventory(snapshot_test.products(10),'[{"codigo":"001","data":"2020-01-10","quantidade":5}]',
 '2020-01-31','2020-01-01','2020-01-31');
insert into expected_snapshot_history values(public.load_inventory('reports.view','2020-01-01'));
-- Reproduz exatamente o comentário do PR #5: mesma data, saldo e abrangência distintos.
select public.import_inventory(snapshot_test.products(99),'[{"codigo":"001","data":"2020-01-25","quantidade":4}]',
 '2020-01-31','2020-01-20','2020-01-31');
select snapshot_test.check(
 (select value->>'estoque'='10' from jsonb_array_elements(public.load_inventory('reports.view','2020-01-01')->'snapshots')
  where value->>'data'='2020-01-31'),'fechamento preserva fotografia do próprio lote após mesma data com outro saldo');
select snapshot_test.check(public.load_inventory('inventory.view')->'products'->0->>'estoque'='99',
 'referência atual conserva importação posterior');
select snapshot_test.check((public.load_inventory('reports.view','2020-01-01')->'products')=(select payload->'products' from expected_snapshot_history),
 'fechamento antigo preserva catálogo original');
select snapshot_test.check((public.load_inventory('reports.view','2020-01-01')->'sales')=(select payload->'sales' from expected_snapshot_history),
 'período sobreposto preserva vendas originais do fechamento');
-- Uma fotografia retroativa não pode alterar os dias elegíveis de lote já fechado.
select public.import_inventory(snapshot_test.products(0),'[]','2020-01-15','2020-01-01','2020-01-15');
select snapshot_test.check((public.load_inventory('reports.view','2020-01-01')->'snapshots')=(select payload->'snapshots' from expected_snapshot_history),
 'fotografia retroativa não altera histórico de disponibilidade já fechado');
select snapshot_test.check((public.import_inventory(snapshot_test.products(99),
 '[{"codigo":"001","data":"2020-01-25","quantidade":4}]','2020-01-31','2020-01-20','2020-01-31')->>'reused')::boolean,
 'reimportação idêntica reutiliza lote');
select snapshot_test.check((select count(*)=4 from public.inventory_snapshot_versions),'reimportação idêntica não duplica versões');
select public.import_inventory(snapshot_test.products(30),'[{"codigo":"001","data":"2020-02-15","quantidade":7}]',
 '2020-02-29','2020-01-01','2020-02-29');
select snapshot_test.check((public.load_inventory('reports.view','2020-01-01')->'snapshots')=(select payload->'snapshots' from expected_snapshot_history),
 'importação de período posterior preserva relatório antigo');
select snapshot_test.check(
 (select value->>'estoque'='0' from jsonb_array_elements(public.load_inventory('reports.view','2020-02-01')->'snapshots')
  where value->>'data'='2020-01-15'),'novo fechamento usa a revisão conhecida na própria ingestão');
-- O cancelamento de uma transação desfaz lote, versão, projeção e referência.
savepoint cancelled_import;
select public.import_inventory(snapshot_test.products(777),'[]','2020-03-31','2020-03-01','2020-03-31');
rollback to cancelled_import;
select snapshot_test.check(public.load_inventory('inventory.view')->'products'->0->>'estoque'='30',
 'importação cancelada por rollback não substitui referência');
select snapshot_test.check((select count(*)=5 from public.inventory_snapshot_versions),
 'cancelamento não deixa fotografia órfã');
select snapshot_test.check(not has_table_privilege('authenticated','public.inventory_snapshot_versions','INSERT,UPDATE,DELETE'),
 'API não permite mutação direta das versões');
select snapshot_test.check(not has_function_privilege('authenticated','private.capture_inventory_snapshot()','execute'),
 'captura privilegiada não é chamável diretamente');
select set_config('request.jwt.claims',jsonb_build_object('sub','80000000-0000-4000-8000-000000000002',
  'session_id','81000000-0000-4000-8000-000000000002','role','authenticated','exp',extract(epoch from now())+3600)::text,true);
select snapshot_test.check((select count(*)=0 from public.inventory_snapshot_versions),'Visualizador não enumera versões por acesso direto');
reset role;
select snapshot_test.check((select count(*)=5 from public.inventory_snapshot_versions where organization_id='82000000-0000-4000-8000-000000000001'),
 'verificação RLS não remove versões');
rollback;
