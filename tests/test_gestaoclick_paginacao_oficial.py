"""Regressões fictícias do envelope de vendas publicado em api.gestaoclick.com/docs.

Não replica dados de empresa, não consulta ERP e não supõe campos analíticos que
não existem no schema público. Os registros precisam somente de IDs de teste.
"""

import json
from dataclasses import replace

import pytest
from test_gestaoclick import BASE, CONFIG, cliente, resposta

from integracoes.gestaoclick import ContratoInvalido, RespostaGestaoClick


def oficial(*, total=1, page=1, limit=100, count=None, next_page=None, next_url=None, **changes):
    """Monta a meta oficial sem total_paginas, preservando contagens inteiras e cursor textual."""
    if count is None:
        count = min(limit, max(0, total - (page - 1) * limit))
    data = [{"id": f"DEMO-{(page - 1) * limit + i:05d}"} for i in range(count)]
    meta = {"total_registros": total, "total_da_pagina": len(data), "pagina_atual": page,
            "limite_por_pagina": limit, "pagina_anterior": None if page == 1 else str(page - 1),
            "url_anterior": None, "proxima_pagina": next_page, "proxima_url": next_url}
    meta.update(changes)
    return RespostaGestaoClick(200, json.dumps({"code": 200, "status": "success", "meta": meta,
                                              "data": data}).encode())


def next_url(page):
    """Mantém a mesma consulta e avança somente o número da página."""
    return BASE + f"/vendas?loja_id=loja-A&pagina={page}&limite=100"


def editar(response, mutate):
    """Altera uma resposta fictícia sem permitir acesso a resposta ou credencial real."""
    payload = json.loads(response.body)
    mutate(payload)
    return replace(response, body=json.dumps(payload).encode())


@pytest.mark.parametrize("total", [0, 1, 100])
def test_meta_oficial_sem_total_paginas_aceita_fechamento_integro(total):
    """Página única completa ou zero resultados explícitos são aceitos com meta válida."""
    client, transport, _ = cliente(oficial(total=total))
    result = client.consultar("vendas")
    assert result.total == total
    assert len(result.registros) == total
    assert result.paginas == 1
    assert len(transport.calls) == 1


def test_meta_oficial_calcula_paginas_com_inteiros_e_reconcilia_total():
    """Sem total_paginas, 201 registros em páginas de100 exigem exatamente três GETs."""
    client, transport, _ = cliente(
        oficial(total=201, next_page="2", next_url=next_url(2)),
        oficial(total=201, page=2, next_page="3", next_url=next_url(3)),
        oficial(total=201, page=3),
    )
    result = client.consultar("vendas")
    assert (result.total, result.paginas, len(result.registros)) == (201, 3, 201)
    assert len({row["id"] for row in result.registros}) == 201
    assert len(transport.calls) == 3


def test_limite_efetivo_da_resposta_e_usado_para_derivar_paginas():
    """O tamanho declarado da página deve permanecer estável, mesmo abaixo do teto100."""
    client, _, _ = cliente(oficial(total=21, limit=20, next_page="2", next_url=next_url(2)),
                            oficial(total=21, limit=20, page=2))
    result = client.consultar("vendas")
    assert (result.total, result.paginas) == (21, 2)


@pytest.mark.parametrize("field,value", [
    ("total_registros", True), ("total_registros", -1), ("total_registros", "1"),
    ("total_da_pagina", True), ("total_da_pagina", -1), ("total_da_pagina", "1"),
    ("limite_por_pagina", True), ("limite_por_pagina", 0), ("limite_por_pagina", -1),
    ("limite_por_pagina", 101), ("limite_por_pagina", 1.0), ("limite_por_pagina", "100"),
    ("pagina_atual", True), ("pagina_atual", 0), ("pagina_atual", 2),
])
def test_meta_oficial_recusa_tipos_e_limites_invalidos(field, value):
    """Booleanos, coerções e valores fora do contrato não viram uma cobertura válida."""
    client, _, _ = cliente(oficial(**{field: value}))
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")


