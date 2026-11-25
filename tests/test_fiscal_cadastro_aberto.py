"""Verifica a preparação do contrato de NF-e com dados fictícios."""

import pytest

from fiscal.cadastro_aberto import ItemFiscalDestino, identificar_cadastro, preparar_cadastro_aberto
from fiscal.regras import EscolhasFiscais
from integracoes.gestaoclick import ContratoInvalido

LOJAS = [{"id": "31", "nome": "FLT Comércio"}, {"id": "42", "nome": "Flagship - Flatter"}]


def preparar(**ajustes):
    """Reutiliza os mesmos papéis e entradas fictícias nos cenários negativos."""
    parametros = dict(lojas=LOJAS, origem_id="31", destino_id="42", venda_id="901",
                     destinatario_destino_id=101, itens=(ItemFiscalDestino(102, 2, "33030010", 103),),
                     escolhas=EscolhasFiscais(1, 9))
    return preparar_cadastro_aberto(**(parametros | ajustes))


def test_prepara_nota_de_saida_com_fob_e_contribuinte_separados_do_corpo():
    """Grava a intenção de cadastro, sem usar ID da venda como vínculo de pedido."""
    preparo = preparar()
    assert preparo.corpo == {
        "loja_id": "42", "tipo_nf": 1, "envio_automatico": 0, "id_destinatario": 101,
        "informacoes_complementares": "Flatter origem 31 venda 901",
        "produtos": [{"produto_id": 102, "quantidade": 2, "NCM": "33030010", "variacao_id": 103}],
    }
    assert preparo.escolhas == EscolhasFiscais(1, 9)
    assert preparo.referencia_origem == preparo.corpo["informacoes_complementares"]


@pytest.mark.parametrize("ajustes", [
    {"destino_id": "31"}, {"origem_id": "42"}, {"venda_id": "9/10"},
    {"destinatario_destino_id": True}, {"itens": ()}, {"escolhas": None},
    {"itens": (ItemFiscalDestino(102, 1.5, "33030010"),)},
    {"itens": (ItemFiscalDestino(102, 1, "00000000"),)},
])
def test_recusa_papeis_identidades_e_dados_fiscais_invalidos(ajustes):
    """Uma entrada inválida não gera um candidato parcialmente preenchido."""
    with pytest.raises(ContratoInvalido):
        preparar(**ajustes)


def test_resposta_documentada_entrega_identificador():
    """O identificador retornado é insumo para verificar o estado em aberto."""
    assert identificar_cadastro({"code": 200, "status": "success",
                                 "data": {"codigo": 200, "ok": True, "dados": "223"}}) == "223"


@pytest.mark.parametrize("dados", [None, {}, {"codigo": 200, "ok": True, "dados": ""},
                                   {"codigo": 200, "ok": 1, "dados": "223"},
                                   {"codigo": 200, "ok": True, "dados": "223/emitir"}])
def test_resposta_ambigua_exige_conferencia(dados):
    """HTTP bem-sucedido não basta para confirmar o cadastro ou refazê-lo."""
    with pytest.raises(ContratoInvalido, match="Reconcilie"):
        identificar_cadastro({"code": 200, "status": "success", "data": dados})
