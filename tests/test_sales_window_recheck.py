"""Compara releituras da mesma janela comercial sem supor cancelamentos."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from test_gestaoclick_sales_audit import amostra
from test_real_home_status import AuthFake

from analytics.sales_window_recheck import assinar_janela, comparar_janelas
from integracoes.gestaoclick import ContratoInvalido
from integracoes.native_preview import DocumentoPreviaVenda, ItemPreviaVenda
from web import sales_window_recheck_page as pagina


def test_mesma_janela_identica_nao_comprova_cobertura():
    primeira = amostra()
    original = assinar_janela(primeira)
    segunda = assinar_janela(primeira, sal=original.sal)
    diff = comparar_janelas(original, segunda)
    assert diff.documentos_antes == 3
    assert diff.documentos_depois == 3
    assert (diff.novos_na_leitura, diff.nao_localizados_na_leitura,
            diff.situacoes_diferentes, diff.itens_diferentes, diff.datas_diferentes) == (
        0, 0, 0, 0, 0,
    )
    assert not diff.abrangencia_comprovada and not diff.apta_reposicao
    assert not original.abrangencia_comprovada and not original.apta_reposicao


def test_troca_de_ordem_de_documentos_e_itens_nao_cria_falsa_alteracao():
    base = amostra()
    invertido = tuple(replace(doc, itens=tuple(reversed(doc.itens)))
                      for doc in reversed(base.documentos))
    segunda = replace(base, documentos=invertido)
    referencia = assinar_janela(base)
    diff = comparar_janelas(referencia, assinar_janela(segunda, sal=referencia.sal))
    assert not any((
        diff.novos_na_leitura, diff.nao_localizados_na_leitura,
        diff.situacoes_diferentes, diff.itens_diferentes, diff.datas_diferentes,
    ))


def test_mudanca_de_situacao_e_quantidade_detectadas_sem_dobrar_pedido():
    base = amostra()
    primeiro = base.documentos[0]
    alterado = replace(primeiro, situacao_id="0", itens=(
        replace(primeiro.itens[0], quantidade=Decimal("11")),
        *primeiro.itens[1:],
    ))
    auditoria_nova = replace(base, documentos=(alterado, *base.documentos[1:]))
    referencia = assinar_janela(base)
    diff = comparar_janelas(referencia, assinar_janela(auditoria_nova, sal=referencia.sal))
    assert diff.situacoes_diferentes == 1
    assert diff.itens_diferentes == 1
    assert diff.novos_na_leitura == 0
    assert diff.nao_localizados_na_leitura == 0


def test_documento_desaparecido_e_novo_nao_provam_cancelamento():
    base = amostra()
    novo = DocumentoPreviaVenda(
        "venda-nova", "PED-4", base.inicio, "0", (
            ItemPreviaVenda("101", None, Decimal("9"), "UN"),
        ),
    )
    auditoria_nova = replace(
        base,
        documentos=(base.documentos[0], base.documentos[2], novo),
    )
    referencia = assinar_janela(base)
    diff = comparar_janelas(referencia, assinar_janela(auditoria_nova, sal=referencia.sal))
    assert diff.documentos_antes == diff.documentos_depois == 3
    assert diff.nao_localizados_na_leitura == 1
    assert diff.novos_na_leitura == 1
    assert not diff.apta_reposicao


def test_alteracao_de_data_e_de_unidade_identificada():
    base = amostra()
    doc = base.documentos[0]
    novos_itens = (replace(doc.itens[0], sigla_unidade="CX"), *doc.itens[1:])
    novo = replace(doc, data=doc.data + timedelta(days=1), itens=novos_itens)
    referencia = assinar_janela(base)
    outro = replace(base, documentos=(novo, *base.documentos[1:]))
    diff = comparar_janelas(referencia, assinar_janela(outro, sal=referencia.sal))
    assert diff.datas_diferentes == 1
    assert diff.itens_diferentes == 1


@pytest.mark.parametrize("mudanca", [
    {"loja_solicitada": "LOJA-DIFERENTE"},
    {"inicio": amostra().inicio + timedelta(days=1)},
    {"fim": amostra().fim - timedelta(days=1)},
])
def test_nao_compara_janelas_de_loja_ou_datas_distintas(mudanca):
    base = amostra()
    referencia = assinar_janela(base)
    outra = replace(base, **mudanca)
    with pytest.raises(ContratoInvalido, match="mesma loja"):
        comparar_janelas(referencia, assinar_janela(outra, sal=referencia.sal))


def test_sal_de_sessao_distinto_impede_cruzamento_e_resumo_nao_expoe_numero():
    base = amostra()
    a, b = assinar_janela(base), assinar_janela(base)
    assert a.sal != b.sal
    assert "venda-0" not in repr(a)
    assert "PED-0" not in repr(a)
    assert all("venda-" not in str(x) for x in a.assinaturas)
    with pytest.raises(ContratoInvalido, match="mesma loja"):
        comparar_janelas(a, b)


def test_contagem_invalida_e_documento_duplicado_sao_rejeitados():
    base = amostra()
    with pytest.raises(ContratoInvalido, match="contagem"):
        assinar_janela(replace(base, total_informado=2))
    with pytest.raises(ContratoInvalido, match="duplicado"):
        assinar_janela(replace(
            base, documentos=(base.documentos[0], base.documentos[0]),
            total_informado=2,
        ))


def test_itens_ilegais_ou_sal_invalido_bloqueiam_comparacao():
    base = amostra()
    with pytest.raises(ContratoInvalido, match="sal"):
        assinar_janela(base, sal="invalido")
    primeiro = base.documentos[0]
    with pytest.raises(ContratoInvalido, match="Itens"):
        assinar_janela(replace(
            base, documentos=(
                replace(primeiro, itens=(replace(primeiro.itens[0], quantidade=Decimal("NaN")),)),
                *base.documentos[1:],
            ),
        ))


def test_referencia_expira_ou_muda_de_loja_sem_consultar_erp(monkeypatch):
    auth = AuthFake()
    estado = {}
    monkeypatch.setattr(pagina.st, "session_state", estado)
    monkeypatch.setattr(pagina, "_stamp", lambda autenticacao: (autenticacao.generation,))
    pagina.guardar_referencia_vendas(auth, "001", "fonte-interna", amostra())
    assert pagina._referencia_valida(auth, "001") is not None
    assert pagina._referencia_valida(auth, "002") is None
    assert not estado
    pagina.guardar_referencia_vendas(auth, "001", "fonte-interna", amostra())
    auth.generation += 1
    assert pagina._referencia_valida(auth, "001") is None
    assert not estado
