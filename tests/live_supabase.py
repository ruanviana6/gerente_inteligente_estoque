"""Integração com Auth e PostgREST reais, exclusivos do Supabase local descartável do CI.

Não aceita endereço remoto e não reutiliza chaves do projeto do proprietário.
Execução separada: python -m pytest tests/live_supabase.py.
"""

import json
import os
import re
import subprocess
import time
from dataclasses import replace
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
SENHA_PESSOAL = "Senha-pessoal-ficticia-2026!"
SEGREDO_BACKEND = "segredo-exclusivo-do-backend-ficticio-2026"


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
    chamadas = []

    def transporte(method, url, headers, payload, timeout):
        """Redireciona apenas a origem aprovada para o serviço local deste teste."""
        assert url.startswith(PROJECT_URL + "/")
        chamadas.append((method, urlsplit(url).path, urlsplit(url).query))
        pedido = Request(destino + url.removeprefix(PROJECT_URL), data=payload,
                         headers=dict(headers), method=method)
        try:
            with cliente.open(pedido, timeout=timeout) as resposta:
                print("HTTP local:", method, urlsplit(url).path, resposta.status)
                return HTTPResponse(resposta.status, resposta.read())
        except HTTPError as erro:
            codigo = erro.code
            try:
                detalhe = json.loads(erro.read())
                identificador = detalhe.get("error_code", detalhe.get("code", ""))
            except (ValueError, AttributeError):
                identificador = ""
            seguro = identificador if isinstance(identificador, str) and re.fullmatch(r"[A-Za-z0-9_]{1,40}", identificador) else ""
            print("HTTP local:", method, urlsplit(url).path, codigo, seguro)
            erro.close()
            # Preserva somente o identificador público do erro, nunca o corpo original.
            return HTTPResponse(codigo, json.dumps({"error_code": seguro}).encode())

    config = Settings(PROJECT_URL, status["ANON_KEY"], status["SERVICE_ROLE_KEY"],
                      backend_secret=SEGREDO_BACKEND)
    api = SupabaseAPI(config, transporte)
    conta = api._create_auth_user("master@example.invalid", SENHA)
    uid = conta.get("user", conta)["id"]
    variaveis = os.environ.copy()
    variaveis.update(PGHOST=banco.hostname, PGPORT=str(banco.port), PGUSER=banco.username,
                     PGPASSWORD=banco.password, PGDATABASE=banco.path.lstrip("/"))
    from uuid import UUID

    uid = str(UUID(uid))
    sql = f"""select private.bootstrap_master('{uid}'::uuid,'Master fictício','Estoque do CI');
    select private.configure_backend_secret(
      (select organization_id from public.app_users where user_id='{uid}'::uuid),
      '{SEGREDO_BACKEND}');"""
    processo = subprocess.run(["psql", "-v", "ON_ERROR_STOP=1", "-c", sql], env=variaveis,
                              capture_output=True, text=True)
    assert processo.returncode == 0, "O bootstrap do banco local falhou."
    master = AuthService(api)
    master.login("master@example.invalid", SENHA)
    operador_id = master.create_user("operador@example.invalid", SENHA, "Operador fictício", "Operador")
    primeiro_acesso = AuthService(SupabaseAPI(config, transporte))
    assert primeiro_acesso.login("operador@example.invalid", SENHA) is None
    assert primeiro_acesso.password_change_required
    primeiro_acesso.finish_initial_password(SENHA_PESSOAL)
    return {"api": api, "master": master, "operador_id": operador_id, "transport": transporte,
            "anon": status["ANON_KEY"], "chamadas": chamadas, "local_url": destino, "cliente": cliente}


