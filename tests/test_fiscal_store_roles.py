"""Confere papéis fiscais sem alterar o ERP, com lojas fictícias."""
import pytest

from fiscal.lojas import validar_lojas_fiscais
from integracoes.gestaoclick import ContratoInvalido

LOJAS = [  # IDs inteiramente fictícios; não pressupõem IDs reais da origem/destino
    {"id": "101", "nome": "FLT Comércio"},
    {"id": "202", "nome": "Flagship - Flatter"},
    {"id": "999", "nome": "Outra loja"},
]


def test_confirmacao_origem_destino_com_ids_distintos():
    assert validar_lojas_fiscais(LOJAS, "101", "202") == ("101", "202")


def test_maiusculas_acentos_e_hifens_tipograficos():
    nomes = [
        {"id": "1", "nome": "FLT COMERCIO"},
        {"id": "2", "nome": "FLAGSHIP — FLATTER"},
    ]
    assert validar_lojas_fiscais(nomes, "1", "2") == ("1", "2")


@pytest.mark.parametrize(("origem", "destino"), [
    (None, "202"), ("101", None), ("", "202"),
    ("101", "101"), ("202", "101"),
    ("101", "999"), ("não-existe", "202"),
])
def test_inverte_omite_ou_troca_loja_bloqueia(origem, destino):
    with pytest.raises(ContratoInvalido):
        validar_lojas_fiscais(LOJAS, origem, destino)


def test_nao_inventa_correspondencia_por_prefixo_nome_ou_selecao():
    lojas = [
        {"id": "101", "nome": "FLT Comércio Teste"},
        {"id": "202", "nome": "Flagship - Flatter"},
    ]
    with pytest.raises(ContratoInvalido):
        validar_lojas_fiscais(lojas, "101", "202")


def test_catalogo_ficticio_duplicado_bloqueia():
    duplicado = LOJAS + [{"id": "202", "nome": "Flagship - Flatter"}]
    with pytest.raises(ContratoInvalido):
        validar_lojas_fiscais(duplicado, "101", "202")
