"""GET de todos os saldos reais com observação histórica, sem compra ou POST."""
import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_observational_review import payload
from test_real_home_status import AuthFake

from analytics.observational_review import conferir_observacao
from integracoes.catalog_full import CatalogoCompleto
from integracoes.catalog_preview import ProdutoCatalogo, VariacaoCatalogo
from integracoes.gestaoclick import ContratoInvalido
from reposicao.quadro_real import construir_quadro_real, exportar_quadro_real_csv
from web import real_batch_replenishment_page as tela
from web.errors import AuthorizationError

AGORA = datetime(2026, 11, 23, 19, 55, tzinfo=timezone.utc)


def catalogo(*, loja="416023", produtos=None):
    if produtos is None:
        produtos = (
            ProdutoCatalogo("101", "COD101", Decimal("2"), (), "Perfume A", "Perfumaria"),
            ProdutoCatalogo("102", "COD102", Decimal("0"), (), "Perfume B", None),
            ProdutoCatalogo("103", "COD103", Decimal("10"), (), "Perfume C", "Cosméticos"),
        )
    return CatalogoCompleto(
        loja, tuple(produtos), len(produtos), 1,
        sum((p.estoque or Decimal(0) for p in produtos), Decimal(0)),
        sum(p.estoque is None for p in produtos),
        0, 0, 0, 0, "checksum",
    )


def observacao(*, loja="416023"):
    bruto = payload()
    bruto["store_id"] = loja
    return conferir_observacao(bruto, loja)


def test_junta_id_da_mesma_loja_sem_subtrair_vendas_do_saldo_atual():
    quadro = construir_quadro_real(catalogo(), observacao(), "416023", AGORA)
    assert quadro.loja_id == "416023"
    assert quadro.produtos == 3
    assert quadro.produtos_com_saldo == 3
    assert quadro.produtos_com_venda_observada == 2
    assert quadro.periodo_inicio.isoformat() == "2026-11-16"
    linhas = {r["Produto (ID)"]: r for r in quadro.linhas}
    assert linhas["101"]["Saldo GET (unidade do ERP não conferida)"] == "2"
    assert linhas["101"]["Vendas fechadas observadas (UN)"] == "3"
    assert linhas["102"]["Saldo GET (unidade do ERP não conferida)"] == "0"
    assert linhas["103"]["Vendas fechadas observadas (UN)"] == "Não observado no período"
    assert [r["Produto (ID)"] for r in quadro.linhas] == ["101", "102", "103"]


def test_sem_observacao_nao_significa_venda_zero():
    quadro = construir_quadro_real(catalogo(), None, "416023", AGORA)
    assert quadro.produtos_com_venda_observada == 0
    assert quadro.periodo_inicio is None
    assert all(row["Vendas fechadas observadas (UN)"] == "Não observado no período"
               for row in quadro.linhas)


def test_saldo_ausente_nao_vira_zero_e_variacoes_exigem_conferencia():
    v = VariacaoCatalogo("77", "A", Decimal("5"), "999")
    dados = (
        ProdutoCatalogo("101", "COD101", None, (v,), "Perfume A", None),
    )
    quadro = construir_quadro_real(catalogo(produtos=dados), None, "416023", AGORA)
    assert quadro.produtos_com_saldo == 0
    assert quadro.linhas[0]["Saldo GET (unidade do ERP não conferida)"] == "Não informado"
    assert quadro.linhas[0]["Variações"] == "Conferir unidades/variações"


def test_nao_mistura_vendas_de_loja_alheia():
    with pytest.raises(ContratoInvalido, match="outra loja"):
        construir_quadro_real(catalogo(), observacao(loja="424409"), "416023", AGORA)
    with pytest.raises(ContratoInvalido, match="loja"):
        construir_quadro_real(catalogo(loja="424409"), observacao(), "416023", AGORA)


def test_linhas_observadas_sem_produto_atual_nao_sao_conciliadas():
    so_um = (ProdutoCatalogo("101", "COD101", Decimal(4), (), "Perfume A", None),)
    quadro = construir_quadro_real(catalogo(produtos=so_um), observacao(), "416023", AGORA)
    assert quadro.observados_sem_catalogo == 1
    assert quadro.produtos_com_venda_observada == 1
    assert len(quadro.linhas) == 1