def operador(ambiente):
    """Reativa o operador com as permissões iniciais antes de cada cenário."""
    Service(ambiente["master"]).admin_set_user(ambiente["operador_id"], "Operador fictício", "Operador", True, {})
    auth = AuthService(SupabaseAPI(ambiente["api"].settings, ambiente["transport"]))
    auth.login("operador@example.invalid", SENHA_PESSOAL)
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
    assert json.loads(resposta.body)["error_code"] == "signup_disabled"


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
    # Aproxima somente o vencimento local, sem presumir a duração do JWT do servidor.
    auth._session = replace(auth.session, expires_at=time.time() + 30)
    inicio = len(ambiente["chamadas"])
    auth.context()
    assert ("POST", "/auth/v1/token", "grant_type=refresh_token") in ambiente["chamadas"][inicio:]
    token = auth.session.access_token
    auth.logout()
    assert auth.session is None
    with pytest.raises((AuthenticationError, AuthorizationError)):
        ambiente["api"].rpc("app_context", {}, token)


def test_redefinicao_master_bloqueia_sessao_e_exige_nova_senha(ambiente):
    """Confere o fluxo completo com Auth e RLS reais em contas descartáveis."""
    uid = ambiente["master"].create_user("recuperar@example.invalid", SENHA, "Funcionário teste", "Operador")
    conta = AuthService(SupabaseAPI(ambiente["api"].settings, ambiente["transport"]))
    assert conta.login("recuperar@example.invalid", SENHA) is None
    conta.finish_initial_password(SENHA_PESSOAL)
    conta.login("recuperar@example.invalid", SENHA_PESSOAL)
    token_antigo = conta.session.access_token
    temporaria = "Temporaria-ficticia-2026!"
    ambiente["master"].reset_employee_password(uid, temporaria, SENHA)
    with pytest.raises((AuthenticationError, AuthorizationError)):
        ambiente["api"].rpc("app_context", {}, token_antigo)
    nova = AuthService(SupabaseAPI(ambiente["api"].settings, ambiente["transport"]))
    assert nova.login("recuperar@example.invalid", temporaria) is None
    nova.finish_initial_password("Senha-final-ficticia-2026!")
    assert nova.login("recuperar@example.invalid", "Senha-final-ficticia-2026!").profile.user_id == uid


def test_painel_sem_estoque_nao_le_base_bruta_pela_api(ambiente):
    """A navegação lê resumo compacto e não concede leitura do catálogo bruto."""
    Service(ambiente["master"]).import_csv(
        b"codigo;nome;estoque\n001;Produto ficticio;10\n",
        b"data;codigo;quantidade\n2020-01-15;001;30\n",
        "2020-01-31", "2020-01-01", "2020-01-31",
    )
    ambiente["master"].create_user(
        "visualizador@example.invalid", SENHA, "Visualizador fictício", "Visualizador",
    )
    auth = AuthService(SupabaseAPI(ambiente["api"].settings, ambiente["transport"]))
    assert auth.login("visualizador@example.invalid", SENHA) is None
    auth.finish_initial_password(SENHA_PESSOAL)
    auth.login("visualizador@example.invalid", SENHA_PESSOAL)
    assert auth.context().can("dashboard.view")
    assert not auth.context().can("inventory.view")
    Service(ambiente['master']).refresh_dashboard()
    compact = Service(auth).dashboard()
    assert compact['summary']['inventory']['products'] == 1
    assert 'finance' not in compact['summary']
    headers = {"apikey": ambiente["anon"], "Authorization": "Bearer " + auth.session.access_token,
               "Content-Type": "application/json"}
    resposta = ambiente["transport"](
        "GET", PROJECT_URL + "/rest/v1/inventory_products?select=codigo", headers, None, 20,
    )
    assert resposta.status == 200
    assert json.loads(resposta.body) == []
    for segredo in (None, "segredo-invalido-nao-concede-base-operacional"):
        parametros = {"p_module": "dashboard.view", "p_backend_secret": segredo}
        resposta = ambiente["transport"](
            "POST", PROJECT_URL + "/rest/v1/rpc/load_inventory", headers,
            json.dumps(parametros).encode(), 20,
        )
        assert resposta.status == 403
    with pytest.raises(AuthorizationError):
        auth.rpc("load_inventory", {"p_module": "inventory.view"}, "inventory.view")


