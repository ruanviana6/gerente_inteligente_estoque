"""Regressões do catálogo inteiro: somente GET local, paginação e identidade Master."""
import json
from dataclasses import asdict
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from integracoes.catalog_full import (
    PaginacaoCatalogoInvalida, consultar_catalogo_completo,
)
from integracoes.gestaoclick import ContratoInvalido, RespostaGestaoClick
from test_gestaoclick_account_setup import (
    ACCESS_TOKEN, BASE, PRIVADO, SECRET_TOKEN, TransporteFicticio, consultar,
)
from test_gestaoclick_account_setup import auth as auth
from test_gestaoclick_account_setup import configuracao as configuracao
from web.errors import AuthorizationError


@pytest.fixture(autouse=True)
def sem_espera_sem_rede(monkeypatch):
    monkeypatch.setattr("integracoes.catalog_full.time.sleep", Mock())
    monkeypatch.setattr("urllib.request.build_opener", Mock(side_effect=AssertionError("Rede proibida")))


@pytest.fixture
def conta(auth, configuracao):
    return consultar(auth, configuracao)[0]


def pagina_catalogo(pagina=1, total=245, *, limite=100, offset=0, metas=None, dados=None):
    inicio = (pagina - 1) * limite
    if dados is None:
        dados = [{
            "id": i + 10000 + offset,
            "codigo_interno": f"FL-{i}",
            "estoque": i % 20,
            "variacoes": [{"estoque": "4", "codigo": None, "variacao_api_id": ""}],
            "cliente": PRIVADO, "valor_venda": SECRET_TOKEN,
        } for i in range(inicio, min(inicio + limite, total))]
    proxima = pagina + 1 if inicio + len(dados) < total else None
    meta = {
        "total_registros": total, "total_registros_pagina": len(dados),
        "limite_por_pagina": limite, "pagina_atual": pagina,
        "total_paginas": (total + limite - 1) // limite,
        "proxima_pagina": str(proxima) if proxima else None,
        "proxima_url": (BASE + f"/produtos?loja_id=001&pagina={proxima}&limite={limite}"
                        if proxima else None),
    }
    meta.update(metas or {})
    return RespostaGestaoClick(200, json.dumps({
        "code": 200, "status": "success", "meta": meta, "data": dados,
    }).encode())


def ler(auth, configuracao, conta, *paginas, autorizado=True, depois=None):
    transporte = TransporteFicticio(*paginas, depois=depois)
    result = consultar_catalogo_completo(
        auth, lambda: configuracao, conta, "001",
        autorizado=autorizado, transporte=transporte,
    )
    return result, transporte


@pytest.mark.parametrize("total", [0, 1, 100, 101, 245, 300, 1000])
def test_paginacao_integral_valida_ate_limite(auth, configuracao, conta, total):
    qtd = max(1, (total + 99) // 100)
    paginas = [pagina_catalogo(i, total) for i in range(1, qtd + 1)]
    catalogo, transporte = ler(auth, configuracao, conta, *paginas)
    assert len(catalogo.produtos) == total
    assert catalogo.total_informado == total
    assert catalogo.paginas == qtd
    assert catalogo.sem_estoque == 0
    assert catalogo.variacoes_sem_id == total
    assert catalogo.variacoes_sem_codigo == total
    assert len(catalogo.linhas_snapshot()) == total
    assert all(v.id is None and v.estoque is None
               for p in catalogo.produtos for v in p.variacoes)
    assert len(transporte.calls) == qtd
    for index, (url, cabecalhos, timeout) in enumerate(transporte.calls, 1):
        assert urlsplit(url).path == "/produtos"
        assert parse_qs(urlsplit(url).query) == {
            "loja_id": ["001"], "pagina": [str(index)], "limite": ["100"],
        }
        assert cabecalhos["access-token"] == ACCESS_TOKEN
        assert cabecalhos["secret-access-token"] == SECRET_TOKEN
        assert timeout <= 15
    assert PRIVADO not in str(asdict(catalogo))
    assert SECRET_TOKEN not in str(asdict(catalogo))
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("metas", [
    {"pagina_atual": 9},
    {"proxima_url": "https://exemplo.invalid/produtos?loja_id=001&pagina=2&limite=100"},
    {"proxima_url": BASE + "/produtos?loja_id=002&pagina=2&limite=100"},
    {"proxima_url": BASE + "/produtos?loja_id=001&pagina=2&limite=100&outro=1"},
    {"proxima_url": None},
    {"proxima_pagina": "3"},
    {"total_registros_pagina": 99},
    {"limite_por_pagina": 0},
    {"total_paginas": 1},
])
def test_metadados_inconsistentes_bloqueiam_todo_lote(auth, configuracao, conta, metas):
    transporte = TransporteFicticio(pagina_catalogo(1, 245, metas=metas))
    with pytest.raises((PaginacaoCatalogoInvalida, ContratoInvalido)):
        consultar_catalogo_completo(
            auth, lambda: configuracao, conta, "001",
            autorizado=True, transporte=transporte,
        )
    assert len(transporte.calls) == 1
    auth.rpc.assert_not_called()


def test_total_mudou_durante_paginacao_bloqueia(auth, configuracao, conta):
    with pytest.raises(PaginacaoCatalogoInvalida, match="ALTERACAO"):
        ler(auth, configuracao, conta, pagina_catalogo(1, 245),
            pagina_catalogo(2, 245, metas={"total_registros": 246}))
    auth.rpc.assert_not_called()


def test_id_duplicado_entre_paginas_recusado(auth, configuracao, conta):
    ultima = pagina_catalogo(2, 101, dados=[{
        "id": 10000, "codigo_interno": "DUPLICADO", "estoque": 3, "variacoes": [],
    }])
    with pytest.raises(PaginacaoCatalogoInvalida, match="DUPLICIDADE"):
        ler(auth, configuracao, conta, pagina_catalogo(1, 101), ultima)
    auth.rpc.assert_not_called()


def test_total_superior_ao_limite_e_conteudo_parcial_nao_entram(auth, configuracao, conta):
    with pytest.raises(PaginacaoCatalogoInvalida, match="EXCESSO"):
        ler(auth, configuracao, conta, pagina_catalogo(1, 1001))
    auth.rpc.assert_not_called()


def test_saldo_desconhecido_fica_desconhecido_e_nao_pode_ser_salvo(auth, configuracao, conta):
    dados = [{"id": 100, "codigo_interno": "SKU", "estoque": None, "variacoes": []}]
    catalogo, _ = ler(auth, configuracao, conta,
                      pagina_catalogo(1, 1, dados=dados))
    assert catalogo.sem_estoque == 1
    assert catalogo.soma_estoque is None
    with pytest.raises(ContratoInvalido):
        catalogo.linhas_snapshot()


def test_sem_consentimento_nao_carrega_secrets(auth, conta):
    carregar = Mock()
    transporte = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_catalogo_completo(
            auth, carregar, conta, "001", autorizado=False, transporte=transporte,
        )
    carregar.assert_not_called()
    assert not transporte.calls


def test_revogacao_na_segunda_pagina_descarta_resultado(auth, configuracao, conta):
    def revogar(index):
        if index == 2:
            auth.generation += 1
    with pytest.raises(AuthorizationError):
        ler(auth, configuracao, conta, pagina_catalogo(1, 101),
            pagina_catalogo(2, 101), depois=revogar)
    auth.rpc.assert_not_called()
