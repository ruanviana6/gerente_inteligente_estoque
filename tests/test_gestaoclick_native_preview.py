"""Verifica a prévia nativa com respostas fictícias, sem banco nem ERP real.

O contrato público descreve IDs dos produtos como inteiros e quantidades como
texto decimal. Nenhuma ausência da origem pode virar SKU, devolução zero ou
identidade artificial de linha durante esta conferência.
"""

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from test_gestaoclick_account_setup import (
    ACCESS_TOKEN,
    BASE,
    OUTRA_ORGANIZACAO,
    PRIVADO,
    SECRET_TOKEN,
    TransporteFicticio,
    alterar_acesso,
    consultar,
)
from test_gestaoclick_account_setup import auth as auth
from test_gestaoclick_account_setup import configuracao as configuracao

from integracoes.gestaoclick import ContratoInvalido, GestaoClickError, RespostaGestaoClick
from integracoes.native_preview import consultar_previa_vendas
from web.errors import AuthorizationError

INICIO = date(2025, 1, 1)
FIM = date(2025, 1, 31)
SENSIVEL = "DADO-PRIVADO-NAO-EXIBIR"


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    """Impede que qualquer caminho de teste alcance a Internet."""
    monkeypatch.setattr("urllib.request.build_opener", Mock(side_effect=AssertionError("Rede proibida.")))
    monkeypatch.setattr("integracoes.account_setup.time.sleep", Mock())


@pytest.fixture
def conta(auth, configuracao):
    """Emite uma conta assinada usando somente os catálogos fictícios existentes."""
    return consultar(auth, configuracao)[0]


def documento(identificador="venda-001", **changes):
    """Replica somente a estrutura pública e inclui sentinelas que devem sumir."""
    return {"id": identificador, "codigo": "PED-001", "data": "2025-01-15", "situacao_id": "1",
            "nome_cliente": PRIVADO, "nome_loja": SENSIVEL, "valor_total": SENSIVEL,
            "observacoes": ACCESS_TOKEN, "source_updated_at": SENSIVEL,
            "produtos": [{"produto": {"produto_id": 101, "variacao_id": 0, "quantidade": "2.500000",
                                      "nome_produto": SENSIVEL, "valor_venda": SENSIVEL,
                                      "sku": SENSIVEL, "returned_quantity": SENSIVEL,
                                      "source_line_id": SECRET_TOKEN}}], **changes}


def resposta(rows=None, *, total=None, limite=5, meta_changes=None, **changes):
    """Produz uma primeira página consistente sem atribuir cobertura integral."""
    rows = [documento()] if rows is None else rows
    total = len(rows) if total is None else total
    continua = total > len(rows)
    meta = {"total_registros": total, "total_da_pagina": len(rows), "pagina_atual": 1,
            "limite_por_pagina": limite, "proxima_pagina": "2" if continua else None,
            "proxima_url": BASE + "/vendas?pagina=2&limite=5" if continua else None}
    meta.update(meta_changes or {})
    payload = {"code": 200, "status": "success", "data": rows, "meta": meta, **changes}
    return RespostaGestaoClick(200, json.dumps(payload).encode())


def executar(auth, configuracao, conta, response=None, *, depois=None, **kwargs):
    """Chama a operação pública com transporte local e devolve sua trilha GET."""
    transport = TransporteFicticio(response if response is not None else resposta(), depois=depois)
    values = {"store_id": "001", "inicio": INICIO, "fim": FIM, "tipo": "produto", "autorizado": True}
    values.update(kwargs)
    result = consultar_previa_vendas(auth, lambda: configuracao, conta, transporte=transport, **values)
    return result, transport


