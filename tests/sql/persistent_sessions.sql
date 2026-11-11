-- Exclusivo de banco descartável, sem dados ou credenciais de produção.
\set ON_ERROR_STOP on
begin;
create schema persistent_test;
grant usage on schema persistent_test to authenticated,anon;
create function persistent_test.check(ok boolean,label text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception 'FALHOU: %',label; end if;
raise notice 'APROVADO SESSÃO: %',label; end $$;
create function persistent_test.denied(statement text,state text,label text) returns void language plpgsql as $$
declare actual text;
begin begin execute statement; exception when others then get stacked diagnostics actual=returned_sqlstate;
 if actual=state then raise notice 'APROVADO SESSÃO: %',label; return; end if;
 raise exception 'FALHOU: % esperado %, recebido %',label,state,actual;
end; raise exception 'FALHOU: % permitiu acesso',label; end $$;
create function persistent_test.login(n integer) returns void language plpgsql as $$
begin perform set_config('request.jwt.claims',jsonb_build_object('sub','d1000000-0000-4000-8000-'||lpad(n::text,12,'0'),
 'session_id','d2000000-0000-4000-8000-'||lpad(n::text,12,'0'),'role','authenticated','exp',extract(epoch from now())+3600)::text,true); end $$;
insert into auth.users(id,email,email_confirmed_at)
select ('d1000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,'sessao'||n||'@example.invalid',now() from generate_series(1,3) n;
insert into auth.sessions(id,user_id,not_after)
select ('d2000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,
 ('d1000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,now()+interval '1 day' from generate_series(1,3) n;
insert into public.organizations(id,name) values('d3000000-0000-4000-8000-000000000001','Sessão DEMO1'),('d3000000-0000-4000-8000-000000000002','Sessão DEMO2');
insert into public.app_users(user_id,organization_id,display_name,role,active) values
 ('d1000000-0000-4000-8000-000000000001','d3000000-0000-4000-8000-000000000001','Master DEMO','Master',true),
 ('d1000000-0000-4000-8000-000000000002','d3000000-0000-4000-8000-000000000001','Visualizador DEMO','Visualizador',true),
 ('d1000000-0000-4000-8000-000000000003','d3000000-0000-4000-8000-000000000002','Outro Master DEMO','Master',true);
select private.configure_backend_secret('d3000000-0000-4000-8000-000000000001',repeat('backend-ficticio-1',4));
select private.configure_backend_secret('d3000000-0000-4000-8000-000000000002',repeat('backend-ficticio-2',4));
set local role authenticated;
select persistent_test.login(1);
select public.persistent_register('d4000000-0000-4000-8000-000000000001',encode(sha256(convert_to(repeat('a',43),'UTF8')),'hex'),repeat('c',100),repeat('backend-ficticio-1',4));
select persistent_test.check(public.app_context(array['dashboard.view'])->'capabilities'->>'persistent_login'='true','contrato confirma permanência instalada');
select persistent_test.denied($q$select public.persistent_register('d4000000-0000-4000-8000-000000000009',repeat('a',64),repeat('c',100),repeat('backend-ficticio-2',4))$q$,'42501','backend de outra organização não registra sessão');
select persistent_test.denied($q$select * from private.persistent_sessions$q$,'42501','nenhum acesso direto aos tokens cifrados');
set local role anon;
select persistent_test.denied($q$select public.persistent_claim('d4000000-0000-4000-8000-000000000001',repeat('a',43),null)$q$,'42501','cookie sem backend autorizado não retoma sessão');
select persistent_test.denied($q$select public.persistent_claim('d4000000-0000-4000-8000-000000000001',repeat('b',43),repeat('backend-ficticio-1',4))$q$,'42501','validador forjado recusado');
select persistent_test.denied($q$select public.persistent_claim('d4000000-0000-4000-8000-000000000001',repeat('a',43),repeat('backend-ficticio-2',4))$q$,'42501','cookie não cruza organização');
select public.persistent_claim('d4000000-0000-4000-8000-000000000001',repeat('a',43),repeat('backend-ficticio-1',4))->>'claim' claim \gset
select persistent_test.check((public.persistent_claim('d4000000-0000-4000-8000-000000000001',repeat('a',43),repeat('backend-ficticio-1',4))->>'busy')::boolean,'duas abas não consomem o mesmo refresh token');
select persistent_test.denied(format('select public.persistent_finish(''d4000000-0000-4000-8000-000000000001'',%L,null,repeat(''backend-ficticio-1'',4))',:'claim'),'42501','finalizar exige JWT real da sessão retomada');
set local role authenticated;
select persistent_test.login(2);
select persistent_test.denied(format('select public.persistent_finish(''d4000000-0000-4000-8000-000000000001'',%L,null,repeat(''backend-ficticio-1'',4))',:'claim'),'42501','outro usuário não assume concessão');
select persistent_test.login(1);
select public.persistent_finish('d4000000-0000-4000-8000-000000000001',:'claim',repeat('d',100),repeat('backend-ficticio-1',4));
reset role;
select persistent_test.check((select encrypted_session=repeat('d',100) from private.persistent_sessions where selector='d4000000-0000-4000-8000-000000000001'),'rotação substitui conteúdo sem criar versão redundante');
select persistent_test.check((select expires_at<=created_at+interval '30 days' from private.persistent_sessions where selector='d4000000-0000-4000-8000-000000000001'),'retomada não prorroga prazo absoluto');
update private.persistent_sessions set expires_at=now()-interval '1 second' where selector='d4000000-0000-4000-8000-000000000001';
set local role anon;
select persistent_test.denied($q$select public.persistent_claim('d4000000-0000-4000-8000-000000000001',repeat('a',43),repeat('backend-ficticio-1',4))$q$,'42501','cookie vencido não retoma');
reset role;
update private.persistent_sessions set expires_at=now()+interval '1 day' where selector='d4000000-0000-4000-8000-000000000001';
update public.app_users set active=false where user_id='d1000000-0000-4000-8000-000000000001';
set local role anon;
select persistent_test.denied($q$select public.persistent_claim('d4000000-0000-4000-8000-000000000001',repeat('a',43),repeat('backend-ficticio-1',4))$q$,'42501','desativação invalida cookie');
reset role;
update public.app_users set active=true where user_id='d1000000-0000-4000-8000-000000000001';
set local role anon;
select persistent_test.denied($q$select public.persistent_claim('d4000000-0000-4000-8000-000000000001',repeat('a',43),repeat('backend-ficticio-1',4))$q$,'42501','reativação não ressuscita sessão antiga');
reset role;
delete from private.account_access_controls where user_id='d1000000-0000-4000-8000-000000000001';
insert into private.account_access_controls(user_id,pending,ready,expires_at) values('d1000000-0000-4000-8000-000000000001',true,true,now()+interval '1 day');
set local role anon;
select persistent_test.denied($q$select public.persistent_claim('d4000000-0000-4000-8000-000000000001',repeat('a',43),repeat('backend-ficticio-1',4))$q$,'42501','primeiro acesso pendente não usa cookie');
reset role;
delete from private.account_access_controls where user_id='d1000000-0000-4000-8000-000000000001';
set local role anon;
select public.persistent_forget('d4000000-0000-4000-8000-000000000001',repeat('a',43),repeat('backend-ficticio-1',4));
select persistent_test.denied($q$select public.persistent_claim('d4000000-0000-4000-8000-000000000001',repeat('a',43),repeat('backend-ficticio-1',4))$q$,'42501','Sair invalida referência opaca');
reset role;
select persistent_test.check((select relrowsecurity from pg_class where oid='private.persistent_sessions'::regclass),'RLS habilitada');
rollback;
