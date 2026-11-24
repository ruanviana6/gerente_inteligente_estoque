"""Testes da pesquisa fiscal: somente projeção de dados fictícios."""
from dataclasses import asdict
from datetime import date
from decimal import Decimal

import pytest

from fiscal.order_observation import observar_pedido_fiscal, situacao_para_conferencia_fiscal
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


@pytest.mark.parametrize(("situacao", "codigo"), [
    ("Em separação", "SITUACAO_COMPATIVEL"),
    ("CONCRETIZADA", "SITUACAO_COMPATIVEL"),
    ("Em andamento", "APROVACAO_NECESSARIA"),
    ("Cancelada", "SITUACAO_BLOQUEADA"),
    ("Orçamento", "SITUACAO_BLOQUEADA"),
    (None, "NAO_OBSERVADO"),
])
def test_classificacao_real_nao_autoriza_cadastro_fiscal(situacao, codigo):
    tipo, mensagem = situacao_para_conferencia_fiscal(situacao)
    assert tipo == codigo
    assert mensagem
    assert "POST" not in mensagem


def test_situacao_invalida_nao_e_aceita():
    with pytest.raises(ContratoInvalido):
        situacao_para_conferencia_fiscal("X" * 201)


def test_triagem_real_mostra_fob_e_isento_sem_acao_de_cadastro(monkeypatch):
    """A conferência usa escolhas explícitas sem chamar o executor fiscal."""
    from streamlit.testing.v1 import AppTest

    from web import fiscal_origin_lookup_page as pagina

    monkeypatch.setattr(pagina, "validar_conta", lambda *_: None)
    monkeypatch.setattr(pagina, "validar_lojas_fiscais", lambda *_: ("1", "2"))
    leituras = []
    def consultar(*args, **kwargs):
        leituras.append((args[3], kwargs["autorizado"]))
        return auditoria()
    monkeypatch.setattr(pagina, "consultar_vendas_integrais", consultar)

    app = AppTest.from_string("""
from contextlib import nullcontext
from types import SimpleNamespace
from web.fiscal_origin_lookup_page import fiscal_order_lookup_controls
auth = SimpleNamespace(operation=nullcontext, require=lambda _: None)
conta = SimpleNamespace(lojas=[], situacoes=[{"id": "1", "nome": "Em separação"}])
fiscal_order_lookup_controls(SimpleNamespace(auth=auth), None, conta, "1", "2")
""").run()
    assert not leituras
    assert app.selectbox(key="fiscal_real_frete").value is None
    assert app.selectbox(key="fiscal_real_contribuinte").value is None
    app.selectbox(key="fiscal_real_frete").select(1)
    app.selectbox(key="fiscal_real_contribuinte").select(2)
    app.text_input(key="fiscal_source_order_number").set_value("1001")
    app.checkbox(key="fiscal_source_order_consent").check()
    app.button[0].click().run()

    assert not app.exception
    assert leituras == [("1", True)]
    assert any("FOB" in item.value and "isento" in item.value for item in app.json)
    assert len(app.button) == 1
    assert "somente leitura" in app.button[0].label
