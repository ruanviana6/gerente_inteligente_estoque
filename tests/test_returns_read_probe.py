"""Diagnóstico de devoluções: testes com transporte fictício e sem rede."""
import json
from datetime import date
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from test_gestaoclick_account_setup import PRIVADO, SECRET_TOKEN, TransporteFicticio, consultar
from test_gestaoclick_account_setup import auth as auth
from test_gestaoclick_account_setup import configuracao as configuracao

from integracoes.gestaoclick import ContratoInvalido, GestaoClickError, RespostaGestaoClick
from integracoes.returns_read_probe import consultar_recurso_devolucoes
from web.errors import AuthorizationError

INICIO = date(2025, 1, 10)
FIM = date(2025, 1, 16)


@pytest.fixture(autouse=True)
def bloquear_rede(monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener", Mock(side_effect=AssertionError("Rede proibida")))


@pytest.fixture
def conta(auth, configuracao):
    return consultar(auth, configuracao)[0]


def resposta(rows, status=200):
    return RespostaGestaoClick(status, json.dumps({
        "code": 200, "status": "success", "data": rows,
    }).encode())


@pytest.mark.parametrize("rows,confere", [
    ([], None),
    ([{"id": "123", "loja_id": "001", "pedido_id": "456", "cliente": PRIVADO,
       "produtos": [{"valor": SECRET_TOKEN}]}], True),
    ([{"loja_id": "002"}], False),
])
def test_get_minimo_sem_dados_privados(auth, configuracao, conta, rows, confere):
    transporte = TransporteFicticio(resposta(rows))
    result = consultar_recurso_devolucoes(
        auth, lambda: configuracao, conta, "001", INICIO, FIM,
        autorizado=True, transporte=transporte,
    )
    assert result["loja_no_registro_confere"] is confere
    assert result["itens_amostra"] == len(rows)
    assert not result["apta_reposicao"] and not result["cobertura_comprovada"]
    assert PRIVADO not in str(result) and SECRET_TOKEN not in str(result)
    assert "123" not in str(result) and "456" not in str(result)
    if confere is True:
        assert result["tipos_campos_candidatos"]["produtos"] == "lista"
        assert result["tipos_campos_candidatos"]["pedido_id"] == "texto"
    assert len(transporte.calls) == 1
    url, _, timeout = transporte.calls[0]
    assert urlsplit(url).path == "/devolucoes"
    assert parse_qs(urlsplit(url).query) == {
        "loja_id": ["001"], "pagina": ["1"], "limite": ["1"],
        "data_inicio": ["2025-01-10"], "data_fim": ["2025-01-16"],
    }
    assert timeout <= 15
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("loja,autorizado", [("001", False), ("999", True), ("001", "true")])
def test_sem_permissao_nao_consulta(auth, conta, loja, autorizado):
    carregar = Mock()
    transporte = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_recurso_devolucoes(
            auth, carregar, conta, loja, INICIO, FIM,
            autorizado=autorizado, transporte=transporte,
        )
    carregar.assert_not_called()
    assert not transporte.calls


def test_periodo_invalido(auth, conta):
    carregar = Mock()
    transporte = TransporteFicticio()
    with pytest.raises(ContratoInvalido):
        consultar_recurso_devolucoes(
            auth, carregar, conta, "001", INICIO, date(2025, 1, 17),
            autorizado=True, transporte=transporte,
        )
    carregar.assert_not_called()
    assert not transporte.calls


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_http_recusado(auth, configuracao, conta, status):
    transporte = TransporteFicticio(resposta([], status))
    with pytest.raises(GestaoClickError):
        consultar_recurso_devolucoes(
            auth, lambda: configuracao, conta, "001", INICIO, FIM,
            autorizado=True, transporte=transporte,
        )


@pytest.mark.parametrize("rows", [[{}, {}], {}, [None]])
def test_formato_invalido(auth, configuracao, conta, rows):
    transporte = TransporteFicticio(resposta(rows))
    with pytest.raises(ContratoInvalido):
        consultar_recurso_devolucoes(
            auth, lambda: configuracao, conta, "001", INICIO, FIM,
            autorizado=True, transporte=transporte,
        )


def test_revogacao_descarta_resposta(auth, configuracao, conta):
    transporte = TransporteFicticio(
        resposta([]), depois=lambda _: setattr(auth, "generation", auth.generation + 1),
    )
    with pytest.raises(AuthorizationError):
        consultar_recurso_devolucoes(
            auth, lambda: configuracao, conta, "001", INICIO, FIM,
            autorizado=True, transporte=transporte,
        )
    auth.rpc.assert_not_called()
