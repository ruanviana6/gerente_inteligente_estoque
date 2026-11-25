"""Testes de leitura fiscal isolada, com ERP inteiramente fictício."""
import json
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from test_gestaoclick_account_setup import (
    ACCESS_TOKEN,
    PRIVADO,
    SECRET_TOKEN,
    TransporteFicticio,
    consultar,
    situacoes,
)
from test_gestaoclick_account_setup import auth as auth
from test_gestaoclick_account_setup import configuracao as configuracao

from integracoes.fiscal_read_probe import (
    conferir_nfe_por_id,
    consultar_itens_para_montagem,
    consultar_recurso_nfe,
)
from integracoes.gestaoclick import ContratoInvalido, GestaoClickError, RespostaGestaoClick
from web.auth import AccessContext
from web.errors import AuthorizationError


@pytest.fixture(autouse=True)
def bloquear_rede(monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener", Mock(side_effect=AssertionError("Rede proibida")))


@pytest.fixture
def conta(auth, configuracao):
    return consultar(auth, configuracao)[0]


def resposta(rows, status=200, **extras):
    payload = {"code": 200, "status": "success", "data": rows, **extras}
    return RespostaGestaoClick(status, json.dumps(payload).encode())


def rodar(auth, config, conta, retorno, *, autorizado=True, depois=None):
    transporte = TransporteFicticio(retorno, depois=depois)
    resultado = consultar_recurso_nfe(
        auth, lambda: config, conta, "001", autorizado=autorizado,
        transporte=transporte,
    )
    return resultado, transporte


@pytest.mark.parametrize("rows", [[], [{
    "id": "123", "cliente": PRIVADO, "chave_nfe": SECRET_TOKEN, "valor": "1.000",
}]])
def test_get_unico_sem_pii_sem_alterar_erp(auth, configuracao, conta, rows):
    resultado, transporte = rodar(auth, configuracao, conta, resposta(rows))
    assert resultado["escrita_habilitada"] is False
    assert resultado["cobertura_comprovada"] is False
    assert resultado["loja_efetiva_comprovada"] is False
    assert len(transporte.calls) == 1
    url, headers, timeout = transporte.calls[0]
    assert urlsplit(url).path == "/notas_fiscais_produtos"
    assert parse_qs(urlsplit(url).query) == {
        "loja_id": ["001"], "pagina": ["1"], "limite": ["1"],
    }
    assert headers["access-token"] == ACCESS_TOKEN
    assert headers["secret-access-token"] == SECRET_TOKEN
    assert timeout <= 15
    for privado in (PRIVADO, SECRET_TOKEN, ACCESS_TOKEN, "1.000", "123"):
        assert privado not in str(resultado)
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("consentimento", [None, False, "true", 1])
def test_sem_autorizacao_nao_le_credenciais(auth, conta, consentimento):
    carregar = Mock()
    transporte = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_recurso_nfe(auth, carregar, conta, "001",
                              autorizado=consentimento, transporte=transporte)
    carregar.assert_not_called()
    assert not transporte.calls


def test_loja_fora_da_conta_rejeitada(auth, configuracao, conta):
    transporte = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_recurso_nfe(auth, lambda: configuracao, conta, "999",
                              autorizado=True, transporte=transporte)
    assert not transporte.calls


@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 429, 500])
def test_http_bloqueado_sem_vazamento(auth, configuracao, conta, status):
    retorno = RespostaGestaoClick(status, (PRIVADO + SECRET_TOKEN).encode(),
                                 {"Location": "https://externo.invalid/" + ACCESS_TOKEN})
    transporte = TransporteFicticio(retorno)
    with pytest.raises(GestaoClickError) as exc:
        consultar_recurso_nfe(auth, lambda: configuracao, conta, "001",
                              autorizado=True, transporte=transporte)
    assert SECRET_TOKEN not in str(exc.value) and PRIVADO not in str(exc.value)
    assert len(transporte.calls) == 1


@pytest.mark.parametrize("retorno", [
    resposta({"id": 1}),
    resposta([{}] * 2),
    resposta([], code="200"),
    RespostaGestaoClick(200, b'{"code":200,"code":200,"status":"success","data":[]}'),
])
def test_contrato_divergente_nao_expoe_payload(auth, configuracao, conta, retorno):
    with pytest.raises(ContratoInvalido):
        rodar(auth, configuracao, conta, retorno)


