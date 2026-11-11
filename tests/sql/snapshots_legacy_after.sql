-- Verifica backfill não destrutivo após a migração e remove só a fixture criada.
\set ON_ERROR_STOP on
begin;
do $$begin
 if (select count(*) from information_schema.columns where table_schema='auth' and table_name='users')<>4 then
   raise exception 'Fixture Auth descartável exigida.';
 end if;
 if (select count(*) from public.inventory_imports where organization_id='85000000-0000-4000-8000-000000000001')<>2
  or (select count(*) from public.inventory_products where organization_id='85000000-0000-4000-8000-000000000001')<>2
  or (select count(*) from public.inventory_sales where organization_id='85000000-0000-4000-8000-000000000001')<>1
  or (select estoque from public.inventory_snapshots where organization_id='85000000-0000-4000-8000-000000000001')<>99 then
   raise exception 'Dados legados não foram preservados integralmente.';
 end if;
 if (select count(*) from public.inventory_snapshot_versions where organization_id='85000000-0000-4000-8000-000000000001')<>2
  or (select count(distinct estoque) from public.inventory_snapshot_versions where organization_id='85000000-0000-4000-8000-000000000001')<>2 then
   raise exception 'Backfill não recuperou ambas as versões.';
 end if;
 if private.inventory_payload('85000000-0000-4000-8000-000000000001','83000000-0000-4000-8000-000000000001','2020-01-01')->'snapshots'->0->>'estoque'<>'10' then
   raise exception 'Fechamento legado permaneceu inconsistente após correção.';
 end if;
 raise notice 'APROVADO: atualização recupera fotografia original e mantém todos os dados legados.';
end $$;
delete from public.inventory_current where organization_id='85000000-0000-4000-8000-000000000001';
delete from public.inventory_snapshot_versions where organization_id='85000000-0000-4000-8000-000000000001';
delete from public.inventory_costs where organization_id='85000000-0000-4000-8000-000000000001';
delete from public.inventory_sales where organization_id='85000000-0000-4000-8000-000000000001';
delete from public.inventory_snapshots where organization_id='85000000-0000-4000-8000-000000000001';
delete from public.inventory_products where organization_id='85000000-0000-4000-8000-000000000001';
delete from public.inventory_imports where organization_id='85000000-0000-4000-8000-000000000001';
delete from public.audit_events where organization_id='85000000-0000-4000-8000-000000000001';
delete from public.app_users where organization_id='85000000-0000-4000-8000-000000000001';
delete from auth.sessions where user_id='83000000-0000-4000-8000-000000000001';
delete from auth.users where id='83000000-0000-4000-8000-000000000001';
delete from public.organizations where id='85000000-0000-4000-8000-000000000001';
commit;
