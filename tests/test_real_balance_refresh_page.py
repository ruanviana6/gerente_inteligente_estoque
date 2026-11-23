"""Consulta GET de saldo por produto real: identidade, loja e autorização."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_real_home_status import AuthFake

from integracoes.catalog_full import CatalogoCompleto
from integracoes.catalog_preview import ProdutoCatalogo, VariacaoCatalogo
from integracoes.gestaoclick import ContratoInvalido
from web import real_balance_refresh_page as tela
from web.errors import AuthorizationError


def catalogo(*, loja="416023", nome="Produto real", codigo="COD01",
             saldo=Decimal(12), variacoes=()):
    produto = ProdutoCatalogo(
        id="1001", codigo_interno=codigo, estoque=saldo,
        variacoes=tuple(variacoes), nome=nome, categoria="Perfumaria",
    )
    return CatalogoCompleto(
        loja, (produto,), 1, 1, saldo, 0, 0, 0, 0, 0, "checksum",
    )


def test_saldo_correto_na_mesma_loja_e_com_id_codigo_nome_iguais():
    dado = catalogo(saldo=Decimal("0"))
    assert tela.extrair_saldo_erp(dado, "416023", "1001", "Produto real", "COD01") == 0
    assert tela.extrair_saldo_erp(catalogo(saldo=Decimal("12.5")),
                                 "416023", "1001", "Produto real", "COD01") == Decimal("12.5")


@pytest.mark.parametrize(("alterar", "motivo"), [
    ({"loja": "424409"}, "loja"),
    ({"nome": "Outro nome"}, "nome"),
    ({"codigo": "OUTRO"}, "código"),
    ({"saldo": None}, "saldo"),
    ({"saldo": Decimal(-1)}, "saldo"),
    ({"saldo": Decimal("NaN")}, "saldo"),
    ({"saldo": Decimal("1e12")}, "saldo"),
])
def test_dados_duvidosos_nao_sao_copiados(alterar, motivo):
    with pytest.raises(ContratoInvalido, match=motivo):
        tela.extrair_saldo_erp(
            catalogo(**alterar), "416023", "1001", "Produto real", "COD01",
        )


@pytest.mark.parametrize("produto", ["1002", "1001;DROP", "", None])
def test_produto_nao_localizado_ou_id_invalido_nao_preenche_campo(produto):
    with pytest.raises(ContratoInvalido):
        tela.extrair_saldo_erp(catalogo(), "416023", produto, "Produto real", "COD01")


def test_variacao_nao_e_tratada_automaticamente_como_unidade_de_produto():
    variacao = VariacaoCatalogo(
        id="123", codigo="A", estoque=Decimal(5), variacao_api_id="456",
    )
    with pytest.raises(ContratoInvalido, match="variações"):
        tela.extrair_saldo_erp(
            catalogo(variacoes=(variacao,)), "416023", "1001", "Produto real", "COD01",
        )


def test_produto_com_identidade_incompleta_nao_e_aceito():
    with pytest.raises(ContratoInvalido, match="nome"):
        tela.extrair_saldo_erp(catalogo(nome=None), "416023", "1001", "Produto real", "COD01")
    with pytest.raises(ContratoInvalido):
        tela.extrair_saldo_erp(catalogo(), "416023", "1001", "", "COD01")
    with pytest.raises(ContratoInvalido, match="não corresponde"):
        tela.extrair_saldo_erp(
            replace(catalogo(), total_informado=3),
            "416023", "1001", "Produto real", "COD01",
        )


def test_uma_consulta_get_explicita_master_sem_mutacoes(monkeypatch):
    auth = AuthFake()
    chamada = []
    conta = SimpleNamespace(lojas=[{"id": "416023", "nome": "Origem"}])
    def conta_get(*args, **kwargs):
        chamada.append(("conta", kwargs["autorizado"]))
        return conta
    def catalogo_get(*args, **kwargs):
        chamada.append(("catalogo", args[3], kwargs["autorizado"]))
        return catalogo()
    monkeypatch.setattr(tela, "consultar_conta", conta_get)
    monkeypatch.setattr(tela, "consultar_catalogo_completo", catalogo_get)
    monkeypatch.setattr(tela, "validar_conta", lambda *args: chamada.append(("validar",)))
    carregar = Mock()
    resultado = tela.consultar_saldo_erp(
        auth, "416023", "1001", "Produto real", "COD01",
        autorizado=True, carregar_configuracao=carregar,
    )
    assert resultado.loja_id == "416023"
    assert resultado.produto_id == "1001"
    assert resultado.saldo == Decimal(12)
    assert resultado.consultado_em.tzinfo is not None
    assert chamada == [
        ("conta", True),
        ("catalogo", "416023", True),
        ("validar",),
    ]
    assert not auth.calls
    carregar.assert_not_called()


def test_sem_consentimento_nunca_consulta_conta_ou_catalogo(monkeypatch):
    auth = AuthFake()
    carregar = Mock(side_effect=AssertionError("nenhum secret deve ser lido"))
    acessar = Mock(side_effect=AssertionError("nenhum GET sem consentimento"))
    monkeypatch.setattr(tela, "consultar_conta", acessar)
    monkeypatch.setattr(tela, "consultar_catalogo_completo", acessar)
    with pytest.raises(AuthorizationError):
        tela.consultar_saldo_erp(
            auth, "416023", "1001", "Produto real", "COD01",
            autorizado=False, carregar_configuracao=carregar,
        )
    acessar.assert_not_called()
    carregar.assert_not_called()
    assert not auth.calls


def test_nao_master_e_loja_alheia_sao_recusados(monkeypatch):
    acesso = Mock()
    monkeypatch.setattr(tela, "consultar_conta", acesso)
    with pytest.raises(AuthorizationError):
        tela.consultar_saldo_erp(
            AuthFake(role="Operador"), "416023", "1001", "Produto real",
            "COD01", autorizado=True,
        )
    acesso.assert_not_called()
    auth = AuthFake()
    monkeypatch.setattr(
        tela, "consultar_conta",
        lambda *args, **kwargs: SimpleNamespace(lojas=[{"id": "424409", "nome": "Varejo"}]),
    )
    monkeypatch.setattr(tela, "consultar_catalogo_completo", acesso)
    with pytest.raises(AuthorizationError, match="loja"):
        tela.consultar_saldo_erp(
            auth, "416023", "1001", "Produto real", "COD01", autorizado=True,
        )
    acesso.assert_not_called()


def test_revogacao_durante_leitura_impede_retorno_do_saldo(monkeypatch):
    auth = AuthFake()
    monkeypatch.setattr(
        tela, "consultar_conta",
        lambda *args, **kwargs: SimpleNamespace(lojas=[{"id": "416023"}]),
    )
    def consultar(*args, **kwargs):
        auth.generation += 1
        return catalogo()
    monkeypatch.setattr(tela, "consultar_catalogo_completo", consultar)
    monkeypatch.setattr(tela, "validar_conta", lambda *args: None)
    with pytest.raises(AuthorizationError, match="autorização"):
        tela.consultar_saldo_erp(
            auth, "416023", "1001", "Produto real", "COD01", autorizado=True,
        )
