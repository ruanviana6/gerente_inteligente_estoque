"""Reproduz a disputa de dois Masters por uma conta ainda sem perfil.

Exclusivo do PostgreSQL descartável do CI, depois de bootstrap.sql e migrações.
Um trigger de teste segura o primeiro INSERT enquanto a outra sessão tenta
vincular o mesmo UUID a uma organização diferente. A segunda deve receber
42501, preservando o perfil criado pela primeira. Nenhum Supabase real é usado.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid


def literal(valor: str) -> str:
    """Protege os identificadores fictícios gerados pelo próprio teste."""
    return "'" + valor.replace("'", "''") + "'"


def psql(sql: str) -> str:
    """Executa SQL no banco descartável selecionado por PGHOST/PGPORT/PGUSER."""
    resultado = subprocess.run(
        ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=sqlstate"],
        input=sql, capture_output=True, text=True, timeout=20, check=False,
    )
    if resultado.returncode:
        raise RuntimeError("Falha na preparação ou consulta do teste concorrente.")
    return resultado.stdout.strip()


def iniciar(sql: str) -> subprocess.Popen[str]:
    """Abre uma sessão que mantém a transação ativa até concluir ou ser encerrada."""
    processo = subprocess.Popen(
        ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=sqlstate"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert processo.stdin is not None
    processo.stdin.write(sql)
    processo.stdin.flush()
    return processo


def aguardar(condicao: str, descricao: str) -> None:
    """Usa estado do PostgreSQL como barreira, sem depender de atrasos arbitrários."""
    limite = time.monotonic() + 15
    while time.monotonic() < limite:
        if psql(condicao) == "t":
            return
        time.sleep(0.1)
    raise AssertionError("A barreira de concorrência não foi alcançada: " + descricao)


def executar() -> None:
    """Garante que a disputa não permite editar uma conta de outra organização."""
    if os.environ.get("GERENTE_DISPOSABLE_DATABASE") != "1":
        raise RuntimeError("Defina GERENTE_DISPOSABLE_DATABASE=1 somente no banco descartável do CI.")
    # A cópia mínima de auth.users tem quatro colunas; um Supabase verdadeiro não passa.
    if psql("select count(*)=4 from information_schema.columns where table_schema='auth' and table_name='users'") != "t":
        raise RuntimeError("O teste recusou um schema Auth diferente do fixture descartável.")

    org_a, org_b, master_a, master_b, alvo, sessao_a, sessao_b = [str(uuid.uuid4()) for _ in range(7)]
    tag = "gerente_corrida_" + uuid.uuid4().hex[:12]
    processos: list[subprocess.Popen[str]] = []
    preparado = False
    try:
        psql(f"""
          begin;
          insert into auth.users(id,email,email_confirmed_at) values
            ({literal(master_a)},'a.{tag}@example.invalid',now()),
            ({literal(master_b)},'b.{tag}@example.invalid',now()),
            ({literal(alvo)},'alvo.{tag}@example.invalid',now());
          insert into auth.sessions(id,user_id,not_after) values
            ({literal(sessao_a)},{literal(master_a)},now()+interval '1 hour'),
            ({literal(sessao_b)},{literal(master_b)},now()+interval '1 hour');
          insert into public.organizations(id,name) values
            ({literal(org_a)},'Organização fictícia A'),({literal(org_b)},'Organização fictícia B');
          insert into public.app_users(user_id,organization_id,display_name,role,active) values
            ({literal(master_a)},{literal(org_a)},'Master A','Master',true),
            ({literal(master_b)},{literal(org_b)},'Master B','Master',true);
          create schema {tag};
          create function {tag}.barreira() returns trigger language plpgsql set search_path='' as $barreira$
          begin
            if new.user_id={literal(alvo)}::uuid then
              perform pg_advisory_xact_lock(hashtextextended({literal(tag)},0));
            end if;
            return new;
          end $barreira$;
          create trigger {tag} before insert on public.app_users for each row execute function {tag}.barreira();
          commit;
        """)
        preparado = True
        controlador = iniciar(f"""
          set application_name={literal(tag + '_controle')};
          begin;
          select pg_advisory_xact_lock(hashtextextended({literal(tag)},0));
        """)
        processos.append(controlador)
        aguardar(
            f"select exists(select 1 from pg_stat_activity where application_name={literal(tag + '_controle')} and state='idle in transaction')",
            "controlador mantém o primeiro INSERT bloqueado",
        )

        def chamada(master: str, sessao: str, nome: str, sufixo: str) -> str:
            """Monta uma chamada administrativa normal, com duas identidades distintas."""
            claims = json.dumps({"sub": master, "session_id": sessao, "role": "authenticated"})
            return f"""
              set application_name={literal(tag + sufixo)};
              begin;
              set local role authenticated;
              select set_config('request.jwt.claims',({literal(claims)}::jsonb ||
                jsonb_build_object('exp',extract(epoch from now())+3600))::text,true);
              select public.admin_set_user({literal(alvo)},{literal(nome)},'Operador',true,'{{}}');
              commit;
            """

        primeiro = iniciar(chamada(master_a, sessao_a, "Perfil da organização A", "_a"))
        processos.append(primeiro)
        aguardar(
            f"select exists(select 1 from pg_stat_activity where application_name={literal(tag + '_a')} and wait_event='advisory')",
            "primeiro Master chegou ao INSERT",
        )
        segundo = iniciar(chamada(master_b, sessao_b, "Captura pela organização B", "_b"))
        processos.append(segundo)
        aguardar(
            f"select exists(select 1 from pg_stat_activity where application_name={literal(tag + '_b')} and wait_event='advisory')",
            "segundo Master disputa a mesma identidade",
        )
        assert controlador.stdin is not None
        controlador.stdin.write("commit;\n")
        controlador.stdin.flush()
        controlador.communicate(timeout=20)
        _, erro_a = primeiro.communicate(timeout=20)
        _, erro_b = segundo.communicate(timeout=20)
        assert primeiro.returncode == 0, "A primeira associação deveria concluir sem erro."
        assert not erro_a or "ERROR" not in erro_a
        assert segundo.returncode != 0 and "42501" in erro_b, "A segunda organização deveria receber acesso negado."
        assert psql(f"select organization_id::text || ':' || display_name from public.app_users where user_id={literal(alvo)}") == org_a + ":Perfil da organização A"
        print("APROVADO: dois Masters concorrentes não capturam o mesmo UUID entre organizações.")
    finally:
        for processo in processos:
            if processo.poll() is None:
                processo.kill()
                processo.communicate(timeout=5)
        if preparado:
            psql(f"""
              begin;
              drop trigger {tag} on public.app_users;
              drop schema {tag} cascade;
              delete from public.audit_events where organization_id in ({literal(org_a)},{literal(org_b)});
              delete from public.user_permissions where user_id in ({literal(master_a)},{literal(master_b)},{literal(alvo)});
              delete from public.app_users where organization_id in ({literal(org_a)},{literal(org_b)});
              delete from auth.sessions where user_id in ({literal(master_a)},{literal(master_b)},{literal(alvo)});
              delete from auth.users where id in ({literal(master_a)},{literal(master_b)},{literal(alvo)});
              delete from public.organizations where id in ({literal(org_a)},{literal(org_b)});
              commit;
            """)


if __name__ == "__main__":
    executar()
