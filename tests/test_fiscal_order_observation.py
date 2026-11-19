"""Testes da pesquisa fiscal: somente projeção de dados fictícios."""
from dataclasses import asdict
from datetime import date
from decimal import Decimal

import pytest

from fiscal.order_observation import observar_pedido_fiscal
from integracoes.gestaoclick import ContratoInvalido
from integracoes.native_preview import DocumentoPreviaVenda, ItemPreviaVenda
from integracoes.sales_audit import AuditoriaVendas

INICIO = date(2026, 1, 1)
FIM = date(2026, 1, 7)
PRIVADO = "CLIENTE-SECRETO-FICTICIO"
ITEM = ItemPreviaVenda("101", None, Decimal("2.00"), "UN")


def auditoria(codigos=("1001", "1002")):
    docs = tuple(DocumentoPreviaVenda(
        f"id_{i}", codigo, INICIO, "1", (ITEM,),
    ) for i, codigo in enumerate(codigos))
    return AuditoriaVendas("origem-ficticia", INICIO, FIM, docs, len(docs), 1, len(docs))


def test_observacao_unica_sem_permitir_cadastro():
    result = observar_pedido_fiscal(auditoria(), "1001", {"1": "Em separação"})
    assert result.encontrado_na_janela
    assert result.quantidade_linhas == 1
    assert result.situacao == "Em separação"
    assert result.data_venda == INICIO
    assert not result.loja_efetiva_comprovada
    assert not result.destino_ce_comprovado
    assert not result.nf_preexistente_verificada
    assert not result.autorizacao_cadastro
    assert PRIVADO not in str(asdict(result))
    assert "2.00" not in str(asdict(result))


def test_nao_observado_nao_comprova_inexistencia():
    result = observar_pedido_fiscal(auditoria(), "9999", {"1": "Em separação"})
    assert not result.encontrado_na_janela
    assert result.quantidade_linhas == 0
    assert result.situacao is None
    assert result.loja_solicitada == "origem-ficticia"
    assert not result.autorizacao_cadastro


@pytest.mark.parametrize("numero", ["", "1 OR 1", "100", "10010", "1001 ", "001001", "a" * 50, None, 1001])
def test_numero_exato_sem_injecao_ou_colisao(numero):
    if numero in ("100", "10010", "001001"):
        assert not observar_pedido_fiscal(auditoria(), numero, {"1": "Em separação"}).encontrado_na_janela
    else:
        with pytest.raises(ContratoInvalido):
            observar_pedido_fiscal(auditoria(), numero, {"1": "Em separação"})


def test_duplicidade_de_numero_bloqueia_a_correspondencia():
    with pytest.raises(ContratoInvalido, match="não é única"):
        observar_pedido_fiscal(auditoria(("1001", "1001")), "1001", {"1": "Em separação"})


def test_status_nao_catalogado_bloqueia():
    with pytest.raises(ContratoInvalido):
        observar_pedido_fiscal(auditoria(), "1001", {"2": "Concretizada"})


def test_nenhum_cliente_ou_tributo_e_projetado():
    result = observar_pedido_fiscal(auditoria(), "1002", {"1": "Concretizada"})
    assert set(asdict(result)).isdisjoint({
        "cliente", "cpf", "cnpj", "nome", "email", "endereco",
        "valor", "cfop", "ncm", "tribtos", "xml", "nota", "chave_nfe",
    })