def test_revogacao_e_troca_de_secrets_abortam(auth, configuracao, conta):
    geracao_anterior = auth.generation
    transporte = TransporteFicticio(resposta([]), depois=lambda n: setattr(auth, "generation", auth.generation+1))
    with pytest.raises(AuthorizationError):
        consultar_recurso_nfe(auth, lambda: configuracao, conta, "001",
                              autorizado=True, transporte=transporte)
    assert len(transporte.calls) == 1
    # Restaura a sessão original antes do segundo cenário independente.
    auth.generation = geracao_anterior
    transporte = TransporteFicticio(resposta([]), depois=lambda n: configuracao.update(GESTAOCLICK_ACCESS_TOKEN="NOVO"))
    with pytest.raises(AuthorizationError):
        consultar_recurso_nfe(auth, lambda: configuracao, conta, "001",
                              autorizado=True, transporte=transporte)
    assert len(transporte.calls) == 1



@pytest.mark.parametrize(("loja_registro", "esperado"), [
    ("001", True),
    (1, False),  # não confundir loja textual com o inteiro 1
    ("002", False),
    (2, False),
    (None, None),
    (True, None),
    ("invalido", None),
])
def test_confere_escopo_apenas_quando_nota_explica_loja(
    auth, configuracao, conta, loja_registro, esperado,
):
    linhas = [{"id": "123", "loja_id": loja_registro, "cliente": PRIVADO}]
    resultado, transporte = rodar(auth, configuracao, conta, resposta(linhas))
    assert resultado["loja_no_registro_confere"] is esperado
    assert resultado["loja_efetiva_comprovada"] is False
    assert resultado["escrita_habilitada"] is False
    assert len(transporte.calls) == 1
    assert PRIVADO not in str(resultado)
    assert "002" not in str(resultado)


def test_lista_vazia_nao_prova_loja_efetiva(auth, configuracao, conta):
    resultado, transporte = rodar(auth, configuracao, conta, resposta([]))
    assert resultado["loja_no_registro_confere"] is None
    assert resultado["loja_efetiva_comprovada"] is False
    assert len(transporte.calls) == 1

@pytest.mark.parametrize(("situacao", "aberta"), [("Em aberto", True), ("Aprovada", False)])
def test_detalhe_fiscal_prova_apenas_campos_conferidos(auth, configuracao, conta, situacao, aberta):
    """Confere o detalhe documentado sem devolver cliente ou conteúdo integral."""
    referencia = "Flatter origem 31 venda 901"
    transporte = TransporteFicticio(resposta({
        "id": "223", "loja_id": "001", "situacao_nf": situacao,
        "informacoes_complementares": referencia, "cliente": PRIVADO, "chave": SECRET_TOKEN,
    }))
    resultado = conferir_nfe_por_id(auth, lambda: configuracao, conta, "001", "223", referencia,
                                     autorizado=True, transporte=transporte)
    assert resultado == {"situacao": situacao, "em_aberto": aberta, "referencia_confere": True}
    assert len(transporte.calls) == 1
    assert urlsplit(transporte.calls[0][0]).path == "/notas_fiscais_produtos/223"
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("mudancas", [{"id": "224"}, {"loja_id": "002"},
                                       {"situacao_nf": PRIVADO}, {"situacao_nf": []}])
def test_detalhe_divergente_nao_confirma_nota(auth, configuracao, conta, mudancas):
    nota = {"id": "223", "loja_id": "001", "situacao_nf": "Em aberto"} | mudancas
    with pytest.raises(ContratoInvalido) as erro:
        conferir_nfe_por_id(auth, lambda: configuracao, conta, "001", "223",
                             autorizado=True, transporte=TransporteFicticio(resposta(nota)))
    assert PRIVADO not in str(erro.value)


@pytest.mark.parametrize("nota_id", ["223/emitir", "https://fora.invalid", "0", "01", "²", 223])
def test_nao_aceita_id_como_rota(auth, conta, nota_id):
    carregar = Mock()
    transporte = TransporteFicticio()
    with pytest.raises(ContratoInvalido):
        conferir_nfe_por_id(auth, carregar, conta, "001", nota_id,
                             autorizado=True, transporte=transporte)
    carregar.assert_not_called()
    assert not transporte.calls


def test_referencia_divergente_e_sinalizada_sem_expor_dado(auth, configuracao, conta):
    nota = {"id": "223", "loja_id": "001", "situacao_nf": "Em aberto",
            "informacoes_complementares": PRIVADO}
    resultado = conferir_nfe_por_id(auth, lambda: configuracao, conta, "001", "223",
                                     "Flatter origem 31 venda 901", autorizado=True,
                                     transporte=TransporteFicticio(resposta(nota)))
    assert resultado["referencia_confere"] is False
    assert PRIVADO not in str(resultado)


