"""Disputa real entre agendamento, pausa e configuração em PostgreSQL descartável.

As barreiras usam locks e pg_stat_activity. Não há chamadas ao GestãoClick,
credenciais produtivas ou dependência de sleeps para ordenar as transações.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid

from concorrencia import aguardar, iniciar, literal, psql


def executar() -> None:
    """Confere serialização do claim e das alterações administrativas do job."""
    if os.environ.get("GERENTE_DISPOSABLE_DATABASE") != "1":
        raise RuntimeError("Permitido somente no PostgreSQL descartável explicitamente selecionado.")
    if psql("select count(*)=5 from information_schema.columns where table_schema='auth' and table_name='users'") != "t":
        raise RuntimeError("O schema Auth não corresponde ao fixture descartável.")
    organization, actor, session, source = [str(uuid.uuid4()) for _ in range(4)]
    tag = "sync_auto_race_" + uuid.uuid4().hex[:12]
    processes: list[subprocess.Popen[str]] = []
    prepared = False
    stores = ["claim", "pause_first", "commit_first", "configure_first", "finish_first", "manual_overlap"]
    claims = json.dumps({"sub": actor, "session_id": session, "role": "authenticated"})

    def sql_call(statement: str, suffix: str, *, master: bool = False, close: bool = True) -> str:
        """Monta uma sessão fictícia, separando autorização humana e delegada."""
        role = "authenticated" if master else "anon"
        context = (f"do $ctx$ begin perform set_config('request.jwt.claims',({literal(claims)}::jsonb || "
                   "jsonb_build_object('exp',extract(epoch from now())+3600))::text,true); end $ctx$;") if master else "do $ctx$ begin perform set_config('request.jwt.claims','{}',true); end $ctx$;"
        return f"""
        set application_name={literal(tag + suffix)};
        begin;
        set local statement_timeout='15s';
        set local role {role};
        {context}
        {statement};
        {"commit;" if close else ""}
        """

    def launch(sql: str) -> subprocess.Popen[str]:
        """Registra o processo para encerrá-lo mesmo se alguma asserção falhar."""
        process = iniciar(sql)
        processes.append(process)
        return process

    def wait_idle(suffix: str) -> None:
        """Confirma que a primeira transação concluiu a função e reteve os locks."""
        aguardar(f"select exists(select 1 from pg_stat_activity where application_name={literal(tag + suffix)} and state='idle in transaction')",
                 "primeira transação mantém locks " + suffix)

    def wait_lock(suffix: str) -> None:
        """Exige disputa observada no servidor, não apenas processos iniciados."""
        aguardar(f"select exists(select 1 from pg_stat_activity where application_name={literal(tag + suffix)} and wait_event_type='Lock')",
                 "segunda transação disputa lock " + suffix)

    def collect(process: subprocess.Popen[str], *, denied: bool = False) -> str:
        """Confere sucesso ou SQLSTATE esperado sem imprimir payloads ou tokens."""
        output, error = process.communicate(timeout=20)
        if denied:
            assert process.returncode != 0 and "42501" in error, "A operação revogada deveria ser recusada."
        else:
            assert process.returncode == 0 and "ERROR" not in error, "A operação autorizada deveria concluir."
        return output

    def release(process: subprocess.Popen[str]) -> str:
        """Confirma a primeira transação e libera a execução concorrente."""
        assert process.stdin is not None
        process.stdin.write("commit;\n")
        process.stdin.flush()
        return collect(process)

    def parsed(output: str) -> dict | None:
        """Lê somente a última resposta JSON produzida pelo psql silencioso."""
        lines = output.strip().splitlines()
        return json.loads(lines[-1]) if lines else None

    def configure(store: str, fingerprint: str = "a") -> str:
        """Gera a operação Master, sem rota configurável nem dados empresariais."""
        return (f"select public.sync_automation_configure({literal(source)},{literal(store)},"
                f"repeat({literal(fingerprint)},64),'2020-01-01','2020-01-20',24,true,true)")

    def step(secret: str, claim: str, action: str, payload: dict | None = None) -> str:
        """Invoca a mesma RPC delegada usada pelo worker, com parâmetros fictícios."""
        return (f"select public.sync_job_step({literal(secret)},{literal(claim)},"
                f"{literal(action)},{literal(json.dumps(payload or {}))}::jsonb)")

    def prepare_run(store: str) -> dict:
        """Cria um lote conciliado que ainda aguarda confirmação e checkpoint."""
        config = parsed(psql(sql_call(configure(store), "_setup_" + store, master=True)))
        secret = config["secret"]
        job = parsed(psql(sql_call(f"select public.sync_job_claim({literal(secret)},repeat('a',64))", "_claim_" + store)))
        run = parsed(psql(sql_call(step(secret, job["claim"], "begin", {
            "p_source_id": source, "p_store_id": store, "p_request_key": job["request_key"],
            "p_period_start": job["start"], "p_period_end": job["end"], "p_expected_rows": 1,
            "p_expected_quantity": "4", "p_content_hash": "a" * 64,
        }), "_begin_" + store)))
        control = {"p_run_id": run["id"], "p_claim_token": run["claim_token"]}
        rows = [{"source_id": source, "store_id": store, "document_id": "0001", "line_id": "001",
                 "product_id": "001", "sku": "001", "sale_date": job["start"], "status": "completed",
                 "quantity": 5, "returned_quantity": 1, "source_updated_at": "2020-01-21T00:00:00Z"}]
        psql(sql_call(step(secret, job["claim"], "stage", {**control, "p_page": 1, "p_rows": rows}), "_stage_" + store))
        psql(sql_call(step(secret, job["claim"], "preview", control), "_preview_" + store))
        return {"store": store, "config": config, "secret": secret, "job": job, "run": run, "control": control}

    def commit_statement(bundle: dict) -> str:
        """Confirma apenas a cópia analítica, sem inventar conferência de cobertura."""
        return step(bundle["secret"], bundle["job"]["claim"], "commit",
                    {**bundle["control"], "p_coverage_confirmed": False})

    def count_versions(store: str) -> str:
        """Conta somente registros fictícios do cenário concorrente atual."""
        return psql(f"select count(*) from public.sales_line_versions where organization_id={literal(organization)} and store_id={literal(store)}")

    try:
        store_sql = ",".join(literal(store) for store in stores)
        psql(f"""
        begin;
        insert into auth.users(id,email,email_confirmed_at) values({literal(actor)},{literal(tag + '@example.invalid')},now());
        insert into auth.sessions(id,user_id,not_after) values({literal(session)},{literal(actor)},now()+interval '1 hour');
        insert into public.organizations(id,name) values({literal(organization)},'Automação concorrente fictícia');
        insert into public.app_users(user_id,organization_id,display_name,role,active)
          values({literal(actor)},{literal(organization)},'Master fictício','Master',true);
        insert into public.integration_sources(id,organization_id,source_key,name,stores)
          values({literal(source)},{literal(organization)},{literal(tag)},'Fonte fictícia',array[{store_sql}]);
        commit;
        """)
        prepared = True

        config = parsed(psql(sql_call(configure("claim"), "_setup_claim", master=True)))
        barrier = launch(f"set application_name={literal(tag + '_claim_barrier')}; begin; select 1 from private.sync_automations where id={literal(config['automation_id'])} for update;\n")
        wait_idle("_claim_barrier")
        statement = f"select public.sync_job_claim({literal(config['secret'])},repeat('a',64))"
        first = launch(sql_call(statement, "_claim_a"))
        wait_lock("_claim_a")
        second = launch(sql_call(statement, "_claim_b"))
        wait_lock("_claim_b")
        release(barrier)
        jobs = [parsed(collect(process)) for process in (first, second)]
        granted = [job for job in jobs if job is not None]
        assert len(granted) == 1, "A mesma janela não pode receber duas concessões simultâneas."
        assert psql(f"select claim::text from private.sync_automations where id={literal(config['automation_id'])}") == granted[0]["claim"]
        print("APROVADO AUTO CONCORRÊNCIA: somente um claim para a janela disputada.")

        bundle = prepare_run("pause_first")
        pause = f"select public.sync_automation_pause({literal(source)},'pause_first')"
        first = launch(sql_call(pause, "_pause_first", master=True, close=False))
        wait_idle("_pause_first")
        second = launch(sql_call(commit_statement(bundle), "_commit_after_pause"))
        wait_lock("_commit_after_pause")
        release(first)
        collect(second, denied=True)
        assert count_versions("pause_first") == "0"
        assert psql(f"select status from public.sync_runs where id={literal(bundle['run']['id'])}") == "previewed"
        print("APROVADO AUTO CONCORRÊNCIA: pausa anterior ao commit impede promoção do lote.")

        bundle = prepare_run("commit_first")
        first = launch(sql_call(commit_statement(bundle), "_commit_first", close=False))
        wait_idle("_commit_first")
        pause = f"select public.sync_automation_pause({literal(source)},'commit_first')"
        second = launch(sql_call(pause, "_pause_after_commit", master=True))
        wait_lock("_pause_after_commit")
        release(first)
        collect(second)
        assert count_versions("commit_first") == "1"
        assert psql(f"select not enabled and cursor_end=history_end from private.sync_automations where id={literal(bundle['config']['automation_id'])}") == "t"
        print("APROVADO AUTO CONCORRÊNCIA: commit anterior à pausa permanece íntegro, sem revogação retroativa.")

        bundle = prepare_run("configure_first")
        psql(sql_call(commit_statement(bundle), "_configure_setup_commit"))
        first = launch(sql_call(configure("configure_first", "b"), "_configure_first", master=True, close=False))
        wait_idle("_configure_first")
        finish = step(bundle["secret"], bundle["job"]["claim"], "finish")
        second = launch(sql_call(finish, "_finish_after_configure"))
        wait_lock("_finish_after_configure")
        release(first)
        collect(second, denied=True)
        assert count_versions("configure_first") == "1"
        assert psql(f"select contract_hash=repeat('b',64) and cursor_end=history_end from private.sync_automations where id={literal(bundle['config']['automation_id'])}") == "t"
        print("APROVADO AUTO CONCORRÊNCIA: contrato alterado invalida finish antigo e preserva histórico.")

        bundle = prepare_run("finish_first")
        psql(sql_call(commit_statement(bundle), "_finish_setup_commit"))
        finish = step(bundle["secret"], bundle["job"]["claim"], "finish")
        first = launch(sql_call(finish, "_finish_first", close=False))
        wait_idle("_finish_first")
        second = launch(sql_call(configure("finish_first", "b"), "_configure_after_finish", master=True))
        wait_lock("_configure_after_finish")
        release(first)
        collect(second)
        assert count_versions("finish_first") == "1"
        assert psql(f"select contract_hash=repeat('b',64) and cursor_end=history_end and last_success is not null from private.sync_automations where id={literal(bundle['config']['automation_id'])}") == "t"
        print("APROVADO AUTO CONCORRÊNCIA: finish anterior à configuração conclui antes da nova aprovação.")

        bundle = prepare_run("manual_overlap")
        manual = f"select public.sync_commit({literal(bundle['run']['id'])},{literal(bundle['run']['claim_token'])},false)"
        first = launch(sql_call(manual, "_manual_commit", master=True, close=False))
        wait_idle("_manual_commit")
        second = launch(sql_call(commit_statement(bundle), "_auto_commit_overlap"))
        wait_lock("_auto_commit_overlap")
        release(first)
        collect(second)
        psql(sql_call(step(bundle["secret"], bundle["job"]["claim"], "finish"), "_manual_overlap_finish"))
        assert count_versions("manual_overlap") == "1"
        assert psql(f"select status||':'||changed_rows from public.sync_runs where id={literal(bundle['run']['id'])}") == "completed:1"
        print("APROVADO AUTO CONCORRÊNCIA: commit manual e automático coexistem sem deadlock ou duplicação.")
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
        if prepared:
            psql(f"""
            begin;
            delete from private.sync_automations where organization_id={literal(organization)};
            delete from public.sales_lines_current where organization_id={literal(organization)};
            delete from public.sales_line_versions where organization_id={literal(organization)};
            delete from public.sync_staging where organization_id={literal(organization)};
            delete from public.sync_pages where organization_id={literal(organization)};
            delete from public.sync_runs where organization_id={literal(organization)};
            delete from public.integration_sources where organization_id={literal(organization)};
            delete from public.audit_events where organization_id={literal(organization)};
            delete from public.app_users where organization_id={literal(organization)};
            delete from auth.sessions where user_id={literal(actor)};
            delete from auth.users where id={literal(actor)};
            delete from public.organizations where id={literal(organization)};
            commit;
            """)


if __name__ == "__main__":
    executar()
