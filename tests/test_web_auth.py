"""Testa autenticação, revogação e isolamento com respostas fictícias do Auth/RPC."""

import copy
import json
from urllib.parse import urlsplit

import pytest

from web.api import HTTPResponse, SupabaseAPI
from web.auth import AuthService, validate_password
from web.errors import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    RateLimited,
    ServiceUnavailable,
)
from web.settings import PROJECT_URL, Settings

USER_A = "00000000-0000-0000-0000-000000000001"
USER_B = "00000000-0000-0000-0000-000000000002"
USER_C = "00000000-0000-0000-0000-000000000003"
ORG = "00000000-0000-0000-0000-000000000010"
PASSWORD = "Senha-ficticia-12345"


class FakeAuthTransport:
    """Emula contratos HTTP; não valida RLS, que possui testes PostgreSQL separados."""

    def __init__(self):
        """Prepara duas identidades com permissões distintas e relógio controlado."""
        self.now = 1000
        self.calls = []
        self.failures = {}
        self.users = {"operador@example.test": USER_A, "visualizador@example.test": USER_B}
        self.contexts = {
            USER_A: self.make_context(USER_A, ["inventory.view", "dashboard.view"]),
            USER_B: self.make_context(USER_B, ["dashboard.view"]),
        }
        self.tokens = {"access-" + uid: uid for uid in (USER_A, USER_B)}
        self.refreshed_identity = None

    @staticmethod
    def make_context(user_id, permissions, role="Operador"):
        """Constrói perfil protegido sem aproveitar metadados do usuário Auth."""
        return {"profile": {"user_id": user_id, "organization_id": ORG,
                            "display_name": "Usuário fictício", "role": role, "active": True},
                "permissions": permissions}

    def session(self, user_id, renewed=False):
        """Produz tokens fictícios distintos a cada renovação."""
        token = ("renewed-" if renewed else "access-") + user_id
        self.tokens[token] = user_id
        return {"access_token": token, "refresh_token": "refresh-" + user_id,
                "expires_at": self.now + 3600, "user": {"id": user_id, "is_anonymous": False,
                "user_metadata": {"role": "Master"}}}

    def __call__(self, method, url, headers, payload, timeout):
        """Responde sem rede e permite injetar falhas em cada endpoint."""
        parsed = urlsplit(url)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        data = json.loads(payload) if payload else {}
        self.calls.append({"method": method, "path": path, "headers": dict(headers), "data": data})
        if path in self.failures:
            failure = self.failures[path]
            if isinstance(failure, Exception):
                raise failure
            return HTTPResponse(failure, b"erro_interno_ficticio")
        token = headers.get("Authorization", "Bearer ").removeprefix("Bearer ")
        user_id = self.tokens.get(token)
        if path == "/auth/v1/token?grant_type=password":
            if data.get("email") not in self.users or data.get("password") != PASSWORD:
                return HTTPResponse(400, b"senha_invalida")
            result = self.session(self.users[data["email"]])
        elif path == "/auth/v1/token?grant_type=refresh_token":
            current = data["refresh_token"].removeprefix("refresh-")
            result = self.session(self.refreshed_identity or current, renewed=True)
        elif path == "/auth/v1/user":
            result = {"id": user_id, "user_metadata": {"role": "Master"}}
        elif path == "/rest/v1/rpc/app_context":
            result = copy.deepcopy(self.contexts.get(user_id))
        elif path == "/auth/v1/admin/users":
            result = {"id": USER_C}
        elif path == "/auth/v1/verify":
            if data.get("token") != "123456":
                return HTTPResponse(400, b"otp_invalido")
            result = self.session(self.users[data["email"]])
        elif path == "/auth/v1/logout?scope=global":
            return HTTPResponse(204, b"")
        else:
            result = {"ok": True}
        return HTTPResponse(200, json.dumps(result).encode())


