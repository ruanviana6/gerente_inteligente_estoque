"""Testes da consulta integral de vendas e conciliação sem importação."""
import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from test_gestaoclick_account_setup import (
    ACCESS_TOKEN,
    BASE,
    PRIVADO,
    SECRET_TOKEN,
    TransporteFicticio,
    consultar,
)
from test_gestaoclick_account_setup import auth as auth
from test_gestaoclick_account_setup import configuracao as configuracao

from analytics.sales_audit import conciliar_vendas_com_catalogo
from integracoes.gestaoclick import ContratoInvalido, RespostaGestaoClick
from integracoes.native_preview import DocumentoPreviaVenda, ItemPreviaVenda
from integracoes.sales_audit import (
    AuditoriaVendas,
    VendaIntegralInvalida,
    consultar_vendas_integrais,
)
from web.errors import AuthorizationError

INICIO = date(2025, 1, 10)
FIM = date(2025, 1, 16)


@pytest.fixture(autouse=True)
def sem_rede_e_espera(monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener", Mock(side_effect=AssertionError("Rede proibida")))
    monkeypatch.setattr("integracoes.sales_audit.time.sleep", Mock())


@pytest.fixture
def conta(auth, configuracao):
    return consultar(auth, configuracao)[0]


def pagina(n, total, *, limite=100, meta_changes=None, rows=None):
    start = (n-1)*limite
    if rows is None:
        rows = [{
            "id": f"venda-{i}",
            "codigo": f"PED-{i}",
            "data": "2025-01-12",
            "situacao_id": str(i % 2),
            "cliente": PRIVADO,
            "valor_total": SECRET_TOKEN,
            "produtos": [{"produto": {
                "produto_id": 101, "variacao_id": 900,
                "quantidade": "2.000", "sigla_unidade": "UN",
                "nome_produto": PRIVADO,
            }}],
        } for i in range(start,min(start+limite,total))]
    proxima = n+1 if start+len(rows)<total else None
    meta = {
        "total_registros": total, "total_da_pagina": len(rows),
        "pagina_atual": n, "limite_por_pagina": limite,
        "total_paginas": (total+limite-1)//limite,
        "proxima_pagina": str(proxima) if proxima else None,
        "proxima_url": BASE + f"/vendas?pagina={proxima}&limite={limite}" if proxima else None,
    }
    meta.update(meta_changes or {})
    return RespostaGestaoClick(200,json.dumps({
        "code": 200,"status":"success","data":rows,"meta":meta,
    }).encode())


def consultar_local(auth,configuracao,conta,*responses,autorizado=True,depois=None):
    transporte=TransporteFicticio(*responses,depois=depois)
    result=consultar_vendas_integrais(
        auth,lambda:configuracao,conta,"001",INICIO,FIM,
        autorizado=autorizado,transporte=transporte,
    )
    return result,transporte


@pytest.mark.parametrize("total", [0,1,100,101,205,1000])
def test_paginas_inteiras_e_escopo_fixo(auth,configuracao,conta,total):
    qtd=max(1,(total+99)//100)
    responses=[pagina(i,total) for i in range(1,qtd+1)]
    resultado,transporte=consultar_local(auth,configuracao,conta,*responses)
    assert len(resultado.documentos)==total
    assert resultado.total_informado==total and resultado.paginas==qtd
    assert resultado.itens==total
    assert not resultado.imported and not resultado.source_coverage_verified
    assert not resultado.stable_line_id_verified and not resultado.source_updated_at_verified
    assert len(transporte.calls)==qtd
    for i,(url,headers,timeout) in enumerate(transporte.calls,1):
        assert urlsplit(url).path=="/vendas"
        assert parse_qs(urlsplit(url).query)=={
            "data_inicio":["2025-01-10"],"data_fim":["2025-01-16"],
            "loja_id":["001"],"tipo":["produto"],"pagina":[str(i)],"limite":["100"],
        }
        assert headers["access-token"]==ACCESS_TOKEN
        assert headers["secret-access-token"]==SECRET_TOKEN
        assert timeout<=15
    resultado_texto=str(asdict(resultado))
    for secreto in (PRIVADO,ACCESS_TOKEN,SECRET_TOKEN):
        assert secreto not in resultado_texto
    auth.rpc.assert_not_called()


def test_cursor_retorna_url_externa_mas_nunca_e_seguida(
    auth,configuracao,conta,
):
    primeira=pagina(1,101,meta_changes={
        "proxima_url":"https://outra-origem.invalid/exfiltrar?token=PRIVADO"
    })
    resultado,transporte=consultar_local(
        auth,configuracao,conta,primeira,pagina(2,101),
    )
    assert resultado.total_informado==101
    assert all("outra-origem.invalid" not in url for url,_,_ in transporte.calls)
    assert len(transporte.calls)==2
    auth.rpc.assert_not_called()


def test_duplicidade_de_documento_entre_paginas_bloqueia(
    auth,configuracao,conta,
):
    dados=json.loads(pagina(2,101).body)["data"]
    dados[0]["id"]="venda-0"
    with pytest.raises(VendaIntegralInvalida,match="DUPLICIDADE"):
        consultar_local(auth,configuracao,conta,pagina(1,101),pagina(2,101,rows=dados))
    auth.rpc.assert_not_called()


def test_total_mudou_na_segunda_pagina_bloqueia(auth,configuracao,conta):
    with pytest.raises(VendaIntegralInvalida,match="TOTAL_ALTERADO"):
        consultar_local(auth,configuracao,conta,pagina(1,101),pagina(2,102))
    auth.rpc.assert_not_called()


def test_limite_de_documentos_e_paginas_bloqueia(auth,configuracao,conta):
    with pytest.raises(VendaIntegralInvalida,match="EXCESSO"):
        consultar_local(auth,configuracao,conta,pagina(1,1001))
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("autorizado",[False,None,0,1,"true"])
def test_sem_autorizacao_nunca_ler_secrets(auth,conta,autorizado):
    loader=Mock()
    transporte=TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_vendas_integrais(
            auth,loader,conta,"001",INICIO,FIM,
            autorizado=autorizado,transporte=transporte,
        )
    loader.assert_not_called()
    assert not transporte.calls


def test_revogacao_na_pagina_intermediaria_descarta_tudo(auth,configuracao,conta):
    def revogar(index):
        if index==2:
            auth.generation+=1
    with pytest.raises(AuthorizationError):
        consultar_local(
            auth,configuracao,conta,pagina(1,101),pagina(2,101),depois=revogar,
        )
    auth.rpc.assert_not_called()


def amostra(statuses=("1","0","2")):
    docs=[]
    for i,status in enumerate(statuses):
        docs.append(DocumentoPreviaVenda(
            f"venda-{i}",f"PED-{i}",INICIO,status,(
                ItemPreviaVenda("101",None,Decimal("2"),"UN"),
                ItemPreviaVenda("101",None,Decimal("1"),"UN"),
                ItemPreviaVenda("102",None,Decimal("3"),"CX"),
                ItemPreviaVenda("999",None,Decimal("2"),"UN"),
                ItemPreviaVenda("101",None,Decimal("3"),None),
            )
        ))
    return AuditoriaVendas("001",INICIO,FIM,tuple(docs),len(docs),1,len(docs)*5)


def test_demanda_fechada_sem_duplicar_pedido_sem_converter_caixa():
    foto={"store_id":"001","product_count":2,"products":[
        {"id":"101","estoque":10},{"id":"102","estoque":"2"},
    ]}
    result=conciliar_vendas_com_catalogo(
        amostra(),foto,separacao_id="1",concretizada_id="0",
    )
    assert result.documentos_fechados==2
    assert result.documentos_separacao==result.documentos_concretizados==1
    assert result.documentos_nao_classificados==1
    assert result.produtos_vistos==3 and result.produtos_correspondentes==2
    assert result.produtos_sem_correspondencia==("999",)
    assert result.unidades_fechadas_un==Decimal("6")
    assert result.agrupamento==(("101",Decimal("6")),)
    assert result.linhas_unidade_nao_confirmada==2
    assert result.linhas_unidade_nao_suportada==2
    assert result.imported is False
    assert result.ready_for_replenishment is False
    assert result.source_coverage_verified is False


@pytest.mark.parametrize("foto",[
    {"store_id":"outro","product_count":1,"products":[{"id":"101","estoque":2}]},
    {"store_id":"001","product_count":2,"products":[{"id":"101","estoque":2}]},
    {"store_id":"001","product_count":2,"products":[{"id":"101","estoque":2},{"id":"101","estoque":2}]},
    {"store_id":"001","product_count":1,"products":[{"id":"101","estoque":-1}]},
    {"store_id":"001","product_count":1,"products":[{"id":"101","estoque":True}]},
])
def test_fotografia_invalida_nao_autoriza_conciliacao(foto):
    with pytest.raises(ContratoInvalido):
        conciliar_vendas_com_catalogo(
            amostra(),foto,separacao_id="1",concretizada_id="0",
        )


def test_status_sem_mapeamento_explicito_rejeitado():
    foto={"store_id":"001","product_count":1,"products":[{"id":"101","estoque":10}]}
    with pytest.raises(ContratoInvalido):
        conciliar_vendas_com_catalogo(
            amostra(),foto,separacao_id="1",concretizada_id="1",
        )
