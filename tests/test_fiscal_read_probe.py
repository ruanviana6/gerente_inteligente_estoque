"""Testes de leitura fiscal isolada, com ERP inteiramente fictício."""
import json
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from test_gestaoclick_account_setup import (
    ACCESS_TOKEN,
    PRIVADO,
    SECRET_TOKEN,
    TransporteFicticio,
    consultar,
)
from test_gestaoclick_account_setup import auth as auth
from test_gestaoclick_account_setup import configuracao as configuracao

from integracoes.fiscal_read_probe import consultar_recurso_nfe
from integracoes.gestaoclick import ContratoInvalido, GestaoClickError, RespostaGestaoClick
from web.errors import AuthorizationError


@pytest.fixture(autouse=True)
def bloquear_rede(monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener", Mock(side_effect=AssertionError("Rede proibida")))


@pytest.fixture
def conta(auth, configuracao):
    return consultar(auth, configuracao)[0]


def resposta(rows, status=200, **extras):
    payload = {"code": 200, "status": "success", "data": rows, **extras}
    return RespostaGestaoClick(status, json.dumps(payload).encode())


def rodar(auth, config, conta, retorno, *, autorizado=True, depois=None):
    transporte = TransporteFicticio(retorno, depois=depois)
    resultado = consultar_recurso_nfe(
        auth, lambda: config, conta, "001", autorizado=autorizado,
        transporte=transporte,
    )
    return resultado, transporte


@pytest.mark.parametrize("rows", [[], [{
    "id": "123", "cliente": PRIVADO, "chave_nfe": SECRET_TOKEN, "valor": "1.000",
}]])
def test_get_unico_sem_pii_sem_alterar_erp(auth, configuracao, conta, rows):
    resultado, transporte = rodar(auth, configuracao, conta, resposta(rows))
    assert resultado["escrita_habilitada"] is False
    assert resultado["cobertura_comprovada"] is False
    assert resultado["loja_efetiva_comprovada"] is False
    assert len(transporte.calls) == 1
    url, headers, timeout = transporte.calls[0]
    assert urlsplit(url).path == "/notas_fiscais_produtos"
    assert parse_qs(urlsplit(url).query) == {
        "loja_id": ["001"], "pagina": ["1"], "limite": ["1"],
    }
    assert headers["access-token"] == ACCESS_TOKEN
    assert headers["secret-access-token"] == SECRET_TOKEN
    assert timeout <= 15
    for privado in (PRIVADO, SECRET_TOKEN, ACCESS_TOKEN, "1.000", "123"):
        assert privado not in str(resultado)
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("consentimento", [None, False, "true", 1])
def test_sem_autorizacao_nao_le_credenciais(auth, conta, consentimento):
    carregar = Mock()
    transporte = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_recurso_nfe(auth, carregar, conta, "001",
                              autorizado=consentimento, transporte=transporte)
    carregar.assert_not_called()
    assert not transporte.calls


def test_loja_fora_da_conta_rejeitada(auth, configuracao, conta):
    transporte = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_recurso_nfe(auth, lambda: configuracao, conta, "999",
                              autorizado=True, transporte=transporte)
    assert not transporte.calls


@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 429, 500])
def test_http_bloqueado_sem_vazamento(auth, configuracao, conta, status):
    retorno = RespostaGestaoClick(status, (PRIVADO + SECRET_TOKEN).encode(),
                                 {"Location": "https://externo.invalid/" + ACCESS_TOKEN})
    transporte = TransporteFicticio(retorno)
    with pytest.raises(GestaoClickError) as exc:
        consultar_recurso_nfe(auth, lambda: configuracao, conta, "001",
                              autorizado=True, transporte=transporte)
    assert SECRET_TOKEN not in str(exc.value) and PRIVADO not in str(exc.value)
    assert len(transporte.calls) == 1


@pytest.mark.parametrize("retorno", [
    resposta({"id": 1}),
    resposta([{}] * 2),
    resposta([], code="200"),
    RespostaGestaoClick(200, b'{"code":200,"code":200,"status":"success","data":[]}'),
])
def test_contrato_divergente_nao_expoe_payload(auth, configuracao, conta, retorno):
    with pytest.raises(ContratoInvalido):
        rodar(auth, configuracao, conta, retorno)


def test_revogacao_e_troca_de_secrets_abortam(auth, configuracao, conta):
    geracao_anterior = auth.generation
    transporte = TransporteFicticio(resposta([]), depois=lambda n: setattr(auth, "generation", auth.generation+1))
    with pytest.raises(AuthorizationError):
        consultar_recurso_nfe(auth, lambda: configuracao, conta, "001",
                              autorizado=True, transporte=transporte)
    assert len(transporte.calls) == 1
    # Restaura a sessão original antes do segundo cenário independente.
    auth.generation = geracao_anterior
    transporte = TransporteFicticio(resposta([]), depois=lambda n: configuracao.update(GESTAOCLICK_ACCESS_TOKEN="NOVO"))
    with pytest.raises(AuthorizationError):
        consultar_recurso_nfe(auth, lambda: configuracao, conta, "001",
                              autorizado=True, transporte=transporte)
    assert len(transporte.calls) == 1
