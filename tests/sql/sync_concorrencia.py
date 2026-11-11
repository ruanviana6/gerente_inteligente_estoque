"""Concorrência real da sincronização em PostgreSQL descartável.

Disputa criação de execução, staging repetido e confirmação simultânea. As
barreiras usam locks e pg_stat_activity, sem presumir tempos de execução.
Nunca executar no projeto Supabase: o fixture Auth mínimo é obrigatório.
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid

from concorrencia import aguardar, iniciar, literal, psql


def executar() -> None:
    """Confere exclusão mútua, páginas idempotentes e um único commit analítico."""
    if os.environ.get('GERENTE_DISPOSABLE_DATABASE') != '1':
        raise RuntimeError('Permitido somente no PostgreSQL descartável explicitamente selecionado.')
    if psql("select count(*)=5 from information_schema.columns where table_schema='auth' and table_name='users'") != 't':
        raise RuntimeError('O schema Auth não corresponde ao fixture descartável.')
    org, user, session, source, request_a, request_b = [str(uuid.uuid4()) for _ in range(6)]
    tag = 'sync_corrida_' + uuid.uuid4().hex[:12]
    processes: list[subprocess.Popen[str]] = []
    ready = False
    claims = json.dumps({'sub': user, 'session_id': session, 'role': 'authenticated'})

    def call(statement: str, suffix: str) -> str:
        """Monta uma sessão com identidade fictícia e autorização normal."""
        return f"""
        set application_name={literal(tag + suffix)};
        begin;
        set local role authenticated;
        select set_config('request.jwt.claims',({literal(claims)}::jsonb ||
          jsonb_build_object('exp',extract(epoch from now())+3600))::text,true);
        {statement};
        commit;
        """

    def barrier(statement: str, suffix: str) -> subprocess.Popen[str]:
        """Mantém um lock até que as duas operações concorrentes estejam esperando."""
        process = iniciar(f"set application_name={literal(tag + suffix)}; begin; {statement};\n")
        processes.append(process)
        aguardar(f"select exists(select 1 from pg_stat_activity where application_name={literal(tag + suffix)} and state='idle in transaction')",
                 'controlador mantém barreira de sincronização')
        return process

    def wait_lock(suffix: str) -> None:
        """Confirma espera real por lock, em vez de apenas processos iniciados."""
        aguardar(f"select exists(select 1 from pg_stat_activity where application_name={literal(tag + suffix)} and wait_event_type='Lock')",
                 'sessão concorrente chegou ao lock ' + suffix)

    def release(process: subprocess.Popen[str]) -> None:
        """Libera a barreira e encerra sua conexão sem imprimir dados de sessão."""
        assert process.stdin is not None
        process.stdin.write('commit;\n')
        process.stdin.flush()
        process.communicate(timeout=20)
        assert process.returncode == 0

    try:
        psql(f"""
        begin;
        insert into auth.users(id,email,email_confirmed_at) values({literal(user)},{literal(tag + '@example.invalid')},now());
        insert into auth.sessions(id,user_id,not_after) values({literal(session)},{literal(user)},now()+interval '1 hour');
        insert into public.organizations(id,name) values({literal(org)},'Organização fictícia de concorrência');
        insert into public.app_users(user_id,organization_id,display_name,role,active)
          values({literal(user)},{literal(org)},'Master fictício','Master',true);
        insert into public.integration_sources(id,organization_id,source_key,name,stores)
          values({literal(source)},{literal(org)},{literal(tag)},'Fonte fictícia',array['loja1','loja2']);
        commit;
        """)
        ready = True
        control = barrier(f"select pg_advisory_xact_lock(hashtextextended({literal(source + '/loja1')},12))", '_control_begin')
        first = iniciar(call(f"select public.sync_begin({literal(source)},'loja1',{literal(request_a)},'2020-01-01','2020-01-31',1,4,repeat('a',64))", '_begin_a'))
        processes.append(first)
        wait_lock('_begin_a')
        second = iniciar(call(f"select public.sync_begin({literal(source)},'loja1',{literal(request_b)},'2020-01-01','2020-01-31',1,4,repeat('b',64))", '_begin_b'))
        processes.append(second)
        wait_lock('_begin_b')
        release(control)
        _, first_error = first.communicate(timeout=20)
        _, second_error = second.communicate(timeout=20)
        # A fila do advisory lock preserva a primeira aquisição observada.
        assert first.returncode == 0 and 'ERROR' not in first_error
        assert second.returncode != 0 and '55000' in second_error
        assert psql(f"select count(*) from public.sync_runs where organization_id={literal(org)}") == '1'
        run = json.loads(psql(f"select json_build_object('id',id,'token',claim_token) from public.sync_runs where organization_id={literal(org)}"))
        run_id, token = run['id'], run['token']
        print('APROVADO SYNC CONCORRÊNCIA: somente uma execução por fonte e loja.')

        row = json.dumps([{'source_id': source, 'store_id': 'loja1', 'document_id': '0001', 'line_id': '001',
            'product_id': '001', 'sku': '001', 'sale_date': '2020-01-10', 'status': 'completed',
            'quantity': 5, 'returned_quantity': 1, 'source_updated_at': '2020-01-11T00:00:00Z'}])
        statement = f"select public.sync_stage({literal(run_id)},{literal(token)},1,{literal(row)}::jsonb)"
        control = barrier(f"select 1 from public.sync_runs where id={literal(run_id)} for update", '_control_stage')
        stages = []
        for suffix in ('_stage_a', '_stage_b'):
            process = iniciar(call(statement, suffix))
            processes.append(process)
            stages.append(process)
            wait_lock(suffix)
        release(control)
        for process in stages:
            _, error = process.communicate(timeout=20)
            assert process.returncode == 0 and 'ERROR' not in error
        assert psql(f"select row_count||':'||checkpoint from public.sync_runs where id={literal(run_id)}") == '1:1'
        assert psql(f"select count(*) from public.sync_staging where run_id={literal(run_id)}") == '1'
        assert psql(f"select count(*) from public.sales_line_versions where organization_id={literal(org)}") == '0'
        print('APROVADO SYNC CONCORRÊNCIA: página concorrente é validada e não duplica staging.')

        psql(call(f"select public.sync_preview({literal(run_id)},{literal(token)})", '_preview'))
        statement = f"select public.sync_commit({literal(run_id)},{literal(token)},true)"
        control = barrier(f"select 1 from public.sync_runs where id={literal(run_id)} for update", '_control_commit')
        commits = []
        for suffix in ('_commit_a', '_commit_b'):
            process = iniciar(call(statement, suffix))
            processes.append(process)
            commits.append(process)
            wait_lock(suffix)
        release(control)
        for process in commits:
            _, error = process.communicate(timeout=20)
            assert process.returncode == 0 and 'ERROR' not in error
        assert psql(f"select status||':'||changed_rows from public.sync_runs where id={literal(run_id)}") == 'completed:1'
        assert psql(f"select count(*) from public.sales_line_versions where organization_id={literal(org)}") == '1'
        assert psql(f"select count(*) from public.sales_lines_current where organization_id={literal(org)}") == '1'
        print('APROVADO SYNC CONCORRÊNCIA: duas confirmações produzem uma única versão analítica.')

        # Fixture mínimo na fronteira da cota. O payload sintético não é usado
        # para análise, somente para contar ocupação concorrente do staging.
        qa, qb = str(uuid.uuid4()), str(uuid.uuid4())
        psql(call(f"select public.sync_begin({literal(source)},'loja1',{literal(qa)},'2020-01-01','2020-01-31',20000,4,repeat('c',64))", '_quota_begin_a'))
        psql(call(f"select public.sync_begin({literal(source)},'loja2',{literal(qb)},'2020-01-01','2020-01-31',1,4,repeat('d',64))", '_quota_begin_b'))
        runs = json.loads(psql(f"select json_agg(json_build_object('store',store_id,'id',id,'token',claim_token)) from public.sync_runs where organization_id={literal(org)} and status='staging'"))
        by_store = {run['store']: run for run in runs}
        a, b = by_store['loja1'], by_store['loja2']
        psql(f"""
        insert into public.sync_staging(organization_id,run_id,document_id,line_id,payload)
          select {literal(org)},{literal(a['id'])},'quota-'||i,'1','{{}}'::jsonb from generate_series(1,19999) i;
        update public.sync_runs set row_count=19999 where id={literal(a['id'])};
        """)
        control = barrier(f"select pg_advisory_xact_lock(hashtextextended({literal(org)},13))", '_control_quota')
        quota_processes = []
        for record, suffix in ((a, '_quota_a'), (b, '_quota_b')):
            payload = json.loads(row)
            payload[0]['store_id'] = record['store']
            statement = f"select public.sync_stage({literal(record['id'])},{literal(record['token'])},1,{literal(json.dumps(payload))}::jsonb)"
            process = iniciar(call(statement, suffix))
            processes.append(process)
            quota_processes.append(process)
            wait_lock(suffix)
        release(control)
        _, error_a = quota_processes[0].communicate(timeout=20)
        _, error_b = quota_processes[1].communicate(timeout=20)
        assert quota_processes[0].returncode == 0 and 'ERROR' not in error_a
        assert quota_processes[1].returncode != 0 and '54000' in error_b
        assert psql(f"select count(*) from public.sync_staging where organization_id={literal(org)}") == '20000'
        assert psql(f"select checkpoint from public.sync_runs where id={literal(b['id'])}") == '0'
        print('APROVADO SYNC CONCORRÊNCIA: duas lojas não ultrapassam a cota conjunta de vinte mil linhas.')

    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
        if ready:
            psql(f"""
            begin;
            delete from public.sales_lines_current where organization_id={literal(org)};
            delete from public.sales_line_versions where organization_id={literal(org)};
            delete from public.sync_staging where organization_id={literal(org)};
            delete from public.sync_pages where organization_id={literal(org)};
            delete from public.sync_runs where organization_id={literal(org)};
            delete from public.integration_sources where organization_id={literal(org)};
            delete from public.audit_events where organization_id={literal(org)};
            delete from public.app_users where organization_id={literal(org)};
            delete from auth.sessions where user_id={literal(user)};
            delete from auth.users where id={literal(user)};
            delete from public.organizations where id={literal(org)};
            commit;
            """)


if __name__ == '__main__':
    executar()