@pytest.mark.parametrize("tipo", ["produto", "vendas_balcao"])
def test_get_unico_sem_json_maps_rpc_ou_promessa_de_importacao(auth, configuracao, conta, tipo):
    """Cada universo explícito usa uma consulta fixa e permanece uma amostra."""
    preview, transport = executar(auth, configuracao, conta, tipo=tipo)
    assert len(transport.calls) == 1
    url, headers, timeout = transport.calls[0]
    assert urlsplit(url).scheme == "https" and urlsplit(url).netloc == "api.gestaoclick.com"
    assert urlsplit(url).path == "/vendas"
    assert parse_qs(urlsplit(url).query) == {"pagina": ["1"], "limite": ["5"], "tipo": [tipo],
                                           "loja_id": ["001"], "data_inicio": ["2025-01-01"],
                                           "data_fim": ["2025-01-31"]}
    assert headers["access-token"] == ACCESS_TOKEN and headers["secret-access-token"] == SECRET_TOKEN
    assert timeout <= 15
    assert preview.loja_solicitada == "001" and preview.inicio == INICIO and preview.fim == FIM
    assert preview.tipo == tipo and preview.amostra_parcial is True
    assert preview.imported is False and preview.coverage_confirmed is False
    assert preview.total_informado == preview.documentos_recebidos == 1
    assert preview.versao_contrato
    auth.rpc.assert_not_called()


def test_projecao_preserva_decimais_e_ausencias_sem_financas_ou_dados_pessoais(auth, configuracao, conta):
    """Campos extras da resposta não viram evidência de identidade ou devolução."""
    preview, _ = executar(auth, configuracao, conta)
    doc = preview.documentos[0]
    item = doc.itens[0]
    assert (doc.id, doc.codigo, doc.data, doc.situacao_id) == ("venda-001", "PED-001", date(2025, 1, 15), "1")
    assert item.produto_id == "101" and item.variacao_id == "0"
    assert item.quantidade == Decimal("2.500000")
    assert item.sigla_unidade is None
    assert item.source_line_id is None and item.sku is None and item.returned_quantity is None
    assert doc.source_updated_at is None
    encoded = str(asdict(preview))
    for sentinel in (ACCESS_TOKEN, SECRET_TOKEN, PRIVADO, SENSIVEL):
        assert sentinel not in encoded
        assert sentinel not in repr(preview)
    assert "venda-001" not in repr(preview)
    with pytest.raises(FrozenInstanceError):
        preview.loja_solicitada = "outra"
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("unidade, esperada", [("CX", "CX"), ("kg", "kg"), ("M²", "M²"),
                                               ("  CX  ", "CX"), ("U" * 20, "U" * 20)])
def test_unidade_documentada_preserva_quantidade_sem_conversao(auth, configuracao, conta, unidade, esperada):
    """Duas caixas continuam duas caixas; a conferência não inventa fator de conversão."""
    doc = documento()
    produto = doc["produtos"][0]["produto"]
    produto.update(quantidade="2", sigla_unidade=unidade)
    preview, transport = executar(auth, configuracao, conta, resposta([doc]))
    item = preview.documentos[0].itens[0]
    assert item.quantidade == Decimal("2") and item.sigla_unidade == esperada
    assert item.returned_quantity is None and item.sku is None
    assert len(transport.calls) == 1
    assert all(sentinel not in str(asdict(preview)) for sentinel in (PRIVADO, SENSIVEL, ACCESS_TOKEN, SECRET_TOKEN))
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("unidade", ["__ausente__", None, "", "   ", "\u00a0"])
def test_unidade_desconhecida_nao_vira_unidade_padrao(auth, configuracao, conta, unidade):
    """Ausência ou texto vazio não autoriza chamar a quantidade de unidades ou caixas."""
    doc = documento()
    if unidade != "__ausente__":
        doc["produtos"][0]["produto"]["sigla_unidade"] = unidade
    preview, _ = executar(auth, configuracao, conta, resposta([doc]))
    item = preview.documentos[0].itens[0]
    assert item.sigla_unidade is None and item.quantidade == Decimal("2.500000")
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("unidade", [1, True, [], {}, "U" * 21, "CX\n", "\t", "CX\x00",
                                    "CX\u202e", "CX\u200b", "CX\u2028", "CX\u2029", SENSIVEL])
def test_unidade_invalida_recusa_amostra_sem_expor_valor(auth, configuracao, conta, unidade):
    """Tipos, tamanho excessivo e controles invisíveis não alcançam a tabela nem o erro."""
    doc = documento()
    doc["produtos"][0]["produto"]["sigla_unidade"] = unidade
    transport = TransporteFicticio(resposta([doc]))
    with pytest.raises(ContratoInvalido) as erro:
        consultar_previa_vendas(auth, lambda: configuracao, conta, "001", INICIO, FIM,
                               autorizado=True, transporte=transport)
    assert "unidade" in str(erro.value).lower()
    assert all(sentinel not in str(erro.value) for sentinel in (PRIVADO, SENSIVEL, ACCESS_TOKEN, SECRET_TOKEN))
    if isinstance(unidade, str) and unidade.strip():
        assert unidade not in str(erro.value)
    assert len(transport.calls) == 1
    auth.rpc.assert_not_called()


