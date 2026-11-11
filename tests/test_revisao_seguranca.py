"""Verificações independentes dos limites de sessão e de indicadores financeiros."""

import importlib.util
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest

from integracoes.gestaoclick import (
    ContratoGestaoClick,
    GestaoClickClient,
    GestaoClickConfig,
    GestaoClickError,
    RecursoConsulta,
    RespostaGestaoClick,
)
from sincronizacao.service import SyncCoordinator
from web.auth import AccessContext, Profile
from web.errors import AuthorizationError
from web.service import normalizar_payload

_spec = importlib.util.spec_from_file_location(
    "revisao_independente_fixture", Path(__file__).parent / "fixtures/flatter_data.py",
)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)

SOURCE = "00000000-0000-0000-0000-000000000501"
REQUEST = "00000000-0000-0000-0000-000000000502"
RUN = "00000000-0000-0000-0000-000000000503"
CLAIM = "00000000-0000-0000-0000-000000000504"


@pytest.mark.parametrize("permissao", ["finance.view", "trends.view"])
def test_revogacao_remove_indicadores_financeiros_indiretos_do_painel(permissao):
    """Textos de ABC e flags não podem sobreviver ao acesso revogado no cálculo."""
    scenario = _fixture.Scenario(size=3)
    service = scenario.service_factory({})

    def calcular_e_revogar(payload, financeiro=False):
        """Produz ABC parcial real e revoga acesso após o motor terminar."""
        payload["products"][0]["custo_unitario"] = None
        resultado = normalizar_payload(payload, financeiro)
        scenario.permissions.discard(permissao)
        return resultado

    with patch("web.service.normalizar_payload", side_effect=calcular_e_revogar):
        resultado = service.load_inventory("inventory.view")
    assert not resultado["_access_context"].can(permissao)
    avisos = " ".join(resultado["panel"]["aviso_qualidade"].fillna("")).lower()
    assert "abc" not in avisos
    if permissao == "finance.view":
        assert "custo" not in avisos
        assert "capital" not in avisos
        assert "custo_unitario" not in resultado["products"]
        assert "capital_estoque" not in resultado["panel"]


class SessaoFicticia:
    """Simula apenas autorização, sem tokens ou transporte de rede."""

    def __init__(self):
        """Cada teste começa com uma vida de sessão independente."""
        self.generation = 1
        self.context = AccessContext(
            Profile("00000000-0000-0000-0000-000000000601",
                    "00000000-0000-0000-0000-000000000602", "Master fictício", "Master", True),
            frozenset({"integrations.manage"}),
        )
        self.calls = []
        self.alterar_apos_rpc = False

    def require(self, permission):
        """Nega acesso sem concessão explícita em qualquer etapa."""
        if not self.context.can(permission):
            raise AuthorizationError("Acesso fictício revogado.")
        return self.context

    def operation(self):
        """Não existe concorrência local neste transporte determinístico."""
        return nullcontext()

    def rpc(self, name, payload, permission):
        """Registra a fronteira RPC e simula nova sessão da mesma identidade."""
        self.calls.append(name)
        if self.alterar_apos_rpc:
            self.generation += 1
        return {"id": RUN, "claim_token": CLAIM, "status": "staging"}


def test_sincronizacao_recusa_nova_sessao_da_mesma_conta_durante_extracao():
    """Logout e novo login não podem validar uma extração iniciada na sessão antiga."""
    auth = SessaoFicticia()

    def extrair():
        """Troca a vida da sessão preservando UUIDs e permissões."""
        auth.generation += 1
        return []

    with pytest.raises(AuthorizationError):
        SyncCoordinator(auth).prepare(source_id=SOURCE, store_id="loja-ficticia", request_key=REQUEST,
                                      start="2026-01-01", end="2026-01-31", read_all=extrair)
    assert auth.calls == ["sync_authorize_source"]


def test_sincronizacao_recusa_nova_sessao_da_mesma_conta_apos_rpc():
    """Uma resposta não deve continuar sendo usada depois da troca da sessão."""
    auth = SessaoFicticia()
    auth.alterar_apos_rpc = True
    with pytest.raises(AuthorizationError):
        SyncCoordinator(auth).commit(RUN, CLAIM, coverage_confirmed=False)
    assert auth.calls == ["sync_commit"]


def test_fonte_sem_autorizacao_no_banco_nao_dispara_consulta_externa():
    """Negação da fonte/loja precisa acontecer antes de utilizar os Secrets do ERP."""
    auth = SessaoFicticia()
    leituras = []

    def negar_rpc(name, payload, permission):
        """Simula um UUID válido pertencente a outra organização."""
        raise AuthorizationError("Fonte fictícia não pertence à organização ativa.")

    def consultar_origem():
        """Não deve ser invocado quando o banco recusa o escopo da fonte."""
        leituras.append(True)
        return []

    with patch.object(auth, "rpc", side_effect=negar_rpc), pytest.raises(AuthorizationError):
        SyncCoordinator(auth).prepare(source_id=SOURCE, store_id="loja-ficticia", request_key=REQUEST,
                                      start="2026-01-01", end="2026-01-31", read_all=consultar_origem)
    assert leituras == []


@pytest.mark.parametrize("enabled,url", [
    (False, "https://conta-ficticia.gestaoclick.com/api/vendas"),
    (True, "https://destino-nao-autorizado.invalid/api/vendas"),
    (True, "https://conta-ficticia.gestaoclick.com/api/clientes"),
])
def test_fronteira_http_recusa_destino_rota_ou_integracao_desabilitada(enabled, url):
    """Até chamadas internas precisam passar a validação antes de transmitir tokens."""
    base = "https://conta-ficticia.gestaoclick.com/api"
    config = GestaoClickConfig(enabled, base, "token-exclusivo-ficticio", "segredo-exclusivo-ficticio",
                              "fonte-ficticia", "loja-ficticia")
    contrato = ContratoGestaoClick(base, {"vendas": RecursoConsulta("vendas", "/vendas", frozenset())})
    chamadas = []

    class Transporte:
        """Captura qualquer transmissão indevida sem acessar rede."""

        def get(self, target, headers, timeout):
            """Uma tentativa de transmissão falha na asserção final do teste."""
            chamadas.append(target)
            return RespostaGestaoClick(200, b'{"code":200,"status":"success"}')

    client = GestaoClickClient(config, contrato, Transporte())
    with pytest.raises(GestaoClickError):
        client._get(url)
    assert chamadas == []