def test_catalogo_duplicado_ou_sem_data_correta_e_bloqueado():
    com_duplicados = (
        ProdutoCatalogo("101", None, Decimal(2), (), "A", None),
        ProdutoCatalogo("101", None, Decimal(4), (), "B", None),
    )
    with pytest.raises(ContratoInvalido, match="duplicado"):
        construir_quadro_real(catalogo(produtos=com_duplicados), None, "416023", AGORA)
    with pytest.raises(ContratoInvalido, match="catálogo"):
        construir_quadro_real(catalogo(), None, "416023", AGORA.replace(tzinfo=None))


def test_exportacao_escapa_formula_e_nao_contem_dados_pessoais():
    itens = (ProdutoCatalogo("101", "=SOMA(1;1)", Decimal(3), (), "+COMANDO", "Perfumaria"),)
    quadro = construir_quadro_real(catalogo(produtos=itens), None, "416023", AGORA)
    linhas = list(csv.reader(io.StringIO(exportar_quadro_real_csv(quadro).decode("utf-8-sig")), delimiter=";"))
    assert len(linhas) == 2
    assert linhas[1][4] == "'+COMANDO"
    assert linhas[1][6] == "'=SOMA(1;1)"
    assert linhas[1][7] == "101"
    assert linhas[1][9] == "Não observado no período"
    assert not any("cliente" in cel.lower() for linha in linhas for cel in linha)


def test_catalogo_get_autorizado_sem_escritas_no_erp(monkeypatch):
    auth = AuthFake()
    actions = []
    conta = SimpleNamespace(lojas=[{"id": "416023", "nome": "Origem"}])
    def conferir_conta(*args, **kwargs):
        actions.append(("conta", kwargs["autorizado"]))
        return conta
    def conferir_catalogo(*args, **kwargs):
        actions.append(("catálogo", args[3], kwargs["autorizado"]))
        return catalogo()
    monkeypatch.setattr(tela, "consultar_conta", conferir_conta)
    monkeypatch.setattr(tela, "consultar_catalogo_completo", conferir_catalogo)
    monkeypatch.setattr(tela, "validar_conta", lambda *args: actions.append(("validar",)))
    config = Mock()
    retorno, instante = tela.consultar_catalogo_para_quadro(
        auth, "416023", autorizado=True, carregar_configuracao=config,
    )
    assert retorno.total_informado == 3
    assert instante.tzinfo is not None
    assert actions == [("conta", True), ("catálogo", "416023", True), ("validar",)]
    assert not auth.calls
    config.assert_not_called()


def test_sem_consentimento_ou_perfil_master_nao_consulta_erp(monkeypatch):
    consultar = Mock(side_effect=AssertionError("GET proibido"))
    monkeypatch.setattr(tela, "consultar_conta", consultar)
    with pytest.raises(AuthorizationError):
        tela.consultar_catalogo_para_quadro(AuthFake(), "416023", autorizado=False)
    with pytest.raises(AuthorizationError):
        tela.consultar_catalogo_para_quadro(AuthFake(role="Operador"), "416023", autorizado=True)
    consultar.assert_not_called()


def test_loja_que_nao_pertence_a_conta_e_recusada(monkeypatch):
    monkeypatch.setattr(
        tela, "consultar_conta",
        lambda *args, **kwargs: SimpleNamespace(lojas=[{"id": "424409"}]),
    )
    catalog = Mock(side_effect=AssertionError("Não consultar catálogo da outra loja"))
    monkeypatch.setattr(tela, "consultar_catalogo_completo", catalog)
    with pytest.raises(AuthorizationError, match="loja"):
        tela.consultar_catalogo_para_quadro(AuthFake(), "416023", autorizado=True)
    catalog.assert_not_called()


def test_revogacao_durante_get_invalida_resultado(monkeypatch):
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
    with pytest.raises(AuthorizationError, match="acesso"):
        tela.consultar_catalogo_para_quadro(auth, "416023", autorizado=True)