def test_itens_do_mesmo_produto_com_unidades_distintas_nao_sao_agregados(auth, configuracao, conta):
    """Linhas de caixas e unidades preservam separadamente suas medidas na prévia."""
    doc = documento()
    primeiro = doc["produtos"][0]["produto"]
    primeiro.update(quantidade="2", sigla_unidade="CX")
    segundo = deepcopy(doc["produtos"][0])
    segundo["produto"]["sigla_unidade"] = "UN"
    doc["produtos"].append(segundo)
    preview, _ = executar(auth, configuracao, conta, resposta([doc]))
    assert [(item.quantidade, item.sigla_unidade) for item in preview.documentos[0].itens] == [
        (Decimal("2"), "CX"), (Decimal("2"), "UN"),
    ]
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("codigo, esperado", [("2025/001", "2025/001"),
                                              ("  PV 2025/001.2  ", "PV 2025/001.2"),
                                              ("C" * 100, "C" * 100)])
def test_codigo_comercial_aceita_pontuacao_sem_mudar_id(auth, configuracao, conta, codigo, esperado):
    """O número comercial é texto de conferência; não vira rota nem identidade da venda."""
    preview, transport = executar(auth, configuracao, conta, resposta([documento(codigo=codigo)]))
    doc = preview.documentos[0]
    assert doc.id == "venda-001" and doc.codigo == esperado
    assert len(transport.calls) == 1
    assert "2025/001" not in transport.calls[0][0]
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("codigo", ["__ausente__", None, "", "   ", 1, True, [], {},
                                   "2025/001\n", "\t2025/001", "2025\x00/001", "2025\u202e/001",
                                   "2025\u2028/001", "2025\u2029/001",
                                   "<b>2025/001</b>", "CODIGO-PRIVADO-" + "X" * 101])
def test_codigo_comercial_invalido_nao_vaza_no_erro(auth, configuracao, conta, codigo):
    """Número obrigatório, limitado e sem controles não aceita conteúdo arbitrário do ERP."""
    doc = documento()
    if codigo == "__ausente__":
        doc.pop("codigo")
    else:
        doc["codigo"] = codigo
    transport = TransporteFicticio(resposta([doc]))
    with pytest.raises(ContratoInvalido) as erro:
        consultar_previa_vendas(auth, lambda: configuracao, conta, "001", INICIO, FIM,
                               autorizado=True, transporte=transport)
    assert "2025/001" not in str(erro.value) and "CODIGO-PRIVADO" not in str(erro.value)
    assert all(sentinel not in str(erro.value) for sentinel in (PRIVADO, SENSIVEL, ACCESS_TOKEN, SECRET_TOKEN))
    assert len(transport.calls) == 1
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("campo", ["id", "situacao_id"])
def test_pontuacao_comercial_nao_relaxa_identificadores_tecnicos(auth, configuracao, conta, campo):
    """Permitir barra no número da venda não autoriza barra nos IDs usados como identidade."""
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta([documento(**{campo: "2025/001"})]))
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("variacao", [None, "", "__ausente__", 0, 7, "007"])
def test_variacao_desconhecida_nao_se_confunde_com_produto(auth, configuracao, conta, variacao):
    """Ausência permanece None; zero explícito e IDs legados não são descartados."""
    doc = documento()
    item = doc["produtos"][0]["produto"]
    if variacao == "__ausente__":
        item.pop("variacao_id")
    else:
        item["variacao_id"] = variacao
    preview, _ = executar(auth, configuracao, conta, resposta([doc]))
    expected = None if variacao in (None, "", "__ausente__") else str(variacao)
    assert preview.documentos[0].itens[0].variacao_id == expected


def test_itens_repetidos_sao_preservados_sem_inventar_id_estavel(auth, configuracao, conta):
    """A prévia mostra as duas linhas sem deduplicá-las nem usar posição como ID."""
    doc = documento()
    doc["produtos"].append(deepcopy(doc["produtos"][0]))
    preview, _ = executar(auth, configuracao, conta, resposta([doc]))
    assert len(preview.documentos[0].itens) == 2
    assert all(x.source_line_id is None for x in preview.documentos[0].itens)