def test_formulario_exige_consentimento_e_mostra_estado_sem_expor_resposta(monkeypatch):
    """A conferência de nota existente só usa a leitura após clique consentido."""
    from streamlit.testing.v1 import AppTest

    from web import fiscal_real_read_page

    consulta = Mock(return_value={"situacao": "Em aberto", "em_aberto": True, "referencia_confere": True})
    monkeypatch.setattr(fiscal_real_read_page, "conferir_nfe_por_id", consulta)
    app = AppTest.from_string("""
from types import SimpleNamespace
from web.fiscal_real_read_page import _conferir_nota_existente
_conferir_nota_existente(SimpleNamespace(auth=None), None, "42")
""").run()
    consulta.assert_not_called()
    app.button[0].click().run()
    consulta.assert_not_called()
    app.text_input[0].set_value("223")
    app.text_input[1].set_value("Flatter origem 31 venda 901")
    app.checkbox[0].check()
    app.button[0].click().run()
    assert not app.exception
    assert consulta.call_count == 1
    assert consulta.call_args.args[3:] == ("42", "223", "Flatter origem 31 venda 901")
    assert "Em aberto" in app.markdown[-1].value
    assert len(app.success) == 1


@pytest.fixture
def conta_atacado(auth, configuracao):
    """Usa somente lojas e permissões fictícias."""
    auth.context = AccessContext(auth.context.profile, auth.context.permissions | {"fiscal.view", "finance.view"})
    lojas = resposta([{"id": "31", "nome": "FLT Comércio"}, {"id": "42", "nome": "Flagship - Flatter"}])
    return consultar(auth, configuracao, lojas, situacoes())[0]


def venda_ficticia():
    """Cria um detalhe documentado com custos e dados alheios à projeção."""
    return {"id": "515", "codigo": "905", "nome_loja": "FLT Comércio", "situacao_id": "1",
            "nome_cliente": PRIVADO, "produtos": [{"produto": {
                "produto_id": 88, "variacao_id": 0, "nome_produto": "Produto fictício",
                "sigla_unidade": "CX", "quantidade": "2.50", "valor_venda": "12.00",
                "desconto_valor": None, "desconto_porcentagem": "5.00", "valor_total": "28.50",
                "valor_custo": SECRET_TOKEN,
            }}]}


def test_montagem_mostra_valores_sem_converter_ou_expor_custo(auth, configuracao, conta_atacado):
    """Mantém decimais e campos vazios tais como retornados pela origem."""
    transporte = TransporteFicticio(resposta(venda_ficticia()))
    linhas = consultar_itens_para_montagem(auth, lambda: configuracao, conta_atacado,
                                           "31", "42", "515", "905", "1",
                                           autorizado=True, transporte=transporte)
    assert linhas[0]["Quantidade na venda"] == "2.50"
    assert linhas[0]["Valor de venda informado"] == "12.00"
    assert linhas[0]["Desconto em valor informado"] is None
    assert linhas[0]["Total da linha informado"] == "28.50"
    assert linhas[0]["Unidade"] == "CX"
    assert all(privado not in str(linhas) for privado in [PRIVADO, SECRET_TOKEN])
    assert len(transporte.calls) == 1
    assert urlsplit(transporte.calls[0][0]).path == "/vendas/515"
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("alteracoes", [{"nome_loja": "Flagship - Flatter"}, {"codigo": "906"},
                                        {"id": "516"}, {"situacao_id": "2"}, {"loja_id": "42"}])
def test_montagem_recusa_venda_diferente(auth, configuracao, conta_atacado, alteracoes):
    """O detalhe precisa corresponder à venda fiscal já localizada."""
    transporte = TransporteFicticio(resposta(venda_ficticia() | alteracoes))
    with pytest.raises(ContratoInvalido, match="origem observadas"):
        consultar_itens_para_montagem(auth, lambda: configuracao, conta_atacado, "31", "42", "515", "905",
                                      "1", autorizado=True, transporte=transporte)


def test_montagem_sem_permissao_financeira_nao_le_erp(auth, conta_atacado):
    """A opção na interface não substitui a autorização no servidor."""
    auth.context = AccessContext(auth.context.profile, auth.context.permissions - {"finance.view"})
    transporte = TransporteFicticio()
    carregar = Mock()
    with pytest.raises(AuthorizationError):
        consultar_itens_para_montagem(auth, carregar, conta_atacado, "31", "42", "515", "905", "1",
                                      autorizado=True, transporte=transporte)
    assert not transporte.calls
    carregar.assert_not_called()
