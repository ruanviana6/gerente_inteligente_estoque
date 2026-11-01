"""Valida origem, chaves, transporte seguro e falhas do cliente Supabase."""

import base64
import json
import urllib.error

import pytest

from web.api import HTTPResponse, SupabaseAPI, _NoRedirect, http_transport
from web.errors import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    RateLimited,
    ServiceUnavailable,
)
from web.settings import PROJECT_URL, Settings


def settings(**values):
    """Fornece chaves fictícias, sem buscar configurações do ambiente real."""
    return Settings(PROJECT_URL, "sb_publishable_ficticio", **values)


def legacy_key(role):
    """Gera apenas a estrutura de uma chave legada, sem assinatura válida."""
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return "cabecalho." + payload + ".assinatura_ficticia"


@pytest.mark.parametrize("url", [
    "http://csiqpzoudruzppfbpqqx.supabase.co", "https://outro.supabase.co",
    PROJECT_URL + "@evil.example", PROJECT_URL + "/rest/v1", PROJECT_URL + "?token=nao",
    PROJECT_URL + "#segredo", "https://user@csiqpzoudruzppfbpqqx.supabase.co",
    PROJECT_URL + ":443", PROJECT_URL + ".evil.example",
])
def test_rejeita_origem_diferente_antes_da_rede(url):
    """Uma URL manipulada não deve receber tokens do projeto autorizado."""
    with pytest.raises(ConfigurationError):
        Settings(url, "sb_publishable_ficticio")


@pytest.mark.parametrize("key", ["sb_secret_ficticio", legacy_key("service_role"), "\nsegredo", "invalida"])
def test_chave_administrativa_nunca_e_chave_comum(key):
    """A configuração impede contornar RLS por uma troca acidental de chave."""
    with pytest.raises(ConfigurationError):
        Settings(PROJECT_URL, key)


def test_chave_anon_legada_e_permitida_sem_autorizar_por_claims():
    """A análise local diferencia tipo de chave, sem validar usuário por JWT editável."""
    config = Settings(PROJECT_URL, legacy_key("anon"))
    assert config.supabase_url == PROJECT_URL
    assert config.publishable_key not in repr(config)


def test_mapping_desabilita_email_por_padrao_e_string_false():
    """Configuração textual falsa não pode acionar um envio externo."""
    source = {"SUPABASE_URL": PROJECT_URL, "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_ficticio"}
    assert not Settings.from_mapping(source).auth_email_enabled
    assert not Settings.from_mapping({**source, "AUTH_EMAIL_ENABLED": "false"}).auth_email_enabled
    assert Settings.from_mapping({**source, "AUTH_EMAIL_ENABLED": "true"}).auth_email_enabled
    with pytest.raises(ConfigurationError):
        Settings.from_mapping({**source, "AUTH_EMAIL_ENABLED": "qualquer texto"})


def test_segredo_backend_opcional_nao_altera_configuracao_de_login():
    """Sem segredo de dados, a configuração de Auth continua disponível para ativação."""
    source = {"SUPABASE_URL": PROJECT_URL, "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_ficticio"}
    assert Settings.from_mapping(source).backend_secret is None
    secret = "segredo_backend_exclusivo_ficticio_123456789"
    config = Settings.from_mapping({**source, "SUPABASE_BACKEND_SECRET": secret})
    assert config.backend_secret == secret
    assert secret not in repr(config)
    assert secret not in repr(SupabaseAPI(config))


@pytest.mark.parametrize("length", [32, 512])
def test_segredo_backend_aceita_limites_validos(length):
    """Os limites do cliente correspondem ao procedimento administrativo do banco."""
    assert settings(backend_secret="x" * length).backend_secret == "x" * length