@pytest.fixture
def backend():
    """Fornece cliente e serviço novos para cada teste, sem herdar sessão anterior."""
    transport = FakeAuthTransport()
    config = Settings(PROJECT_URL, "sb_publishable_ficticio", service_role_key="sb_secret_ficticio")
    auth = AuthService(SupabaseAPI(config, transport), clock=lambda: transport.now)
    return auth, transport


def test_login_valido_ignora_role_master_em_user_metadata(backend):
    """O papel enviado pelo cliente/Auth metadata não substitui o perfil protegido."""
    auth, transport = backend
    context = auth.login("operador@example.test", PASSWORD)
    assert context.profile.role == "Operador"
    assert context.can("inventory.view")
    assert not context.can("users.manage")
    assert not context.can("finance.view")
    assert auth.session.access_token not in repr(auth.session)
    assert auth.session.refresh_token not in repr(auth.session)
    assert transport.calls[-1]["path"] == "/rest/v1/rpc/app_context"


@pytest.mark.parametrize("email,password", [("operador@example.test", "errada"), ("ausente@example.test", PASSWORD)])
def test_login_invalido_nao_consulta_dados_internos(backend, email, password):
    """Senha errada e conta inexistente produzem mesma negação sem consultar o banco."""
    auth, transport = backend
    with pytest.raises(AuthenticationError, match="Acesso não autorizado"):
        auth.login(email, password)
    assert auth.session is None
    assert len(transport.calls) == 1
    assert transport.calls[0]["path"].startswith("/auth/v1/token")


@pytest.mark.parametrize("profile", [None, {"profile": {"active": False}, "permissions": []}])
def test_auth_sem_perfil_ativo_nao_libera_aplicacao(backend, profile):
    """Uma identidade Auth válida não é suficiente para acessar dados do aplicativo."""
    auth, transport = backend
    transport.contexts[USER_A] = profile
    with pytest.raises(AuthenticationError):
        auth.login("operador@example.test", PASSWORD)
    assert auth.session is None


def test_sem_login_nenhum_rpc_e_executado(backend):
    """Nem a primeira consulta do contexto ocorre anonimamente pelo fluxo comum."""
    auth, transport = backend
    with pytest.raises(AuthenticationError):
        auth.rpc("load_inventory", {}, "inventory.view")
    assert not transport.calls


def test_permissao_revogada_e_revalidada_antes_da_operacao(backend):
    """Uma permissão retirada durante a sessão impede a próxima consulta de dados."""
    auth, transport = backend
    auth.login("operador@example.test", PASSWORD)
    transport.contexts[USER_A]["permissions"] = ["dashboard.view"]
    transport.calls.clear()
    with pytest.raises(AuthorizationError):
        auth.rpc("load_inventory", {}, "inventory.view")
    assert [c["path"] for c in transport.calls] == ["/auth/v1/user", "/rest/v1/rpc/app_context"]


def test_desativacao_durante_sessao_limpa_tokens_e_nega_operacao(backend):
    """O estado ativo é consultado novamente, sem esperar a expiração do JWT."""
    auth, transport = backend
    auth.login("operador@example.test", PASSWORD)
    transport.contexts[USER_A]["profile"]["active"] = False
    with pytest.raises(AuthorizationError):
        auth.rpc("load_inventory", {}, "inventory.view")
    assert auth.session is None


def test_renovacao_antes_da_expiracao_troca_tokens(backend):
    """Uma sessão prestes a vencer usa o refresh token antes de acessar o banco."""
    auth, transport = backend
    auth.login("operador@example.test", PASSWORD)
    transport.now = auth.session.expires_at - 30
    transport.calls.clear()
    auth.require("inventory.view")
    assert transport.calls[0]["path"] == "/auth/v1/token?grant_type=refresh_token"
    assert auth.session.access_token.startswith("renewed-")
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer " + auth.session.access_token