@pytest.mark.parametrize("forma", ["moderna", "legada", "ambas"])
def test_aliases_documentados_e_amostra_limitada_sem_continuar_paginas(auth, configuracao, conta, forma):
    """O total informado não transforma a página deliberadamente limitada em carga."""
    rows = [documento(f"venda-{i}") for i in range(5)]
    response = resposta(rows, total=17)
    body = json.loads(response.body)
    if forma in {"legada", "ambas"}:
        body["meta"]["total_registros_pagina"] = 5
        body["meta"]["total_paginas"] = 4
    if forma == "legada":
        body["meta"].pop("total_da_pagina")
    response = RespostaGestaoClick(200, json.dumps(body).encode())
    preview, transport = executar(auth, configuracao, conta, response)
    assert len(preview.documentos) == 5 and preview.total_informado == 17
    assert preview.documentos_recebidos == 5 and preview.amostra_parcial is True
    assert len(transport.calls) == 1


def test_limite_efetivo_maior_nao_exibe_mais_de_cinco_documentos(auth, configuracao, conta):
    """Uma página válida maior é explicitamente reduzida para a amostra projetada."""
    rows = [documento(f"venda-{i}") for i in range(20)]
    preview, transport = executar(auth, configuracao, conta, resposta(rows, total=23, limite=20))
    assert len(preview.documentos) == 5 and preview.documentos_recebidos == 20
    assert preview.total_informado == 23 and preview.amostra_parcial is True
    assert len(transport.calls) == 1


def test_resultado_vazio_nao_prova_cobertura_ou_devolucao_zero(auth, configuracao, conta):
    """Nenhuma venda retornada pode significar filtro ou visibilidade insuficiente."""
    preview, _ = executar(auth, configuracao, conta, resposta([]))
    assert preview.documentos == ()
    assert preview.total_informado == preview.documentos_recebidos == 0
    assert preview.coverage_confirmed is False and preview.imported is False


def test_url_de_continuacao_nao_e_seguida_nem_exposta(auth, configuracao, conta):
    """A amostra nunca usa um endereço recebido da API para uma segunda requisição."""
    rows = [documento(f"venda-{i}") for i in range(5)]
    response = resposta(rows, total=8,
                        meta_changes={"proxima_url": "https://destino-ficticio.invalid/" + SENSIVEL})
    preview, transport = executar(auth, configuracao, conta, response)
    assert len(transport.calls) == 1
    assert all(urlsplit(call[0]).netloc == "api.gestaoclick.com" for call in transport.calls)
    assert SENSIVEL not in str(asdict(preview))


def test_periodo_de_um_dia_e_quantidade_zero_preservam_os_valores(auth, configuracao, conta):
    """Zero explícito na origem é diferente de devolução desconhecida."""
    doc = documento()
    doc["produtos"][0]["produto"]["quantidade"] = "0.000000"
    dia = date(2025, 1, 15)
    preview, _ = executar(auth, configuracao, conta, resposta([doc]), inicio=dia, fim=dia)
    item = preview.documentos[0].itens[0]
    assert item.quantidade == Decimal("0") and item.returned_quantity is None


@pytest.mark.parametrize("consentimento", [False, None, 0, 1, "true"])
def test_sem_consentimento_nao_le_secrets_nem_consulta(auth, conta, consentimento):
    """O aceite exige bool True, não uma aproximação truthy."""
    loader, transport = Mock(), TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_previa_vendas(auth, loader, conta, "001", INICIO, FIM,
                               autorizado=consentimento, transporte=transport)
    loader.assert_not_called()
    assert transport.calls == []
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("campo", ["role", "active", "permission", "user_id", "organization_id", "generation"])
def test_conta_nao_pode_ser_reutilizada_por_outro_acesso(auth, conta, campo):
    """Mudanças de sessão, tenant ou autorização impedem até o carregamento dos Secrets."""
    alterar_acesso(auth, campo)
    loader, transport = Mock(), TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_previa_vendas(auth, loader, conta, "001", INICIO, FIM,
                               autorizado=True, transporte=transport)
    loader.assert_not_called()
    assert not transport.calls


