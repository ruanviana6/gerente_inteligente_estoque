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
    conferir_nfe_por_numero,
    consultar_dados_para_formulario,
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


def test_montagem_sete_linhas_com_ids_textuais_e_variacoes_legadas(auth, configuracao, conta_atacado):
    """A mesma venda pode trazer IDs em formatos distintos por item."""
    venda = venda_ficticia()
    item = venda["produtos"][0]["produto"]
    variacoes = ["", None, 0, "0", "003", 8, 9]
    venda["produtos"] = [
        {"produto": item | {"produto_id": f"00{100 + i}" if i % 2 else 100 + i, "variacao_id": variacao}}
        for i, variacao in enumerate(variacoes)
    ]
    linhas = consultar_itens_para_montagem(
        auth, lambda: configuracao, conta_atacado, "31", "42", "515", "905", "1",
        autorizado=True, transporte=TransporteFicticio(resposta(venda)),
    )
    assert len(linhas) == 7
    assert linhas[1]["Produto no atacado (ID)"] == "00101"
    assert [linha["Variação no atacado (ID)"] for linha in linhas] == [None, None, "0", "0", "003", "8", "9"]


@pytest.mark.parametrize(("campo", "valor"), [
    ("produto_id", ""), ("produto_id", "000"), ("produto_id", True),
    ("produto_id", PRIVADO), ("variacao_id", 1.5), ("variacao_id", "-1"),
])
def test_montagem_recusa_id_invalido_e_mostra_so_indice(auth, configuracao, conta_atacado, campo, valor):
    """A posição auxilia a conferência sem revelar o conteúdo recusado."""
    venda = venda_ficticia()
    venda["produtos"][0]["produto"][campo] = valor
    with pytest.raises(ContratoInvalido, match="linha 1") as erro:
        consultar_itens_para_montagem(
            auth, lambda: configuracao, conta_atacado, "31", "42", "515", "905", "1",
            autorizado=True, transporte=TransporteFicticio(resposta(venda)),
        )
    assert PRIVADO not in str(erro.value)


@pytest.mark.parametrize(("campo", "valor", "coluna", "esperado"), [
    ("desconto_valor", "", "Desconto em valor informado", None),
    ("desconto_porcentagem", "", "Desconto em percentual informado", None),
    ("quantidade", 2, "Quantidade na venda", "2"),
    ("valor_venda", "3.123456780000", "Valor de venda informado", "3.123456780000"),
])
def test_montagem_preserva_decimais_e_descontos_ausentes(campo, valor, coluna, esperado):
    from integracoes.fiscal_read_probe import _itens_para_montagem

    venda = venda_ficticia()
    venda["produtos"][0]["produto"][campo] = valor
    assert _itens_para_montagem(venda)[0][coluna] == esperado


@pytest.mark.parametrize("valor", [None, "", True, "1,230", "1.234567891", "1e5", PRIVADO])
def test_montagem_indica_linha_e_campo_sem_revelar_valor(valor):
    from integracoes.fiscal_read_probe import _itens_para_montagem

    venda = venda_ficticia()
    item = venda["produtos"][0]["produto"]
    venda["produtos"].append({"produto": item | {"valor_total": valor}})
    with pytest.raises(ContratoInvalido, match="linha 2, o campo valor_total") as erro:
        _itens_para_montagem(venda)
    assert PRIVADO not in str(erro.value)


def nota_saida_ficticia():
    return {"id": "843", "loja_id": "001", "tipo_nf": "1", "modelo": "55", "numero_nf": "0094",
            "situacao_nf": "Em aberto", "nome_destinatario": PRIVADO}


def test_numero_comercial_filtra_loja_sem_expor_dados(auth, configuracao, conta):
    transporte = TransporteFicticio(resposta([nota_saida_ficticia()], meta={"total_registros": 1, "total_paginas": 1}))
    resultado = conferir_nfe_por_numero(auth, lambda: configuracao, conta, "001", "00094",
                                        autorizado=True, transporte=transporte)
    assert resultado == {"id": "843", "situacao": "Em aberto"}
    assert len(transporte.calls) == 1
    url = urlsplit(transporte.calls[0][0])
    assert url.path == "/notas_fiscais_produtos"
    assert parse_qs(url.query) == {"loja_id": ["001"], "numero": ["94"], "tipo": ["1"], "pagina": ["1"], "limite": ["2"]}


@pytest.mark.parametrize(("total", "alteracoes"), [
    (0, {}), (2, {}), (1, {"loja_id": "002"}), (1, {"numero_nf": "95"}),
    (1, {"modelo": "65"}), (1, {"tipo_nf": "0"}), (1, {"situacao_nf": "inválido"}),
])
def test_busca_por_numero_rejeita_resultado_inconclusivo(auth, configuracao, conta, total, alteracoes):
    notas = [nota_saida_ficticia() | alteracoes] * total
    transporte = TransporteFicticio(resposta(notas, meta={"total_registros": total, "total_paginas": 1}))
    with pytest.raises(ContratoInvalido):
        conferir_nfe_por_numero(auth, lambda: configuracao, conta, "001", "94", autorizado=True, transporte=transporte)


@pytest.mark.parametrize("status", [402, 404, 500])
def test_diagnostico_http_informa_codigo_sem_corpo(auth, configuracao, conta, status):
    transporte = TransporteFicticio(RespostaGestaoClick(status, PRIVADO.encode()))
    with pytest.raises(GestaoClickError, match=f"HTTP {status}") as erro:
        conferir_nfe_por_numero(auth, lambda: configuracao, conta, "001", "94", autorizado=True, transporte=transporte)
    assert PRIVADO not in str(erro.value)


@pytest.mark.parametrize("caso", ["ok", "cliente divergente", "duas opções", "outra UF", "sem consentimento"])
def test_preparo_formulario_cliente_da_venda(auth, configuracao, conta_atacado, caso):
    cliente = {"id": "94", "tipo_pessoa": "PF", "nome": "Pessoa fictícia",
               "enderecos": [{"endereco": {"estado": "Ceará", "nome_cidade": "Cidade fictícia"}}],
               "observacao": PRIVADO, "cnpj": SECRET_TOKEN}
    if caso == "cliente divergente":
        cliente["id"] = "95"
    if caso == "duas opções":
        cliente["enderecos"] *= 2
    if caso == "outra UF":
        cliente["enderecos"][0]["endereco"]["estado"] = "PE"
    transporte = TransporteFicticio(resposta(venda_ficticia() | {"cliente_id": "94"}), resposta(cliente))

    def consultar_dados():
        return consultar_dados_para_formulario(auth, lambda: configuracao, conta_atacado, "31", "42",
                                               "515", "905", "1", autorizado=caso != "sem consentimento",
                                               transporte=transporte)

    if caso == "ok":
        itens, dados = consultar_dados()
        assert dados["Cidade"] == "Cidade fictícia" and dados["UF"] == "CE" and dados["CPF"] is None
        assert len(itens) == 1 and PRIVADO not in str(dados) and SECRET_TOKEN not in str(dados)
    elif caso == "sem consentimento":
        with pytest.raises(AuthorizationError):
            consultar_dados()
        assert not transporte.calls
        return
    else:
        with pytest.raises(ContratoInvalido):
            consultar_dados()
    assert [urlsplit(url).path for url, _, _ in transporte.calls] == ["/vendas/515", "/clientes/94"]
