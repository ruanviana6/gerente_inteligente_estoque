"""Compatibilidade comprovada e rejeições da paginação de situações da conta.

Os formatos vêm da Central oficial e do OpenAPI público, mas todos os IDs,
nomes, respostas e credenciais usados aqui são exclusivamente fictícios.
"""

import json
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from test_gestaoclick_account_setup import (
    BASE,
    TransporteFicticio,
    lojas,
    resposta,
)
from test_gestaoclick_account_setup import auth as auth
from test_gestaoclick_account_setup import configuracao as configuracao
from test_gestaoclick_account_setup import sem_rede as sem_rede

from integracoes.account_setup import consultar_conta
from integracoes.gestaoclick import ContratoInvalido


def pagina(numero=1, total=2, limite=100, formato="moderno", *, limite_url=None, **changes):
    """Gera uma página exata com um dos dois conjuntos documentados de contadores."""
    start = (numero - 1) * limite
    data = [{"id": str(i), "nome": f"Situação fictícia {i}"} for i in range(start, min(start + limite, total))]
    pages = max(1, (total + limite - 1) // limite)
    next_page = numero + 1 if numero < pages else None
    url_limit = limite if limite_url is None else limite_url
    meta = {"total_registros": total, "pagina_atual": numero, "limite_por_pagina": limite,
            "proxima_pagina": str(next_page) if next_page else None,
            "proxima_url": BASE + f"/situacoes_vendas?pagina={next_page}&limite={url_limit}" if next_page else None}
    if formato in {"moderno", "ambos"}:
        meta["total_da_pagina"] = len(data)
    if formato in {"legado", "ambos"}:
        meta["total_registros_pagina"] = len(data)
        meta["total_paginas"] = pages
    meta.update(changes)
    return resposta(data, meta=meta)


def alterar(response, callback):
    """Altera JSON de teste sem retornar dados de uma conta real."""
    payload = json.loads(response.body)
    callback(payload)
    return replace(response, body=json.dumps(payload).encode())


def consultar(auth, configuracao, *pages):
    """Inclui o GET não paginado de lojas antes das situações e usa somente o fake."""
    transport = TransporteFicticio(lojas(), *pages)
    return consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transport), transport


@pytest.mark.parametrize("formato", ["moderno", "legado", "ambos"])
def test_formatos_documentados_entregam_catalogo_completo(auth, configuracao, formato):
    """Alias legado e contador moderno representam a mesma quantidade sem inferência."""
    conta, transport = consultar(auth, configuracao, pagina(formato=formato))
    assert conta.lojas == [{"id": "001", "nome": "Loja fictícia"}]
    assert [row["id"] for row in conta.situacoes] == ["0", "1"]
    assert len(transport.calls) == 2
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("limite_url", [20, 100])
def test_limite_efetivo20_reconstroi_segundo_get_com_limite_permitido(auth, configuracao, limite_url):
    """A próxima URL pode refletir pedido100 ou limite20; o destino nunca vem dela."""
    conta, transport = consultar(auth, configuracao,
                                 pagina(total=21, limite=20, limite_url=limite_url),
                                 pagina(numero=2, total=21, limite=20))
    assert len(conta.situacoes) == 21
    assert transport.calls[1][0] == BASE + "/situacoes_vendas?pagina=1&limite=100"
    assert transport.calls[2][0] == BASE + f"/situacoes_vendas?pagina=2&limite={limite_url}"