def test_conta_adulterada_e_loja_nao_conferida_sao_recusadas(auth, conta):
    """IDs fornecidos pelo navegador não substituem o catálogo assinado do servidor."""
    loader, transport = Mock(), TransporteFicticio()
    adulterada = replace(conta, _lojas=(("outra", "Nome arbitrário"),))
    for tested, store in ((adulterada, "outra"), (conta, "outra")):
        with pytest.raises((AuthorizationError, ContratoInvalido)):
            consultar_previa_vendas(auth, loader, tested, store, INICIO, FIM,
                                   autorizado=True, transporte=transport)
    loader.assert_not_called()
    assert not transport.calls


def test_conta_expirada_impede_consulta(auth, conta, monkeypatch):
    """O limite de validade é conferido também no novo caminho de vendas."""
    monkeypatch.setattr("integracoes.account_setup.time.monotonic", lambda: conta._expira_em)
    loader, transport = Mock(), TransporteFicticio()
    with pytest.raises((AuthorizationError, ContratoInvalido)):
        consultar_previa_vendas(auth, loader, conta, "001", INICIO, FIM,
                               autorizado=True, transporte=transport)
    loader.assert_not_called()
    assert not transport.calls


@pytest.mark.parametrize("campo", ["role", "active", "permission", "user_id", "organization_id", "generation"])
@pytest.mark.parametrize("falha_http", [False, True])
def test_revogacao_durante_http_impede_entrega_inclusive_em_falhas(auth, configuracao, conta, campo, falha_http):
    """Uma resposta ou erro não entrega dados depois de a autorização mudar."""
    response = OSError(SENSIVEL) if falha_http else resposta()
    with pytest.raises(AuthorizationError) as error:
        executar(auth, configuracao, conta, response, depois=lambda _: alterar_acesso(auth, campo))
    assert SENSIVEL not in str(error.value)
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("campo,valor", [("GESTAOCLICK_ACCESS_TOKEN", "nova-chave-ficticia"),
                                         ("GESTAOCLICK_ORGANIZATION_ID", OUTRA_ORGANIZACAO),
                                         ("GESTAOCLICK_ENABLED", False)])
def test_troca_configuracao_durante_http_descarta_resultado(auth, configuracao, conta, campo, valor):
    """O vínculo de configuração deve permanecer válido até a entrega da amostra."""
    with pytest.raises((AuthorizationError, GestaoClickError)):
        executar(auth, configuracao, conta, depois=lambda _: configuracao.update({campo: valor}))
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("campo,valor", [
    ("GESTAOCLICK_ACCESS_TOKEN", "outro-access-token-ficticio"),
    ("GESTAOCLICK_SECRET_ACCESS_TOKEN", "outro-secret-token-ficticio"),
    ("GESTAOCLICK_BASE_URL", "https://api.gestaoclick.com/v1"),
])
def test_troca_da_conta_configurada_entre_etapas_impede_qualquer_get(auth, configuracao, conta, campo, valor):
    """Uma loja conferida com uma chave não autoriza outra conta que reutilize o mesmo ID."""
    configuracao[campo] = valor
    transport = TransporteFicticio(resposta())
    with pytest.raises(AuthorizationError):
        consultar_previa_vendas(auth, lambda: configuracao, conta, "001", INICIO, FIM,
                               autorizado=True, transporte=transport)
    assert not transport.calls
    auth.rpc.assert_not_called()


def test_configuracao_posterior_da_fonte_nao_invalida_a_conta_conferida(auth, configuracao, conta):
    """A fonte analítica e mapas não identificam a conta nem contêm credenciais de origem."""
    configuracao.update({"GESTAOCLICK_SOURCE_ID": "30000000-0000-4000-8000-000000000001",
                        "GESTAOCLICK_STORE_ID": "001", "GESTAOCLICK_FIELD_MAP_JSON": "{}",
                        "GESTAOCLICK_STATUS_MAP_JSON": "{}", "GESTAOCLICK_PERIOD_FILTERS_JSON": "{}"})
    preview, transport = executar(auth, configuracao, conta)
    assert len(transport.calls) == 1 and len(preview.documentos) == 1
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("digest", [b"digest-adulterado", b"", "digest-com-tipo-incorreto"])
def test_digest_adulterado_na_conta_nao_autoriza_a_previa(auth, conta, digest):
    """O digest integra a assinatura, sem transformar seu conhecimento em autorização."""
    adulterada = replace(conta, _config_stamp=digest)
    loader, transport = Mock(), TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_previa_vendas(auth, loader, adulterada, "001", INICIO, FIM,
                               autorizado=True, transporte=transport)
    loader.assert_not_called()
    assert not transport.calls


