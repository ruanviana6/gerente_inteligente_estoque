"""Importação fictícia de embalagens sem alterações parciais ou SKUs inferidos."""

import pytest

from reposicao.embalagens import ler_embalagens


def test_csv_preserva_zeros_no_sku_e_revisao_da_previa():
    linhas = ler_embalagens(b"sku;unidades_por_caixa;vigencia\n001;6;2026-01-01\n", {"001"}, {"001": "versao"})
    assert linhas[0]["sku"] == "001"
    assert linhas[0]["units_per_box"] == 6
    assert linhas[0]["expected"] == "versao"


@pytest.mark.parametrize("conteudo", [
    b"sku;unidades_por_caixa;vigencia\n001;0;2026-01-01\n",
    b"sku;unidades_por_caixa;vigencia\n001;1.5;2026-01-01\n",
    b"sku;unidades_por_caixa;vigencia\n002;6;2026-01-01\n",
    b"sku;unidades_por_caixa;vigencia\n001;6;2026-01-01\n001;3;2026-01-01\n",
    b"sku;unidades_por_caixa;vigencia;desconhecido\n001;6;2026-01-01;x\n",
])
def test_csv_invalido_nao_gera_previa(conteudo):
    with pytest.raises(ValueError):
        ler_embalagens(conteudo, {"001"}, {})