@pytest.mark.parametrize("formato", ["moderno", "legado", "ambos"])
@pytest.mark.parametrize("limite,total", [(1, 3), (20, 60), (100, 300)])
def test_tres_paginas_completas_reconciliadas_sem_aumentar_budget(auth, configuracao, formato, limite, total):
    """Aceita até três páginas exatas, com quantidade efetiva estável e sem duplicatas."""
    conta, transport = consultar(auth, configuracao,
                                 *(pagina(i, total, limite, formato) for i in range(1, 4)))
    assert len(conta.situacoes) == total
    assert len(transport.calls) == 4
    assert len({row["id"] for row in conta.situacoes}) == total
    assert [parse_qs(urlsplit(call[0]).query).get("pagina") for call in transport.calls[1:]] == [
        ["1"], ["2"], ["3"],
    ]
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("total_paginas", [0, 1])
@pytest.mark.parametrize("formato", ["moderno", "legado", "ambos"])
def test_catalogo_vazio_tem_apenas_pagina1_sem_continuacao(auth, configuracao, formato, total_paginas):
    """Zero registros aceita a convenção0 ou1 páginas, sempre com uma única consulta vazia."""
    conta, transport = consultar(auth, configuracao,
                                 pagina(total=0, formato=formato, total_paginas=total_paginas))
    assert conta.situacoes == []
    assert len(transport.calls) == 2


@pytest.mark.parametrize("formato", ["moderno", "legado", "ambos"])
def test_total_paginas_opcional_deve_corresponder_a_divisao_inteira(auth, configuracao, formato):
    """Quando informado, o total de páginas reforça a conferência em vez de substituir contagens."""
    conta, _ = consultar(auth, configuracao, pagina(formato=formato, total_paginas=1))
    assert len(conta.situacoes) == 2


@pytest.mark.parametrize("formato,campo", [
    ("moderno", "total_registros"), ("moderno", "total_da_pagina"),
    ("legado", "total_registros_pagina"), ("ambos", "total_registros_pagina"),
    ("moderno", "pagina_atual"), ("moderno", "limite_por_pagina"),
    ("legado", "total_paginas"),
])
@pytest.mark.parametrize("valor", [True, False, "2", 2.0, -1, None])
def test_contadores_exigem_inteiros_estritos_sem_coercao(auth, configuracao, formato, campo, valor):
    """Strings numéricas, booleanos, floats, negativos e nulos não comprovam paginação."""
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, pagina(formato=formato, **{campo: valor}))


@pytest.mark.parametrize("limite", [0, 101])
def test_limite_efetivo_fora_de1_a100_e_recusado(auth, configuracao, limite):
    """Não transforma limite zero ou acima do teto oficial em uma configuração implícita."""
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, pagina(limite_por_pagina=limite))


@pytest.mark.parametrize("campo", ["total_da_pagina", "total_registros_pagina"])
def test_dois_aliases_presentes_devem_ser_coerentes(auth, configuracao, campo):
    """Não escolhe silenciosamente o contador que faria o lote ser aceito."""
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, pagina(formato="ambos", **{campo: 1}))


@pytest.mark.parametrize("total_paginas", [0, 2, 9])
def test_total_paginas_divergente_nao_e_ignorado(auth, configuracao, total_paginas):
    """Duas situações com limite100 não podem declarar zero, duas ou nove páginas."""
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, pagina(total_paginas=total_paginas))


@pytest.mark.parametrize("total,limite", [(301, 100), (61, 20), (4, 1)])
def test_mais_de_tres_paginas_aborta_antes_de_pedir_continuacao(auth, configuracao, total, limite):
    """O teto de três páginas vale também quando o limite efetivo é menor que100."""
    transport = TransporteFicticio(lojas(), pagina(total=total, limite=limite))
    with pytest.raises(ContratoInvalido):
        consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transport)
    assert len(transport.calls) == 2


@pytest.mark.parametrize("campo", ["total_registros", "pagina_atual", "limite_por_pagina", "proxima_url"])
def test_ausencia_de_metadado_essencial_nao_presume_fim(auth, configuracao, campo):
    """Nem uma lista pequena substitui total, página, limite ou término explícito."""
    response = alterar(pagina(), lambda p: p["meta"].pop(campo))
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, response)


def test_ausencia_de_ambos_contadores_da_pagina_e_recusada(auth, configuracao):
    """Não infere um contador ausente apenas pelo tamanho da lista recebida."""
    response = alterar(pagina(), lambda p: p["meta"].pop("total_da_pagina"))
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, response)