@pytest.mark.parametrize("secret", ["", "x" * 31, "x" * 513, 32, True, b"x" * 32])
def test_segredo_backend_invalido_nao_expoe_valor(secret):
    """Configuração inválida é recusada sem interpolar o segredo na mensagem."""
    with pytest.raises(ConfigurationError) as failure:
        settings(backend_secret=secret)
    assert str(failure.value) == "O segredo dedicado do backend deve ter entre 32 e 512 caracteres."


def test_rpc_load_inventory_injeta_segredo_protegido_sem_aceitar_override():
    """Somente a RPC de dados recebe o segredo configurado, sem alterar entrada ou headers."""
    calls = []

    def transport(*args):
        """Captura a chamada fictícia para conferir o limite da informação protegida."""
        calls.append(args)
        return HTTPResponse(200, b"{}")

    secret = "segredo_backend_exclusivo_ficticio_123456789"
    api = SupabaseAPI(settings(backend_secret=secret), transport)
    payload = {"p_module": "dashboard.view", "p_backend_secret": "override_nao_confiavel"}
    api.rpc("load_inventory", payload, "token_usuario")
    assert json.loads(calls[0][3])["p_backend_secret"] == secret
    assert payload["p_backend_secret"] == "override_nao_confiavel"
    assert secret not in calls[0][1]
    assert secret not in repr(calls[0][2])
    assert calls[0][2]["Authorization"] == "Bearer token_usuario"
    assert calls[0][2]["apikey"] == "sb_publishable_ficticio"
    api.rpc("app_context", {"p_backend_secret": "override_nao_confiavel"}, "token_usuario")
    assert json.loads(calls[1][3]) == {}
    api.auth("POST", "token?grant_type=password", {"email": "ficticio@example.test", "password": "ficticia"})
    assert "p_backend_secret" not in json.loads(calls[2][3])


def test_rpc_sem_segredo_configurado_descarta_override_do_payload():
    """A ausência de configuração nunca é suprida por um valor recebido do chamador."""
    calls = []

    def transport(*args):
        """Registra o JSON gerado sem executar rede."""
        calls.append(args)
        return HTTPResponse(200, b"{}")

    api = SupabaseAPI(settings(), transport)
    api.rpc("load_inventory", {"p_module": "inventory.view", "p_backend_secret": "forjado"}, "token_usuario")
    assert json.loads(calls[0][3]) == {"p_module": "inventory.view", "p_backend_secret": None}


def test_rpc_transmite_somente_chave_publica_e_jwt_do_usuario():
    """A presença de service key não influencia consultas normais."""
    calls = []

    def transport(*args):
        """Captura o contrato HTTP sem conexão externa."""
        calls.append(args)
        return HTTPResponse(200, b'{"ok":true}')

    config = settings(service_role_key="sb_secret_ficticio")
    api = SupabaseAPI(config, transport)
    assert api.rpc("app_context", {}, "token_usuario") == {"ok": True}
    assert calls[0][1] == PROJECT_URL + "/rest/v1/rpc/app_context"
    assert calls[0][2]["apikey"] == config.publishable_key
    assert calls[0][2]["Authorization"] == "Bearer token_usuario"
    assert config.service_role_key not in repr(calls)
    assert config.service_role_key not in repr(config)


@pytest.mark.parametrize("name", ["../admin/users", "http://evil.example", "app_context?x=y", "A", ""])
def test_nome_rpc_nao_pode_injetar_caminho(name):
    """Nomes externos não alteram origem, rota nem parâmetros HTTP."""
    with pytest.raises(AppError):
        SupabaseAPI(settings(), lambda *a: pytest.fail("rede indevida")).rpc(name, {}, "token")


def test_rpc_exige_jwt_e_recusa_header_com_quebra_de_linha():
    """Operações comuns não funcionam com identidade ausente ou header injetável."""
    api = SupabaseAPI(settings(), lambda *a: pytest.fail("rede indevida"))
    with pytest.raises(AuthenticationError):
        api.rpc("app_context", {}, "")
    with pytest.raises(AuthenticationError):
        api.rpc("app_context", {}, "token\r\nX-Header: inseguro")


