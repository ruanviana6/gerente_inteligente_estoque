"""Verifica concorrência e idempotência de fotografias no PostgreSQL descartável.

Duas conexões importam o mesmo dia mantendo o lock da organização durante a
primeira transação. A segunda aguarda; cada saldo continua associado ao próprio
lote. Nunca usa credenciais ou tabelas de um Supabase real.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid

from concorrencia import aguardar, iniciar, literal, psql


def executar() -> None:
    """Reproduz importações concorrentes e verifica o fechamento completo."""
    if os.environ.get("GERENTE_DISPOSABLE_DATABASE") != "1":
        raise RuntimeError("O teste exige GERENTE_DISPOSABLE_DATABASE=1 e Auth descartável.")
    if psql("select count(*)=4 from information_schema.columns where table_schema='auth' and table_name='users'") != "t":
        raise RuntimeError("Schema Auth incompatível com a fixture descartável.")
    org, master, session = [str(uuid.uuid4()) for _ in range(3)]
    tag = "snapshot_race_" + uuid.uuid4().hex[:12]
    processes: list[subprocess.Popen[str]] = []
    prepared = False
    try:
        psql(f"""
          begin;
          insert into auth.users(id,email,email_confirmed_at) values
            ({literal(master)},'snapshot.{tag}@example.invalid',now());
          insert into auth.sessions(id,user_id,not_after) values
            ({literal(session)},{literal(master)},now()+interval '1 hour');
          insert into public.organizations(id,name) values({literal(org)},'Estoque fictício concorrente');
          insert into public.app_users(user_id,organization_id,display_name,role,active) values
            ({literal(master)},{literal(org)},'Master fictício','Master',true);
          commit;
        """)
        prepared = True
        claims = json.dumps({"sub": master, "session_id": session, "role": "authenticated"})

        def products(stock: int) -> str:
            """Gera apenas catálogo fictício com os parâmetros exigidos pela RPC."""
            return literal(json.dumps([{"codigo": "001", "nome": "Produto fictício", "fornecedor": "Fictício",
                "estoque": stock, "prazo_entrega_dias": 7, "estoque_seguranca": 2,
                "pedidos_pendentes": 0, "reservas": 0, "cobertura_alvo_dias": 30}]))

        def pair(first: str, second: str, suffix: str) -> tuple[dict, dict]:
            """Controla a disputa com locks e estado observado do PostgreSQL."""
            barrier = tag + suffix
            controller = iniciar(f"set application_name={literal(barrier + '_control')}; begin; "
                                 f"select pg_advisory_xact_lock(hashtextextended({literal(barrier)},0));\n")
            processes.append(controller)
            aguardar(f"select exists(select 1 from pg_stat_activity where application_name={literal(barrier + '_control')} "
                     "and state='idle in transaction')", "controlador da importação")

            def sql(operation: str, name: str, block: bool) -> str:
                """Abre transação autenticada real e mantém a primeira em curso."""
                hold = f"select pg_advisory_xact_lock(hashtextextended({literal(barrier)},0));" if block else ""
                return f"""
                  set application_name={literal(name)}; begin; set local role authenticated;
                  select set_config('request.jwt.claims',({literal(claims)}::jsonb ||
                    jsonb_build_object('exp',extract(epoch from now())+3600))::text,true);
                  {operation}; {hold} commit;
                """

            a = iniciar(sql(first, barrier + '_a', True))
            processes.append(a)
            aguardar(f"select exists(select 1 from pg_stat_activity where application_name={literal(barrier + '_a')} "
                     "and wait_event='advisory')", "primeira importação antes do commit")
            b = iniciar(sql(second, barrier + '_b', False))
            processes.append(b)
            aguardar(f"select exists(select 1 from pg_stat_activity where application_name={literal(barrier + '_b')} "
                     "and wait_event='advisory')", "segunda importação serializada por organização")
            assert controller.stdin is not None
            controller.stdin.write("commit;\n")
            controller.stdin.flush()
            controller.communicate(timeout=20)
            out_a, err_a = a.communicate(timeout=20)
            out_b, err_b = b.communicate(timeout=20)
            assert a.returncode == 0, err_a
            assert b.returncode == 0, err_b

            def result(output: str) -> dict:
                """Extrai a resposta da RPC entre as demais respostas da transação."""
                for line in output.splitlines():
                    if line.startswith('{'):
                        value = json.loads(line)
                        if 'batch_id' in value:
                            return value
                raise AssertionError("A RPC não devolveu a identificação do lote.")
            return result(out_a), result(out_b)

        full = f"select public.import_inventory({products(10)},'[]','2020-01-31','2020-01-01','2020-01-31')"
        narrow = f"select public.import_inventory({products(99)},'[]','2020-01-31','2020-01-20','2020-01-31')"
        first, second = pair(full, narrow, '_different')
        assert first['batch_id'] != second['batch_id']
        assert psql(f"select count(*)=2 from public.inventory_snapshot_versions where organization_id={literal(org)}") == 't'
        assert psql(f"select estoque from public.inventory_snapshot_versions where batch_id={literal(first['batch_id'])}") == '10'
        assert psql(f"select estoque from public.inventory_snapshot_versions where batch_id={literal(second['batch_id'])}") == '99'
        # O helper privado é consultado como postgres da fixture, com destinatário
        # e organização explícitos. A autorização autenticada é coberta no SQL.
        assert psql(f"select value->>'estoque' from jsonb_array_elements(private.inventory_payload({literal(org)},{literal(master)},'2020-01-01')->'snapshots') "
                    "where value->>'data'='2020-01-31'") == '10'
        print("APROVADO: importações concorrentes no mesmo dia preservam os dois saldos e o fechamento completo.")

        identical = f"select public.import_inventory({products(50)},'[]','2020-03-31','2020-03-01','2020-03-31')"
        first, second = pair(identical, identical, '_identical')
        assert first['batch_id'] == second['batch_id']
        assert not first['reused'] and second['reused']
        assert psql(f"select count(*)=3 from public.inventory_imports where organization_id={literal(org)}") == 't'
        assert psql(f"select count(*)=3 from public.inventory_snapshot_versions where organization_id={literal(org)}") == 't'
        print("APROVADO: reimportações idênticas concorrentes reutilizam o mesmo lote sem duplicar fotografias.")
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
        if prepared:
            psql(f"""
              begin;
              delete from public.inventory_current where organization_id={literal(org)};
              delete from public.inventory_snapshot_versions where organization_id={literal(org)};
              delete from public.inventory_costs where organization_id={literal(org)};
              delete from public.inventory_sales where organization_id={literal(org)};
              delete from public.inventory_snapshots where organization_id={literal(org)};
              delete from public.inventory_products where organization_id={literal(org)};
              delete from public.inventory_imports where organization_id={literal(org)};
              delete from public.audit_events where organization_id={literal(org)};
              delete from public.app_users where organization_id={literal(org)};
              delete from auth.sessions where user_id={literal(master)};
              delete from auth.users where id={literal(master)};
              delete from public.organizations where id={literal(org)};
              commit;
            """)


if __name__ == '__main__':
    executar()