def test_refresh_revogado_exige_novo_login_sem_consulta_interna(backend):
    """Falha na renovação não reutiliza o token anterior."""
    auth, transport = backend
    auth.login("operador@example.test", PASSWORD)
    transport.now = auth.session.expires_at + 1
    transport.failures["/auth/v1/token?grant_type=refresh_token"] = 400
    transport.calls.clear()
    with pytest.raises(AuthenticationError):
        auth.context()
    assert auth.session is None
    assert len(transport.calls) == 1


def test_refresh_nao_aceita_identidade_diferente(backend):
    """Nem uma resposta Auth inesperada mistura a identidade de duas sessões."""
    auth, transport = backend
    auth.login("operador@example.test", PASSWORD)
    transport.now = auth.session.expires_at
    transport.refreshed_identity = USER_B
    with pytest.raises(AuthenticationError):
        auth.context()
    assert auth.session is None


def test_get_user_com_identidade_diferente_e_negado(backend):
    """A identidade retornada pelo Auth deve coincidir com a sessão emitida."""
    auth, transport = backend
    auth.login("operador@example.test", PASSWORD)
    transport.tokens[auth.session.access_token] = USER_B
    with pytest.raises(AuthenticationError):
        auth.context()
    assert auth.session is None


def test_logout_revoga_remotamente_e_limpa_localmente(backend):
    """Após logout, o objeto de sessão não pode ser usado para nova consulta."""
    auth, transport = backend
    auth.login("operador@example.test", PASSWORD)
    auth.logout()
    assert auth.session is None
    assert transport.calls[-1]["path"] == "/auth/v1/logout?scope=global"
    with pytest.raises(AuthenticationError):
        auth.require("dashboard.view")


def test_logout_em_falha_de_rede_ainda_remove_tokens(backend):
    """A interface informa revogação remota não confirmada sem manter login local."""
    auth, transport = backend
    auth.login("operador@example.test", PASSWORD)
    transport.failures["/auth/v1/logout?scope=global"] = TimeoutError("segredo_ficticio")
    with pytest.raises(ServiceUnavailable, match="revogação remota"):
        auth.logout()
    assert auth.session is None


def test_duas_sessoes_nao_compartilham_identidade_nem_permissoes(backend):
    """Um segundo login não troca o token ou o contexto do primeiro navegador."""
    auth_a, transport = backend
    auth_b = AuthService(SupabaseAPI(auth_a.api.settings, transport), clock=lambda: transport.now)
    auth_a.login("operador@example.test", PASSWORD)
    token_a = auth_a.session.access_token
    auth_b.login("visualizador@example.test", PASSWORD)
    assert auth_a.session.access_token == token_a
    assert auth_a.context().profile.user_id == USER_A
    assert auth_b.context().profile.user_id == USER_B
    assert not auth_b.context().can("inventory.view")
    auth_b.logout()
    assert auth_a.session is not None


def test_limite_local_de_tentativas_nao_faz_novas_chamadas(backend):
    """A limitação por navegador complementa o rate limit configurado no Auth."""
    auth, transport = backend
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            auth.login("operador@example.test", "errada")
    calls = len(transport.calls)
    with pytest.raises(RateLimited):
        auth.login("operador@example.test", PASSWORD)
    assert len(transport.calls) == calls
    transport.now += 61
    assert auth.login("operador@example.test", PASSWORD).profile.user_id == USER_A


def test_operador_nao_pode_usar_chave_administrativa(backend):
    """Possuir configuração de servidor não dá acesso ao endpoint administrativo."""
    auth, transport = backend
    auth.login("operador@example.test", PASSWORD)
    with pytest.raises(AuthorizationError):
        auth.create_user("novo@example.test", PASSWORD, "Fictício", "Operador", {})
    assert not any(c["path"] == "/auth/v1/admin/users" for c in transport.calls)