def test_rotacao_exige_nova_conferencia_e_nao_expoe_chaves_no_snapshot(auth, configuracao, conta):
    """A nova conferência autoriza a nova chave sem guardar seu valor no catálogo."""
    nova_chave = "NOVA-CHAVE-EXCLUSIVAMENTE-FICTICIA"
    configuracao["GESTAOCLICK_ACCESS_TOKEN"] = nova_chave
    renovada = consultar(auth, configuracao)[0]
    assert renovada._config_stamp != conta._config_stamp
    for value in (asdict(renovada), repr(renovada)):
        assert nova_chave not in str(value) and SECRET_TOKEN not in str(value)
    preview, transport = executar(auth, configuracao, renovada)
    assert len(preview.documentos) == 1 and len(transport.calls) == 1
    assert transport.calls[0][1]["access-token"] == nova_chave


@pytest.mark.parametrize("inicio,fim,tipo", [
    (FIM, INICIO, "produto"),
    (INICIO, FIM + timedelta(days=1), "produto"),
    ("2025-01-01", FIM, "produto"),
    (INICIO, None, "produto"),
    (INICIO, FIM, "servico"),
    (INICIO, FIM, "produto&loja_id=outra"),
    (INICIO, FIM, ""),
])
def test_filtros_invalidos_recusados_antes_do_http(auth, configuracao, conta, inicio, fim, tipo):
    """A prévia não aceita rotas, tipos arbitrários ou janelas além de 31 dias."""
    transport = TransporteFicticio()
    with pytest.raises(ContratoInvalido):
        consultar_previa_vendas(auth, lambda: configuracao, conta, "001", inicio, fim,
                               tipo=tipo, autorizado=True, transporte=transport)
    assert not transport.calls


def test_periodo_futuro_nao_gera_chamada(auth, configuracao, conta):
    """Uma margem de dois dias mantém o teste independente do fuso do executor."""
    futuro = date.today() + timedelta(days=2)
    transport = TransporteFicticio()
    with pytest.raises(ContratoInvalido):
        consultar_previa_vendas(auth, lambda: configuracao, conta, "001", futuro, futuro,
                               autorizado=True, transporte=transport)
    assert not transport.calls


@pytest.mark.parametrize("campo,valor", [("id", None), ("id", ""), ("id", "invalido/segredo"),
                                        ("codigo", True), ("data", "2025-02-01"),
                                        ("data", "2025-01-15T00:00:00"), ("data", "2025-1-15"),
                                        ("situacao_id", "999-nao-conferida"), ("produtos", {})])
def test_documento_invalido_recusa_amostra_sem_vazar_valores(auth, configuracao, conta, campo, valor):
    """A amostra não pode apresentar documentos fora do período ou catálogo autorizado."""
    doc = documento(**{campo: valor})
    with pytest.raises(ContratoInvalido) as error:
        executar(auth, configuracao, conta, resposta([doc]))
    assert SENSIVEL not in str(error.value) and PRIVADO not in str(error.value)


@pytest.mark.parametrize("campo,valor", [
    ("produto_id", None), ("produto_id", True), ("produto_id", 0), ("produto_id", -1),
    ("produto_id", 1.5), ("produto_id", "../produto"),
    ("variacao_id", True), ("variacao_id", -1),
    ("quantidade", None), ("quantidade", True), ("quantidade", 1), ("quantidade", "NaN"),
    ("quantidade", "Infinity"), ("quantidade", "-1"), ("quantidade", "1e2"),
    ("quantidade", "1,25"), ("quantidade", " 1.25 "), ("quantidade", "0.0000001"),
    ("quantidade", "1000000001"),
])
def test_item_invalido_nao_e_coagido_em_numero_ou_identidade(auth, configuracao, conta, campo, valor):
    """Coerções permissivas poderiam alterar quantidades e relações produto/variação."""
    doc = documento()
    doc["produtos"][0]["produto"][campo] = valor
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta([doc]))


