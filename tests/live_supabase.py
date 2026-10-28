"""Integração com Auth e PostgREST reais, exclusivos do Supabase local descartável do CI.

Não aceita endereço remoto e não reutiliza chaves do projeto do proprietário.
Execução separada: python -m pytest tests/live_supabase.py.
"""

import json
import os
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from web.api import HTTPResponse, SupabaseAPI
from web.auth import AuthService
from web.errors import AuthenticationError, AuthorizationError
from web.service import Service
from web.settings import PROJECT_URL, Settings

SENHA = "Senha-ficticia-local-2026!"


@pytest.fixture(scope="module")
def ambiente():
    """Cria contas fictícias via Admin API real e ativa Master no banco descartável."""
    resultado = subprocess.run(["npx", "--yes", "supabase@2.122.0", "status", "-o", "json"],
                               check=True, capture_output=True, text=True)
    status = json.loads(resultado.stdout)
    destino = status["API_URL"]
    banco = urlsplit(status["DB_URL"])
    assert urlsplit(destino).hostname in {"127.0.0.1", "localhost"}
    assert banco.hostname in {"127.0.0.1", "localhost"}
    cliente = build_opener(ProxyHandler({}))

    def transporte(method, url, headers, payload, timeout):
        """Redireciona apenas a origem aprovada para o serviço local deste teste."""
        assert url.startswith(PROJECT_URL + "/")
        pedido = Request(destino + url.removeprefix(PROJECT_URL), data=payload,
                         headers=dict(headers), method=method)
        try:
            with cliente.open(pedido, timeout=timeout) as resposta:
                return HTTPResponse(resposta.status, resposta.read())
        except HTTPError as erro:
            codigo = erro.code
            erro.close()
            return HTTPResponse(codigo, b"")

    config = Settings(PROJECT_URL, status["ANON_KEY"], status["SERVICE_ROLE_KEY"])
    api = SupabaseAPI(config, transporte)
    conta = api._create_auth_user("master@example.invalid", SENHA)
    uid = conta.get("user", conta)["id"]
    variaveis = os.environ.copy()
    variaveis.update(PGHOST=banco.hostname, PGPORT=str(banco.port), PGUSER=banco.username,
                     PGPASSWORD=banco.password, PGDATABASE=banco.path.lstrip("/"))
    from uuid import UUID

    uid = str(UUID(uid))
    sql = f"select private.bootstrap_master('{uid}'::uuid,'Master fictício','Estoque do CI');"
    processo = subprocess.run(["psql", "-v", "ON_ERROR_STOP=1", "-c", sql], env=variaveis,
                              capture_output=True, text=True)
    assert processo.returncode == 0, "O bootstrap do banco local falhou."
    master = AuthService(api)
    master.login("master@example.invalid", SENHA)
    operador_id = master.create_user("operador@example.invalid", SENHA, "Operador fictício", "Operador")
    return {"api": api, "master": master, "operador_id": operador_id, "transport": transporte,
            "anon": status["ANON_KEY"]}


def operador(ambiente):
    """Reativa o operador com as permissões iniciais antes de cada cenário."""
    Service(ambiente["master"]).admin_set_user(ambiente["operador_id"], "Operador fictício", "Operador", True, {})
    auth = AuthService(SupabaseAPI(ambiente["api"].settings, ambiente["transport"]))
    auth.login("operador@example.invalid", SENHA)
    return auth


def test_login_csv_rpc_financeiro_e_relatorio_reais(ambiente):
    """Percorre Auth, PostgREST, PostgreSQL, motor e relatório sem mock de endpoint."""
    master = Service(ambiente["master"])
    master.import_csv(b"codigo;nome;estoque;custo_unitario\n001;Produto ficticio;10;20\n",
                      b"data;codigo;quantidade\n2020-01-15;001;30\n",
                      "2020-01-31", "2020-01-01", "2020-01-31")
    auth = operador(ambiente)
    service = Service(auth)
    assert service.load_inventory("inventory.view")["panel"].iloc[0]["estoque"] == 10
    assert "custo_unitario" not in service.load_inventory("inventory.view")["products"]
    assert master.monthly_report("2020-01-01")["resumo"]["unidades_vendidas"] == 30
    master.admin_set_user(ambiente["operador_id"], "Operador fictício", "Operador", True, {"finance.view": True})
    assert service.load_inventory("inventory.view")["products"].iloc[0]["custo_unitario"] == 20
    master.admin_set_user(ambiente["operador_id"], "Operador fictício", "Operador", True, {"finance.view": False})
    assert "custo_unitario" not in service.load_inventory("inventory.view")["products"]
    assert auth.session.user_id != ambiente["master"].session.user_id


def test_senha_invalida_e_cadastro_publico_bloqueados(ambiente):
    """Auth real recusa senha incorreta e signup público fora do aplicativo."""
    auth = AuthService(ambiente["api"])
    with pytest.raises(AuthenticationError):
        auth.login("operador@example.invalid", "Senha-incorreta-999!")
    resposta = ambiente["transport"]("POST", PROJECT_URL + "/auth/v1/signup",
                                     {"apikey": ambiente["anon"], "Content-Type": "application/json"},
                                     json.dumps({"email": "publico@example.invalid", "password": SENHA}).encode(), 20)
    assert resposta.status in {400, 403, 422}


def test_desativacao_nega_jwt_ainda_valido(ambiente):
    """A API do banco nega a conta desativada mesmo com token não expirado."""
    auth = operador(ambiente)
    token = auth.session.access_token
    Service(ambiente["master"]).admin_set_user(ambiente["operador_id"], "Operador fictício", "Operador", False, {})
    with pytest.raises((AuthenticationError, AuthorizationError)):
        ambiente["api"].rpc("load_inventory", {"p_module": "inventory.view"}, token)


def test_refresh_e_logout_invalidam_acesso_real(ambiente):
    """Renova pelo endpoint Auth real e prova a revogação do JWT após logout."""
    auth = operador(ambiente)
    # Aproxima o relógio do vencimento para exercitar refresh sem esperar uma hora.
    auth._clock = lambda: time.time() + 3550
    auth.context()
    token = auth.session.access_token
    auth._clock = time.time
    auth.logout()
    assert auth.session is None
    with pytest.raises((AuthenticationError, AuthorizationError)):
        ambiente["api"].rpc("app_context", {}, token)
