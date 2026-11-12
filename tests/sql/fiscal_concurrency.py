"""Duas conexões reais disputam confirmação e execução da mesma solicitação DEMO."""

import json
import os
import uuid

from concorrencia import aguardar, iniciar, literal, psql


def executar():
    """Exige banco descartável, observa bloqueios e remove somente sua fixture."""
    if os.environ.get('GERENTE_DISPOSABLE_DATABASE') != '1' or psql(
        "select count(*)=5 from information_schema.columns where table_schema='auth' and table_name='users'") != 't':
        raise RuntimeError('Teste exclusivo do PostgreSQL descartável.')
    org, user, session = [str(uuid.uuid4()) for _ in range(3)]
    tag = 'fiscal_' + uuid.uuid4().hex[:12]
    processes = []
    try:
        psql(f"""begin;
          insert into auth.users(id,email,email_confirmed_at) values({literal(user)},'{tag}@example.invalid',now());
          insert into auth.sessions(id,user_id,not_after) values({literal(session)},{literal(user)},now()+interval '1 hour');
          insert into public.organizations(id,name) values({literal(org)},'Fiscal DEMO concorrente');
          insert into public.app_users(user_id,organization_id,display_name,role,active) values({literal(user)},{literal(org)},'Master DEMO','Master',true);
          commit;""")
        claims = literal(json.dumps({'sub': user, 'session_id': session, 'role': 'authenticated'}))
        login = f"set local role authenticated; select set_config('request.jwt.claims',({claims}::jsonb||jsonb_build_object('exp',extract(epoch from now())+3600))::text,true);"
        preview = psql(f"begin; {login} select public.fiscal_preview('1001','{{\"modalidade_frete\":1,\"tipo_contribuinte\":9}}')->>'id'; commit;").splitlines()[-1]

        def pair(operation, suffix):
            """Segura o primeiro commit e prova que o segundo espera o lock do banco."""
            barrier = tag + suffix
            controller = iniciar(f"set application_name={literal(barrier+'_control')}; begin; select pg_advisory_xact_lock(hashtextextended({literal(barrier)},0));\n")
            processes.append(controller)
            aguardar(f"select exists(select 1 from pg_stat_activity where application_name={literal(barrier+'_control')} and state='idle in transaction')",'controlador fiscal')
            first = iniciar(f"set application_name={literal(barrier+'_a')}; begin; {login} {operation}; select pg_advisory_xact_lock(hashtextextended({literal(barrier)},0)); commit;")
            processes.append(first)
            aguardar(f"select exists(select 1 from pg_stat_activity where application_name={literal(barrier+'_a')} and wait_event='advisory')",'primeiro commit fiscal')
            second = iniciar(f"set application_name={literal(barrier+'_b')}; begin; {login} {operation}; commit;")
            processes.append(second)
            aguardar(f"select exists(select 1 from pg_stat_activity where application_name={literal(barrier+'_b')} and wait_event_type='Lock')",'segunda solicitação serializada')
            controller.stdin.write('commit;\n')
            controller.stdin.flush()
            controller.communicate(timeout=20)
            results = []
            for process in (first, second):
                output, error = process.communicate(timeout=20)
                assert process.returncode == 0, error
                results.append(next(json.loads(line) for line in output.splitlines() if line.startswith('{') and 'state' in json.loads(line)))
            return results

        first, second = pair(f'select public.fiscal_confirm({literal(preview)},true,false)', '_confirm')
        assert first['id'] == second['id'] and not first['duplicate'] and second['duplicate']
        request = first['id']
        first, second = pair(f'select public.fiscal_begin({literal(request)})', '_execute')
        assert first.get('claim') and not second.get('claim') and second['busy']
        assert psql(f'select count(*) from public.fiscal_requests where organization_id={literal(org)}') == '1'
        assert psql(f"select count(*) from public.fiscal_events where organization_id={literal(org)} and event_code='demo_registration_started'") == '1'
        print('APROVADO FISCAL: concorrência real mantém uma solicitação e uma única concessão de execução.')
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
        psql(f"""begin;
          delete from public.fiscal_events where organization_id={literal(org)};
          delete from public.fiscal_requests where organization_id={literal(org)};
          delete from public.fiscal_previews where organization_id={literal(org)};
          delete from public.app_users where user_id={literal(user)};
          delete from auth.sessions where user_id={literal(user)};
          delete from auth.users where id={literal(user)};
          delete from public.organizations where id={literal(org)};
          commit;""")


if __name__ == '__main__':
    executar()