def test_master_cria_conta_com_chave_admin_so_no_auth(backend):
    """A autorização e a atribuição de perfil usam o JWT Master, não service role."""
    auth, transport = backend
    transport.contexts[USER_A] = transport.make_context(USER_A, ["users.manage", "permissions.manage"], "Master")
    auth.login("operador@example.test", PASSWORD)
    assert auth.create_user("novo@example.test", PASSWORD, "Fictício", "Visualizador", {}) == USER_C
    admin = [c for c in transport.calls if c["headers"]["apikey"] == "sb_secret_ficticio"]
    assert len(admin) == 1
    assert admin[0]["path"] == "/auth/v1/admin/users"
    assert "app_metadata" not in admin[0]["data"]
    profile = transport.calls[-1]
    assert profile["path"] == "/rest/v1/rpc/admin_set_user"
    assert profile["headers"]["apikey"] == "sb_publishable_ficticio"
    assert profile["data"]["p_user_id"] == USER_C
    assert profile["data"]["p_role"] == "Visualizador"


def test_nem_master_pode_criar_outro_master_pela_interface(backend):
    """O bootstrap protegido permanece fora de qualquer formulário público ou privado."""
    auth, transport = backend
    with pytest.raises(AuthorizationError):
        auth.create_user("novo@example.test", PASSWORD, "Fictício", "Master", {})
    assert not transport.calls


def test_recovery_envio_desabilitado_nao_acessa_auth(backend):
    """Sem configuração externa, não existe tentativa de envio real."""
    auth, transport = backend
    with pytest.raises(ConfigurationError):
        auth.request_password_reset("operador@example.test")
    assert not transport.calls


def test_recovery_habilitado_envia_somente_email_sem_redirecionamento(backend):
    """A recuperação usa código digitado, evitando tokens em URL ou parâmetros."""
    _, transport = backend
    config = Settings(PROJECT_URL, "sb_publishable_ficticio", auth_email_enabled=True)
    auth = AuthService(SupabaseAPI(config, transport), clock=lambda: transport.now)
    auth.request_password_reset("operador@example.test")
    assert transport.calls[0]["path"] == "/auth/v1/recover"
    assert transport.calls[0]["data"] == {"email": "operador@example.test"}


def test_otp_recebido_externamente_permite_troca_sem_habilitar_envio(backend):
    """Verificar um código existente não dispara e-mail nem depende do gate de envio."""
    auth, transport = backend
    auth.recover_password("operador@example.test", "123456", "Outra-senha-ficticia-987")
    assert auth.session is None
    assert transport.calls[0]["data"]["type"] == "recovery"
    changes = [c for c in transport.calls if c["method"] == "PUT"]
    assert len(changes) == 1
    assert changes[0]["data"] == {"password": "Outra-senha-ficticia-987"}
    assert all("recover" not in c["path"] for c in transport.calls)


def test_otp_invalido_nao_altera_senha(backend):
    """Um código incorreto deixa o usuário desautenticado."""
    auth, transport = backend
    with pytest.raises(AuthenticationError):
        auth.recover_password("operador@example.test", "999999", PASSWORD)
    assert auth.session is None
    assert not any(c["method"] == "PUT" for c in transport.calls)


@pytest.mark.parametrize("password", ["curta", "a" * 16, "Abcdefghijklm1", "123456789012!", "x" * 129])
def test_senha_fraca_nao_e_enviada(password):
    """Criação e alteração exigem uma política mínima explícita."""
    with pytest.raises(AppError):
        validate_password(password)


def test_sem_finance_view_nao_dispara_consulta_financeira(backend):
    """A autorização do backend não depende de ocultar botão na interface."""
    auth, transport = backend
    auth.login("operador@example.test", PASSWORD)
    transport.calls.clear()
    with pytest.raises(AuthorizationError):
        auth.rpc("load_inventory", {"p_module": "finance.view"}, "finance.view")
    assert not any(c["path"].endswith("load_inventory") for c in transport.calls)