def test_produto_textual_legado_preserva_identidade_sem_virar_sku(auth, configuracao, conta):
    """Zeros de um ID textual não são removidos por conversão numérica."""
    doc = documento()
    doc["produtos"][0]["produto"]["produto_id"] = "00101"
    preview, _ = executar(auth, configuracao, conta, resposta([doc]))
    assert preview.documentos[0].itens[0].produto_id == "00101"
    assert preview.documentos[0].itens[0].sku is None


def test_ids_de_documentos_duplicados_na_pagina_sao_recusados(auth, configuracao, conta):
    """O total de linhas não basta quando duas posições representam o mesmo documento."""
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta([documento(), documento()]))


def test_identidade_de_documento_duplicada_fora_da_amostra_tambem_bloqueia(auth, configuracao, conta):
    """A redução visual a cinco documentos não oculta inconsistências na página recebida."""
    rows = [documento(f"venda-{i}") for i in range(6)]
    rows[-1]["id"] = rows[0]["id"]
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta(rows, limite=20))


def test_item_invalido_de_documento_nao_exibido_tambem_bloqueia(auth, configuracao, conta):
    """A redução da exibição não permite validar apenas o início da página recebida."""
    rows = [documento(f"venda-{i}") for i in range(6)]
    rows[-1]["produtos"][0]["produto"]["quantidade"] = "NaN"
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta(rows, limite=20))


def test_orcamento_de_itens_considera_documentos_fora_da_exibicao(auth, configuracao, conta):
    """O teto limita toda a resposta, inclusive o sexto documento que não será mostrado."""
    rows = [documento(f"venda-{i}") for i in range(6)]
    rows[-1]["produtos"] *= 96
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta(rows, limite=20))


def test_limite_de_itens_rejeita_excesso_sem_truncar_linhas(auth, configuracao, conta):
    """A amostra não deve parecer completa quando corta itens dentro de um documento."""
    doc = documento()
    doc["produtos"] *= 101
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta([doc]))


def test_limite_de_documentos_recebidos_rejeita_pagina_abusiva(auth, configuracao, conta):
    """A resposta permanece limitada mesmo quando o servidor ignora limite=5."""
    rows = [documento(f"venda-{i}") for i in range(101)]
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta(rows, limite=101))


@pytest.mark.parametrize("mudancas", [
    {"total_registros": True}, {"total_registros": "1"}, {"total_registros": -1},
    {"total_da_pagina": 0}, {"total_registros_pagina": 2},
    {"pagina_atual": 2}, {"pagina_atual": True}, {"limite_por_pagina": 0},
    {"limite_por_pagina": 101}, {"total_paginas": 2},
])
def test_metadados_inconsistentes_recusam_a_amostra(auth, configuracao, conta, mudancas):
    """Mesmo uma amostra não aceita contagens divergentes ou tipos ambíguos."""
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta(meta_changes=mudancas))


@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500])
def test_falhas_http_nao_expoem_corpos_nem_credenciais(auth, configuracao, conta, status):
    """Não segue redirecionamentos, repete GET ou mostra respostas sensíveis de erro."""
    transport = TransporteFicticio(RespostaGestaoClick(status, SENSIVEL.encode()))
    with pytest.raises(GestaoClickError) as error:
        consultar_previa_vendas(auth, lambda: configuracao, conta, "001", INICIO, FIM,
                               autorizado=True, transporte=transport)
    assert SENSIVEL not in str(error.value)
    assert len(transport.calls) == 1
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("response", [
    OSError(SENSIVEL), TimeoutError(SENSIVEL), GestaoClickError(SENSIVEL),
    RespostaGestaoClick(200, SENSIVEL.encode()),
    resposta(code=201), resposta(status="error"), resposta(data={}), resposta(meta=None),
])
def test_respostas_e_excecoes_invalidas_falham_com_mensagem_segura(auth, configuracao, conta, response):
    """Conteúdo arbitrário nunca é interpolado na explicação entregue ao usuário."""
    with pytest.raises(GestaoClickError) as error:
        executar(auth, configuracao, conta, response)
    for sentinel in (SENSIVEL, ACCESS_TOKEN, SECRET_TOKEN, PRIVADO):
        assert sentinel not in str(error.value)
    auth.rpc.assert_not_called()