@pytest.mark.parametrize("status,error", [
    (400, AppError), (401, AuthenticationError), (403, AuthorizationError),
    (429, RateLimited), (500, ServiceUnavailable), (302, ServiceUnavailable),
])
def test_resposta_de_erro_nao_expoe_corpo_ou_credenciais(status, error):
    """Mesmo que a API devolva conteúdo sensível, a mensagem é definida pelo aplicativo."""
    api = SupabaseAPI(settings(), lambda *a: HTTPResponse(status, b'"token_ficticio_em_erro"'))
    with pytest.raises(error) as failure:
        api.rpc("app_context", {}, "token_usuario")
    assert "token_ficticio" not in str(failure.value)
    assert "token_usuario" not in str(failure.value)


@pytest.mark.parametrize("body", [b"<html>indisponivel</html>", b'{"valor":NaN}', b"\xff"])
def test_resposta_invalida_e_fechada_sem_dados_parciais(body):
    """Falhas de serialização não são tratadas como resposta confiável."""
    api = SupabaseAPI(settings(), lambda *a: HTTPResponse(200, body))
    with pytest.raises(ServiceUnavailable):
        api.rpc("app_context", {}, "token")


def test_rejeita_nan_antes_de_enviar_json():
    """Valores não finitos não chegam ao PostgreSQL como JSON inválido."""
    api = SupabaseAPI(settings(), lambda *a: pytest.fail("rede indevida"))
    with pytest.raises(AppError):
        api.rpc("import_inventory", {"valor": float("nan")}, "token")


def test_cadastro_publico_nao_esta_disponivel_no_cliente():
    """O backend não oferece endpoint de signup nem caminho administrativo genérico."""
    api = SupabaseAPI(settings(), lambda *a: pytest.fail("rede indevida"))
    for path in ("signup", "admin/users", "../rest/v1/profiles"):
        with pytest.raises(AuthorizationError):
            api.auth("POST", path, {})


def test_redirecionamento_nunca_recebe_headers_da_origem():
    """O handler não cria request para uma Location controlada por terceiro."""
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example") is None


def test_transporte_traduz_erro_de_rede_sem_conteudo(monkeypatch):
    """Falhas de proxy ou TLS não imprimem a URL nem headers secretos."""
    class FailedOpener:
        """Simula um erro local que contém texto não confiável."""

        def open(self, *args, **kwargs):
            """Falha sem abrir socket."""
            raise urllib.error.URLError("detalhe_com_token_ficticio")

    monkeypatch.setattr("urllib.request.build_opener", lambda *a: FailedOpener())
    with pytest.raises(ServiceUnavailable) as failure:
        http_transport("GET", PROJECT_URL, {}, None, 10)
    assert "detalhe_com_token" not in str(failure.value)


def test_job_e_limitado_a_rpc_dedicada_sem_chave_admin_ou_bearer():
    """O worker usa segredo próprio no corpo e não recebe acesso genérico ao banco."""
    calls = []

    def transport(*args):
        """Registra somente chamadas fictícias para conferir os headers."""
        calls.append(args)
        return HTTPResponse(200, b"[]")

    api = SupabaseAPI(settings(service_role_key="sb_secret_ficticio"), transport)
    secret = "segredo_ficticio_exclusivo_de_teste_123"
    assert api.job_rpc("report_job_claim", {"p_job_secret": secret}) == []
    assert "Authorization" not in calls[0][2]
    assert secret not in calls[0][1]
    assert calls[0][2]["apikey"] == "sb_publishable_ficticio"
    with pytest.raises(AuthorizationError):
        api.job_rpc("admin_set_user", {"p_job_secret": secret})
    with pytest.raises(ConfigurationError):
        api.job_rpc("report_job_claim", {"p_job_secret": "curto"})
    assert len(calls) == 1
