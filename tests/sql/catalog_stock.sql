-- Somente dados fictícios e transação revertida. Nunca acessa o ERP.
\set ON_ERROR_STOP on
begin;
create schema catalog_test;
grant usage on schema catalog_test to authenticated;
create function catalog_test.check(ok boolean, nome text) returns void language plpgsql as $$
begin
  if ok is distinct from true then raise exception 'FALHOU: %',nome; end if;
  raise notice 'APROVADO CATALOGO: %',nome;
end $$;
create function catalog_test.denied(cmd text, code text, nome text) returns void
language plpgsql as $$
declare actual text;
begin
  begin execute cmd;
  exception when others then
    get stacked diagnostics actual=returned_sqlstate;
    if actual=code then raise notice 'APROVADO CATALOGO: %',nome; return; end if;
    raise exception 'FALHOU: %, esperado %, veio %',nome,code,actual;
  end;
  raise exception 'FALHOU: % aceitou escrita',nome;
end $$;
create function catalog_test.login(i integer) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',jsonb_build_object(
    'sub','81000000-0000-4000-8000-'||lpad(i::text,12,'0'),
    'session_id','82000000-0000-4000-8000-'||lpad(i::text,12,'0'),
    'role','authenticated','exp',extract(epoch from now())+3600
  )::text,true);
end $$;
create function catalog_test.rows() returns jsonb language sql as $$
  select jsonb_build_array(
    jsonb_build_object('id','00001','codigo_interno','TESTE-001','estoque','11',
      'variacoes_sem_id',1,'variacoes_sem_codigo',1)
  )
$$;
insert into auth.users(id,email,email_confirmed_at)
select ('81000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
 'catalog' || i || '@example.invalid',now() from generate_series(1,3) i;
insert into auth.sessions(id,user_id,not_after)
select ('82000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
 ('81000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
 now()+interval '1 day' from generate_series(1,3) i;
insert into public.organizations(id,name) values
 ('83000000-0000-4000-8000-000000000001','Catalogo org1'),
 ('83000000-0000-4000-8000-000000000002','Catalogo org2');
insert into public.app_users(user_id,organization_id,display_name,role,active) values
 ('81000000-0000-4000-8000-000000000001','83000000-0000-4000-8000-000000000001','Master1','Master',true),
 ('81000000-0000-4000-8000-000000000002','83000000-0000-4000-8000-000000000002','Master2','Master',true),
 ('81000000-0000-4000-8000-000000000003','83000000-0000-4000-8000-000000000001','Operador','Operador',true);
set local role authenticated;
select catalog_test.login(1);
select (public.integration_register_source('catalogo1','Fonte teste',array['loja1','loja2'])->>'id') source \gset
select catalog_test.denied(format(
 'select public.catalog_stock_commit(%L,%L,1,11,%L::jsonb,false)',
 :'source','loja1',catalog_test.rows()::text
),'22023','requer confirmação explícita');
select catalog_test.denied(format(
 'select public.catalog_stock_commit(%L,%L,2,11,%L::jsonb,true)',
 :'source','loja1',catalog_test.rows()::text
),'22023','não permite total diferente da contagem');
select catalog_test.denied(format(
 'select public.catalog_stock_commit(%L,%L,1,10,%L::jsonb,true)',
 :'source','loja1',catalog_test.rows()::text
),'22023','não permite soma de saldos divergente');
select catalog_test.denied(format(
 'select public.catalog_stock_commit(%L,%L,1,11,%L::jsonb,true)',
 :'source','loja1',(catalog_test.rows()->0||'{"cliente":"PRIVADO"}'::jsonb)::text
),'22023','não aceita dados pessoais extras');
select catalog_test.denied(format(
 'select public.catalog_stock_commit(%L,%L,1,11,%L::jsonb,true)',
 :'source','loja1',(catalog_test.rows()->0||'{"estoque":"-1"}'::jsonb)::text
),'22023','estoque negativo não pode virar saldo');
select catalog_test.denied(format(
 'select public.catalog_stock_commit(%L,%L,1,11,%L::jsonb,true)',
 :'source','loja3',catalog_test.rows()::text
),'42501','loja fora da fonte recusada');
select catalog_test.check(
 (public.catalog_stock_commit(:'source','loja1',1,11,catalog_test.rows(),true)->>'product_count')::int=1,
 'fotografia confirmada grava somente dados analíticos');
select catalog_test.check(
 (public.catalog_stock_commit(:'source','loja1',1,11,catalog_test.rows(),true)->>'reused')::boolean,
 'salvar novamente o mesmo lote é idempotente');
select catalog_test.check(
 jsonb_array_length(public.catalog_stock_history(:'source','loja1'))=1,
 'histórico da loja contém apenas um lote');
select catalog_test.denied($q$select * from public.catalog_stock_rows$q$,
 '42501','linhas brutas inacessíveis via REST');
select catalog_test.denied($q$delete from public.catalog_stock_batches$q$,
 '42501','exclusão direta bloqueada');
select catalog_test.denied(format(
 'select public.catalog_stock_commit(%L,%L,1,11,%L::jsonb,true)',
 :'source','loja1',jsonb_build_array(catalog_test.rows()->0,catalog_test.rows()->0)::text
),'22023','duplicação e contagem inconsistente rejeitadas');
select catalog_test.login(2);
select catalog_test.denied(format(
 'select public.catalog_stock_history(%L,%L)',:'source','loja1'
),'42501','outra organização não lê o histórico');
select catalog_test.denied(format(
 'select public.catalog_stock_commit(%L,%L,1,11,%L::jsonb,true)',
 :'source','loja1',catalog_test.rows()::text
),'42501','outra organização não escreve o catálogo');
select catalog_test.login(3);
select catalog_test.denied(format(
 'select public.catalog_stock_commit(%L,%L,1,11,%L::jsonb,true)',
 :'source','loja1',catalog_test.rows()::text
),'42501','Operador sem Master não confirma catálogo');
reset role;
select catalog_test.check((select count(*)=1 from public.catalog_stock_batches),'um lote no histórico interno');
select catalog_test.check((select count(*)=1 from public.catalog_stock_rows),'uma linha por produto');
select catalog_test.check((select count(*)=0 from public.inventory_imports),'motor atual não foi alterado');
select catalog_test.check((select count(*)=0 from public.inventory_current),'nenhum indicador atual promovido');
rollback;
