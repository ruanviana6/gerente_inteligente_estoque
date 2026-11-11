-- Exercita somente dados fictícios no PostgreSQL descartável e reverte tudo.
\set ON_ERROR_STOP on
begin;
create schema acesso_test;
grant usage on schema acesso_test to authenticated,anon;
create function acesso_test.check(ok boolean,nome text) returns void language plpgsql as $$
begin
  if ok is distinct from true then raise exception 'FALHOU: %',nome; end if;
  raise notice 'APROVADO ACESSO: %',nome;
end $$;
create function acesso_test.denied(comando text,nome text) returns void language plpgsql as $$
begin
  begin execute comando; exception when insufficient_privilege then
    raise notice 'APROVADO ACESSO: %',nome; return;
  end;
  raise exception 'FALHOU: %',nome;
end $$;
create function acesso_test.login(n integer) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',jsonb_build_object(
    'sub','91000000-0000-4000-8000-'||lpad(n::text,12,'0'),
    'session_id','92000000-0000-4000-8000-'||lpad(n::text,12,'0'),
    'role','authenticated','exp',extract(epoch from now())+3600)::text,true);
end
$$;
insert into auth.users(id,email,email_confirmed_at,encrypted_password)
select ('91000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,
  'acesso'||n||'@example.invalid',now(),'hash-inicial-ficticio-'||n from generate_series(1,4) n;
insert into auth.sessions(id,user_id,created_at,not_after)
select ('92000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,
  ('91000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,now(),now()+interval '1 day'
  from generate_series(1,4) n;
select private.bootstrap_master('91000000-0000-4000-8000-000000000001','Master teste','Organização teste');
insert into public.organizations(id,name) values('93000000-0000-4000-8000-000000000004','Outra organização');
insert into public.app_users(user_id,organization_id,display_name,role,active)
values('91000000-0000-4000-8000-000000000004','93000000-0000-4000-8000-000000000004','Outra conta','Operador',true);
set local role authenticated;
select acesso_test.login(1);
select public.admin_register_temporary_user('91000000-0000-4000-8000-000000000002','Pessoa teste','Operador','{}');
select acesso_test.denied($q$select public.admin_register_temporary_user('91000000-0000-4000-8000-000000000004','Outra','Operador','{}')$q$,
  'cadastro não captura usuário de outra organização');
select acesso_test.denied($q$select public.admin_begin_password_reset('91000000-0000-4000-8000-000000000001')$q$,
  'Master não redefine o próprio acesso pelo fluxo de funcionários');
select acesso_test.denied($q$select public.admin_begin_password_reset('91000000-0000-4000-8000-000000000004')$q$,
  'Master não redefine usuário de outra organização');
select acesso_test.login(2);
select acesso_test.check((public.password_gate_status()->>'required')::boolean,'primeiro acesso exige troca');
select acesso_test.denied($q$select public.app_context()$q$,'sessão temporária não recebe contexto ou permissões');
select acesso_test.denied($q$select public.finish_initial_password()$q$,'não libera sem troca real no Auth');
select acesso_test.denied($q$select * from private.account_access_controls$q$,'controle privado não é enumerável');
reset role;
update auth.users set encrypted_password='hash-trocado-ficticio' where id='91000000-0000-4000-8000-000000000002';
set local role authenticated;
select acesso_test.login(2);
select public.finish_initial_password();
select acesso_test.denied($q$select public.app_context()$q$,'sessão usada na troca é revogada');
reset role;
update auth.sessions set created_at=clock_timestamp() where user_id='91000000-0000-4000-8000-000000000002';
set local role authenticated;
select acesso_test.login(2);
select acesso_test.check(public.app_context()->'profile'->>'role'='Operador','novo login recebe somente papel autorizado');
select acesso_test.denied($q$select public.admin_begin_password_reset('91000000-0000-4000-8000-000000000001')$q$,
  'funcionário não redefine o Master');
select acesso_test.login(1);
select set_config('acesso.operacao',public.admin_begin_password_reset(
  '91000000-0000-4000-8000-000000000002')->>'operation_id',true);
select acesso_test.denied($q$select public.admin_finish_password_reset(
  '91000000-0000-4000-8000-000000000002',current_setting('acesso.operacao')::uuid)$q$,
  'preparação não conclui sem atualização da senha no Auth');
select acesso_test.login(2);
select acesso_test.denied($q$select public.password_gate_status()$q$,'sessão antiga não prepara a própria redefinição');
reset role;
update auth.users set encrypted_password='hash-temporario-redefinido' where id='91000000-0000-4000-8000-000000000002';
set local role authenticated;
select acesso_test.login(1);
select public.admin_finish_password_reset('91000000-0000-4000-8000-000000000002',current_setting('acesso.operacao')::uuid);
reset role;
update auth.sessions set created_at=clock_timestamp() where user_id='91000000-0000-4000-8000-000000000002';
set local role authenticated;
select acesso_test.login(2);
select acesso_test.check((public.password_gate_status()->>'required')::boolean,'redefinição exige nova senha pessoal');
select acesso_test.denied($q$select public.app_context()$q$,'senha temporária redefinida não libera dados');
select acesso_test.denied($q$select public.finish_initial_password()$q$,'senha redefinida precisa ser trocada pelo funcionário');
reset role;
update auth.users set encrypted_password='hash-pessoal-redefinido' where id='91000000-0000-4000-8000-000000000002';
set local role authenticated;
select acesso_test.login(2);
select public.finish_initial_password();
reset role;
update auth.sessions set created_at=clock_timestamp() where user_id='91000000-0000-4000-8000-000000000002';
set local role authenticated;
select acesso_test.login(1);
select public.admin_set_user('91000000-0000-4000-8000-000000000002','Pessoa teste','Operador',false,'{}');
select public.admin_set_user('91000000-0000-4000-8000-000000000002','Pessoa teste','Operador',true,'{}');
select acesso_test.login(2);
select acesso_test.denied($q$select public.app_context()$q$,'reativar conta não reativa sessão anterior');
rollback;
