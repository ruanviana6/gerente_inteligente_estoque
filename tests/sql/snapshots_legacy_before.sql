-- Preparação EXCLUSIVA do teste de atualização, antes da migração de snapshots.
\set ON_ERROR_STOP on
begin;
do $$begin
  if (select count(*) from information_schema.columns where table_schema='auth' and table_name='users')<>5
    or to_regclass('public.inventory_snapshot_versions') is not null then
    raise exception 'Exigido esquema descartável anterior à migração de snapshots.';
  end if;
end $$;
insert into auth.users(id,email,email_confirmed_at) values
 ('83000000-0000-4000-8000-000000000001','legado-snapshots@example.invalid',now());
insert into auth.sessions(id,user_id,not_after) values
 ('84000000-0000-4000-8000-000000000001','83000000-0000-4000-8000-000000000001',now()+interval '1 hour');
insert into public.organizations(id,name) values
 ('85000000-0000-4000-8000-000000000001','Atualização fictícia de snapshots');
insert into public.app_users(user_id,organization_id,display_name,role,active) values
 ('83000000-0000-4000-8000-000000000001','85000000-0000-4000-8000-000000000001','Master fictício legado','Master',true);
set local role authenticated;
select set_config('request.jwt.claims',jsonb_build_object('sub','83000000-0000-4000-8000-000000000001',
 'session_id','84000000-0000-4000-8000-000000000001','role','authenticated','exp',extract(epoch from now())+3600)::text,true);
select public.import_inventory('[{"codigo":"001","nome":"Produto fictício legado","fornecedor":"Fictício",
 "estoque":10,"prazo_entrega_dias":7,"estoque_seguranca":2,"pedidos_pendentes":0,"reservas":0,"cobertura_alvo_dias":30}]',
 '[{"codigo":"001","data":"2020-01-15","quantidade":5}]','2020-01-31','2020-01-01','2020-01-31');
select public.import_inventory('[{"codigo":"001","nome":"Produto fictício legado","fornecedor":"Fictício",
 "estoque":99,"prazo_entrega_dias":7,"estoque_seguranca":2,"pedidos_pendentes":0,"reservas":0,"cobertura_alvo_dias":30}]',
 '[]','2020-01-31','2020-01-20','2020-01-31');
do $$begin
 if public.load_inventory('reports.view','2020-01-01')->'products'->0->>'estoque'<>'10'
  or public.load_inventory('reports.view','2020-01-01')->'snapshots'->0->>'estoque'<>'99' then
   raise exception 'Reprodução legada não atingiu a incoerência esperada.';
 end if;
 raise notice 'REPRODUZIDO: relatório legado tem saldo 10 e fotografia sobrescrita 99.';
end $$;
commit;