def test_reabertura_e_refresh_persistentes_com_auth_e_postgrest_reais(ambiente):
    """Reconstitui conexão, gira refresh uma vez e prova que Sair impede retomada."""
    from web.persistent_sessions import PersistentSessions

    auth = operador(ambiente)
    expirado = replace(auth.session, expires_at=time.time()-1)
    ticket = PersistentSessions(auth.api).register(expirado, auth.context())
    nova = AuthService(SupabaseAPI(ambiente["api"].settings, ambiente["transport"]))
    inicio = len(ambiente["chamadas"])
    assert nova.restore_browser(ticket).profile.user_id == ambiente["operador_id"]
    chamadas = ambiente["chamadas"][inicio:]
    assert ("POST", "/auth/v1/token", "grant_type=refresh_token") in chamadas
    assert ("POST", "/auth/v1/token", "grant_type=password") not in chamadas
    terceira = AuthService(SupabaseAPI(ambiente["api"].settings, ambiente["transport"]))
    assert terceira.restore_browser(ticket).profile.user_id == ambiente["operador_id"]
    nova.logout()
    with pytest.raises((AuthenticationError, AuthorizationError)):
        terceira.restore_browser(ticket)


def test_fiscal_auth_rpc_e_edge_reais_em_demo(ambiente):
    """Usa Auth, PostgreSQL e runtime Edge locais reais, sem endpoint do ERP."""
    from web.fiscal_service import FiscalService
    auth = operador(ambiente)
    with pytest.raises(AuthorizationError):
        FiscalService(auth).consultar('1001')
    Service(ambiente['master']).admin_set_user(ambiente['operador_id'], 'Operador fictício', 'Operador', True,
        {'fiscal.view': True, 'fiscal.create_request': True})
    fiscal = FiscalService(auth)
    preview = fiscal.preparar('1001', 1, 2)
    assert preview['choices']['tipo_contribuinte'] == 2
    request = fiscal.confirmar(preview['id'], True)
    assert fiscal.confirmar(preview['id'], True)['duplicate'] is True
    assert request['state'] == 'confirmed'
    # A função precisa estar atendendo no runtime, não apenas presente em disco.
    deadline = time.monotonic()+50
    while True:
        try:
            response = ambiente['transport']('POST', PROJECT_URL+'/functions/v1/fiscal-demo',
                {'apikey': ambiente['anon'], 'Content-Type': 'application/json'}, b'{}', 5)
            if response.status == 401:
                break
        except (OSError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            raise AssertionError('Runtime Edge local não confirmou a recusa anônima no prazo.')
        time.sleep(0.5)
    result = fiscal.executar(request['id'])
    assert result['state'] == 'authorized' and result['real_enabled'] is False
    assert fiscal.executar(request['id'])['state'] == 'authorized'
    assert fiscal.listar()[0]['taxpayer'] == 2
    assert not any('gestaoclick' in path.lower() for _, path, _ in ambiente['chamadas'])


def test_fiscal_revogacao_bloqueia_rpc_e_edge_com_jwt_ainda_valido(ambiente):
    """Uma chamada direta ao executor não contorna a revogação no banco."""
    from web.fiscal_service import FiscalService
    auth = operador(ambiente)
    master = Service(ambiente['master'])
    master.admin_set_user(ambiente['operador_id'], 'Operador fictício', 'Operador', True,
                         {'fiscal.view': True, 'fiscal.create_request': True})
    fiscal = FiscalService(auth)
    preview = fiscal.preparar('1003', 1, 9)
    pending = fiscal.confirmar(preview['id'], True)
    assert pending['state'] == 'awaiting_approval'
    with pytest.raises(AuthorizationError):
        fiscal.aprovar(pending['id'], True)
    FiscalService(ambiente['master']).aprovar(pending['id'], True)
    jwt = auth.session.access_token
    master.admin_set_user(ambiente['operador_id'], 'Operador fictício', 'Operador', True, {})
    response = ambiente['transport']('POST', PROJECT_URL+'/functions/v1/fiscal-demo',
        {'apikey': ambiente['anon'], 'Authorization':'Bearer '+jwt, 'Content-Type':'application/json'},
        json.dumps({'request_id':pending['id'], 'action':'execute_demo'}).encode(),20)
    assert response.status == 403
    with pytest.raises(AuthorizationError):
        fiscal.listar()