@pytest.mark.parametrize("valor", [None, [], "meta"])
def test_meta_ausente_ou_nao_objeto_e_recusada(auth, configuracao, valor):
    """Ausência de prova de paginação nunca vira catálogo completo por HTTP200."""
    response = alterar(pagina(), lambda p: p.update(meta=valor))
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, response)


@pytest.mark.parametrize("url", [
    "https://externo.invalid/situacoes_vendas?pagina=2&limite=20",
    BASE + "/vendas?pagina=2&limite=20",
    BASE + "/situacoes_vendas?pagina=2&limite=20&loja_id=outra",
    BASE + "/situacoes_vendas?pagina=2&pagina=2&limite=20",
    BASE + "/situacoes_vendas?pagina=3&limite=20",
    BASE + "/situacoes_vendas?pagina=2&limite=30",
    BASE + "/situacoes_vendas?pagina=2&limite=20#fragmento",
    BASE + "/situacoes_vendas?pagina=2&limite=020",
    BASE + "/situacoes_vendas?pagina=2&limite=0",
    "", None,
])
def test_proxima_url_nao_pode_mudar_destino_escopo_ou_limite(auth, configuracao, url):
    """Somente a mesma consulta e os limites solicitado/efetivo podem comprovar continuação."""
    transport = TransporteFicticio(lojas(), pagina(total=21, limite=20, proxima_url=url))
    with pytest.raises(ContratoInvalido):
        consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transport)
    assert len(transport.calls) == 2


@pytest.mark.parametrize("cursor", [None, 2, True, "1", "3", "02", "2&limite=100"])
def test_cursor_deve_avancar_exatamente_uma_pagina(auth, configuracao, cursor):
    """A URL válida não autoriza ignorar um cursor presente contraditório."""
    transport = TransporteFicticio(lojas(), pagina(total=21, limite=20, proxima_pagina=cursor))
    with pytest.raises(ContratoInvalido):
        consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transport)
    assert len(transport.calls) == 2


@pytest.mark.parametrize("problema", ["limite", "total", "duplicada", "truncada"])
def test_segunda_pagina_divergente_nunca_entrega_catalogo_parcial(auth, configuracao, problema):
    """Rejeita alteração do tamanho, total, identidade repetida e truncamento após dados válidos."""
    second = pagina(numero=2, total=21, limite=20)
    if problema == "limite":
        second = pagina(numero=2, total=21, limite=10)
    elif problema == "total":
        second = pagina(numero=2, total=22, limite=20)
    elif problema == "duplicada":
        second = alterar(second, lambda p: p["data"][0].update(id="0"))
    else:
        def truncar(payload):
            """Faz contagem local coerente, preservando a divergência com o total global."""
            payload["data"] = []
            payload["meta"]["total_da_pagina"] = 0
        second = alterar(second, truncar)
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, pagina(total=21, limite=20), second)


def test_pagina_intermediaria_truncada_nao_e_deduzida_como_ultima(auth, configuracao):
    """Uma página curta não comprova fim quando o total ainda exige mais registros."""
    def truncar(payload):
        """Remove um item e ajusta apenas o contador local da resposta fictícia."""
        payload["data"].pop()
        payload["meta"]["total_da_pagina"] -= 1
    transport = TransporteFicticio(lojas(), alterar(pagina(total=21, limite=20), truncar))
    with pytest.raises(ContratoInvalido):
        consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transport)
    assert len(transport.calls) == 2


@pytest.mark.parametrize("campo,valor", [("proxima_url", BASE + "/situacoes_vendas?pagina=2&limite=100"),
                                        ("proxima_pagina", "2"), ("pagina_atual", 2)])
def test_zero_registros_recusa_pagina2_ou_qualquer_continuacao(auth, configuracao, campo, valor):
    """Total zero só permite a primeira página vazia e fim explícito."""
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, pagina(total=0, **{campo: valor}))
