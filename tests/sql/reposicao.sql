-- Testes exclusivamente em banco descartável com dados fictícios.
\set ON_ERROR_STOP on
begin;
create schema reposicao_test;
grant usage on schema reposicao_test to authenticated;
create function reposicao_test.check(ok boolean,nome text) returns void language plpgsql as $$
begin
  if ok is distinct from true then raise exception 'FALHOU: %',nome; end if;
  raise notice 'APROVADO REPOSIÇÃO: %',nome;
end $$;
create function reposicao_test.denied(comando text,estado text,nome text) returns void language plpgsql as $$
declare recebido text;
begin
  begin execute comando; exception when others then
    get stacked diagnostics recebido=returned_sqlstate;
    if recebido=estado then raise notice 'APROVADO REPOSIÇÃO: %',nome; return; end if;
    raise exception 'FALHOU: %, estado %',nome,recebido;
  end;
  raise exception 'FALHOU: %',nome;
end $$;
create function reposicao_test.login(n integer) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',jsonb_build_object(
    'sub','a1000000-0000-4000-8000-'||lpad(n::text,12,'0'),
    'session_id','a2000000-0000-4000-8000-'||lpad(n::text,12,'0'),
    'role','authenticated','exp',extract(epoch from now())+3600)::text,true);
end $$;
insert into auth.users(id,email,email_confirmed_at)
select ('a1000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,
  'reposicao'||n||'@example.invalid',now() from generate_series(1,3) n;
insert into auth.sessions(id,user_id,not_after)
select ('a2000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,
  ('a1000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,now()+interval '1 day' from generate_series(1,3) n;
select private.bootstrap_master('a1000000-0000-4000-8000-000000000001','Master teste','Estoque fictício');
insert into public.organizations(id,name) values('a3000000-0000-4000-8000-000000000003','Outra organização');
insert into public.app_users(user_id,organization_id,display_name,role,active)
values('a1000000-0000-4000-8000-000000000003','a3000000-0000-4000-8000-000000000003','Outro Master','Master',true);
set local role authenticated;
select reposicao_test.login(1);
select public.admin_set_user('a1000000-0000-4000-8000-000000000002','Visualizador teste','Visualizador',true,'{}');
select public.import_inventory('[{"codigo":"001","nome":"Produto fictício","fornecedor":"Indústria","estoque":10,
  "prazo_entrega_dias":7,"estoque_seguranca":2,"pedidos_pendentes":0,"reservas":0,"cobertura_alvo_dias":30}]',
  '[]','2020-01-31','2020-01-01','2020-01-31');
select reposicao_test.check((public.replenishment_rules()->'configuration'->>'reference_boxes')::int=80,
  '80 é referência inicial e não capacidade máxima');
select public.save_replenishment_packaging('001',current_date,6,0,1,null,'',null)->>'id' as first_id \gset
select reposicao_test.check((public.replenishment_rules()->'rules'->0->>'units_per_box')::int=6,'embalagem vigente por SKU');
select reposicao_test.denied($q$select public.save_replenishment_packaging('001',current_date,12,0,1,null,'',null)$q$,
  '40001','versão concorrente não é sobrescrita');
select reposicao_test.denied($q$select public.save_replenishment_packaging('999',current_date,6,0,1,null,'',null)$q$,
  '22023','SKU desconhecido é recusado');
select reposicao_test.denied($q$select public.save_replenishment_packaging('001',current_date,0,0,1,null,'',null)$q$,
  '22023','caixa com zero unidades é recusada');
select public.save_replenishment_proposal(14,current_date,jsonb_build_array(jsonb_build_object(
  'sku','001','units',12,'boxes',2,'packaging_id',:'first_id')));
select public.save_replenishment_packaging('001',current_date,12,0,1,null,'nova',:'first_id'::uuid)->>'id' as second_id \gset
select reposicao_test.check((public.list_replenishment_proposals()->0->'items'->0->>'units_per_box')::int=6,
  'proposta anterior preserva conversão original');
select reposicao_test.denied(format($q$select public.save_replenishment_proposal(14,current_date,
  '[{"sku":"001","units":12,"boxes":2,"packaging_id":"%s"}]')$q$,:'first_id'),
  '40001','proposta antiga não pode confirmar embalagem alterada');
select reposicao_test.denied($q$select * from public.replenishment_packaging_versions$q$,'42501','dados brutos não são expostos');
select reposicao_test.login(2);
select reposicao_test.denied($q$select public.replenishment_rules()$q$,'42501','perfil sem acesso não consulta regras');
select reposicao_test.denied($q$select public.save_replenishment_configuration(14,21,80)$q$,'42501','perfil sem permissão não altera regras');
select reposicao_test.login(3);
select reposicao_test.check(jsonb_array_length(public.replenishment_rules()->'rules')=0,'embalagens isoladas por organização');
rollback;