@pytest.mark.parametrize("field", ["meta", "total_registros", "total_da_pagina", "limite_por_pagina",
                                    "pagina_atual", "proxima_url"])
def test_meta_incompleta_nao_e_inferida_por_http200(field):
    """Sem metadados suficientes o cliente não presume tamanho ou fim da listagem."""
    def remove(payload):
        """Remove a evidência obrigatória do envelope fictício."""
        if field == "meta":
            payload.pop(field)
        else:
            payload["meta"].pop(field)
    client, _, _ = cliente(editar(oficial(), remove))
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")


@pytest.mark.parametrize("mutation", [
    lambda p: p["meta"].update(total_da_pagina=2),
    lambda p: p["data"].clear(),
    lambda p: p["meta"].update(proxima_pagina="2"),
    lambda p: p["meta"].update(proxima_pagina=0),
    lambda p: p["meta"].update(proxima_url=next_url(2)),
])
def test_ultima_pagina_recusa_contagem_cursor_ou_url_inconsistentes(mutation):
    """Não retorna uma lista que declare página seguinte ou contagem divergente."""
    client, _, _ = cliente(editar(oficial(), mutation))
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")


@pytest.mark.parametrize("cursor", [None, "1", "3", "02", 2, True, "", "2&loja_id=outra"])
def test_cursor_oficial_presente_deve_avancar_exatamente_uma_pagina(cursor):
    """URL correta não compensa um cursor de próxima página contraditório."""
    client, transport, _ = cliente(oficial(total=101, next_page=cursor, next_url=next_url(2)))
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")
    assert len(transport.calls) == 1


@pytest.mark.parametrize("url", [
    BASE + "/vendas?loja_id=outra&pagina=2&limite=100",
    BASE + "/vendas?loja_id=loja-A&pagina=3&limite=100",
    BASE + "/vendas?loja_id=loja-A&pagina=2&limite=20",
    BASE + "/vendas?loja_id=loja-A&pagina=2&pagina=2&limite=100",
    "https://destino-invalido.invalid/vendas?pagina=2&limite=100",
])
def test_formato_oficial_preserva_validacao_da_url_e_do_escopo(url):
    """O suporte ao novo envelope não relaxa URL, filtro, loja ou limite solicitado."""
    client, transport, _ = cliente(oficial(total=101, next_page="2", next_url=url))
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")
    assert len(transport.calls) == 1


def test_interrupcao_antes_da_ultima_pagina_nao_retorna_parcial():
    """Metadados ainda indicando registros faltantes impedem encerrar na página atual."""
    client, _, _ = cliente(oficial(total=101, next_page=None, next_url=None))
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")


def test_pagina_intermediaria_curta_nao_pode_ser_compensada_por_pagina_seguinte():
    """Rejeita truncamento mesmo se as contagens globais pudessem fechar depois."""
    client, transport, _ = cliente(oficial(total=150, count=99, next_page="2", next_url=next_url(2)))
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")
    assert len(transport.calls) == 1


@pytest.mark.parametrize("second", [
    oficial(total=102, page=2),
    oficial(total=101, page=2, limit=50, count=1),
    oficial(total=101, page=2, count=0),
    resposta([{"id": "ficticio"}], total=101, page=2, pages=2),
])
def test_origem_nao_pode_mudar_total_limite_ou_formato_durante_a_leitura(second):
    """Uma alteração no meio da consulta invalida todos os registros já coletados."""
    client, _, _ = cliente(oficial(total=101, next_page="2", next_url=next_url(2)), second)
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")


def test_paginas_derivadas_respeitam_orcamento_antes_da_segunda_chamada():
    """O cálculo não permite exceder o teto de páginas configurado."""
    client, transport, _ = cliente(oficial(total=101, next_page="2", next_url=next_url(2)),
                                   config=replace(CONFIG, max_pages=1))
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")
    assert len(transport.calls) == 1
