"""Testa a descoberta autorizada da conta com transporte e RPC exclusivamente fictícios."""

import json
from dataclasses import FrozenInstanceError, asdict, replace
from unittest.mock import Mock

import pytest
from test_integration_interface import AuthRPCFicticia

from integracoes.account_setup import consultar_conta, registrar_fonte, validar_conta
from integracoes.gestaoclick import (
    ContratoInvalido,
    GestaoClickError,
    IntegracaoDesabilitada,
    RespostaGestaoClick,
)
from web.auth import AccessContext
from web.errors import AuthorizationError, ServiceUnavailable

ORGANIZACAO = "10000000-0000-4000-8000-000000000001"
OUTRA_ORGANIZACAO = "10000000-0000-4000-8000-000000000002"
FONTE = "30000000-0000-4000-8000-000000000001"
BASE = "https://api.gestaoclick.com"
ACCESS_TOKEN = "ACCESS-EXCLUSIVAMENTE-FICTICIO"
SECRET_TOKEN = "SECRET-EXCLUSIVAMENTE-FICTICIO"
PRIVADO = "pessoa-ficticia@example.invalid"


def resposta(data, *, meta=None, **changes):
    """Cria um envelope JSON local sem qualquer tráfego de rede."""
    payload = {"code": 200, "status": "success", "data": data, **changes}
    if meta is not None:
        payload["meta"] = meta
    return RespostaGestaoClick(200, json.dumps(payload).encode())


def lojas():
    """Inclui campos indevidos para verificar que somente id e nome são preservados."""
    return resposta([{"id": "001", "nome": "Loja fictícia", "email": PRIVADO,
                      "access-token": ACCESS_TOKEN, "financeiro": "99.00"}])


def situacoes(pagina=1, total=2, **meta_changes):
    """Gera páginas completas conforme os metadados publicados do GestãoClick."""
    start = (pagina - 1) * 100
    data = [{"id": str(i), "nome": "Situação fictícia " + str(i),
             "secret-access-token": SECRET_TOKEN, "cliente": PRIVADO}
            for i in range(start, min(start + 100, total))]
    next_page = pagina + 1 if start + len(data) < total else None
    meta = {"total_registros": total, "total_da_pagina": len(data), "pagina_atual": pagina,
            "limite_por_pagina": 100, "proxima_pagina": str(next_page) if next_page else None,
            "proxima_url": BASE + f"/situacoes_vendas?pagina={next_page}&limite=100" if next_page else None,
            **meta_changes}
    return resposta(data, meta=meta)


class TransporteFicticio:
    """Expõe apenas GET e permite alterar autorização enquanto o ERP responderia."""

    def __init__(self, *responses, depois=None):
        """Guarda respostas fictícias e callback de revogação sem cliente HTTP real."""
        self.responses = list(responses)
        self.depois = depois
        self.calls = []

    def get(self, url, headers, timeout):
        """Registra os argumentos e entrega a próxima resposta ou falha controlada."""
        self.calls.append((url, dict(headers), timeout))
        if self.depois:
            self.depois(len(self.calls))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    """Bloqueia qualquer tentativa de conexão e substitui apenas a espera entre GETs."""
    monkeypatch.setattr("urllib.request.build_opener", Mock(side_effect=AssertionError("Rede proibida.")))
    monkeypatch.setattr("integracoes.account_setup.time.sleep", Mock())


@pytest.fixture
def auth():
    """Cria Master com organização UUID e RPC local auditável."""
    session = AuthRPCFicticia()
    session.context = AccessContext(replace(session.context.profile, organization_id=ORGANIZACAO),
                                    session.context.permissions)
    session.rpc = Mock(return_value={"id": FONTE})
    return session


@pytest.fixture
def configuracao():
    """Fornece Secrets fictícios suficientes para conexão, sem loja ou fonte predefinida."""
    return {"GESTAOCLICK_ENABLED": True, "GESTAOCLICK_BASE_URL": BASE,
            "GESTAOCLICK_ORGANIZATION_ID": ORGANIZACAO,
            "GESTAOCLICK_ACCESS_TOKEN": ACCESS_TOKEN,
            "GESTAOCLICK_SECRET_ACCESS_TOKEN": SECRET_TOKEN}


def consultar(auth, configuracao, *responses, depois=None):
    """Executa a API pública somente com respostas locais explicitamente fornecidas."""
    transporte = TransporteFicticio(*(responses or (lojas(), situacoes())), depois=depois)
    return consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transporte), transporte


def alterar_acesso(auth, campo):
    """Reproduz alterações em todas as dimensões de autorização da sessão."""
    if campo == "generation":
        auth.generation += 1
    elif campo == "permission":
        auth.context = AccessContext(auth.context.profile, frozenset())
    elif campo == "permissions_changed":
        auth.context = AccessContext(auth.context.profile, auth.context.permissions | {"trends.view"})
    else:
        values = {"user_id": "outro-usuario", "organization_id": OUTRA_ORGANIZACAO,
                  "role": "Administrador", "active": False}
        auth.context = AccessContext(replace(auth.context.profile, **{campo: values[campo]}),
                                     auth.context.permissions)


def assert_seguro(value):
    """Recusa qualquer token, campo financeiro ou identificação pessoal nos resultados."""
    for privado in (ACCESS_TOKEN, SECRET_TOKEN, PRIVADO, "99.00"):
        assert privado not in str(value)


def test_catalogos_minimos_gets_fixos_snapshot_imutavel_sem_credenciais(auth, configuracao):
    """A conta entrega somente campos permitidos; alterações na UI não mudam a assinatura."""
    conta, transporte = consultar(auth, configuracao)
    assert conta.lojas == [{"id": "001", "nome": "Loja fictícia"}]
    assert conta.situacoes == [{"id": "0", "nome": "Situação fictícia 0"},
                               {"id": "1", "nome": "Situação fictícia 1"}]
    assert conta.organization_id == ORGANIZACAO
    assert [call[0] for call in transporte.calls] == [BASE + "/lojas", BASE + "/situacoes_vendas?pagina=1&limite=100"]
    assert all(call[1] == {"access-token": ACCESS_TOKEN, "secret-access-token": SECRET_TOKEN,
                           "Accept": "application/json"} and call[2] == 15 for call in transporte.calls)
    assert_seguro(asdict(conta))
    assert "Loja fictícia" not in repr(conta)
    assert "Situação fictícia" not in repr(conta)
    conta.lojas.append({"id": "outra", "nome": "Adulterada"})
    conta.lojas[0]["id"] = "outra"
    conta.situacoes.clear()
    assert validar_conta(auth, conta) is conta
    assert len(conta.lojas) == 1 and len(conta.situacoes) == 2
    with pytest.raises(FrozenInstanceError):
        conta.organization_id = OUTRA_ORGANIZACAO
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("total", [0, 1, 99, 100, 101, 200, 201, 300])
def test_paginas_oficiais_completas_dentro_budget(auth, configuracao, total):
    """Cada total admissível exige a quantidade exata de páginas e nunca perde situações."""
    pages = max(1, (total + 99) // 100)
    conta, transporte = consultar(auth, configuracao, lojas(), *(situacoes(i, total) for i in range(1, pages + 1)))
    assert len(conta.situacoes) == total
    assert len(transporte.calls) == 1 + pages <= 4
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("consentimento", [False, None, 0, 1, "true"])
def test_sem_consentimento_nao_le_secrets_nem_http(auth, consentimento):
    """Confirmação exige bool True; valores semelhantes não liberam consulta."""
    carregar = Mock()
    transporte = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_conta(auth, carregar, autorizado=consentimento, transporte=transporte)
    carregar.assert_not_called()
    assert not transporte.calls


@pytest.mark.parametrize("campo", ["role", "active", "permission"])
def test_nao_master_ativo_autorizado_nao_carrega_configuracao(auth, campo):
    """A autorização precede inclusive o acesso às credenciais protegidas."""
    alterar_acesso(auth, campo)
    carregar = Mock()
    with pytest.raises(AuthorizationError):
        consultar_conta(auth, carregar, autorizado=True, transporte=TransporteFicticio())
    carregar.assert_not_called()


@pytest.mark.parametrize("organizacao", [None, "", "uuid-invalido", [], 42, OUTRA_ORGANIZACAO])
def test_organizacao_protegida_invalida_ou_outra_impede_http(auth, configuracao, organizacao):
    """Nenhum Master de outro tenant pode usar os Secrets globais desta conta."""
    configuracao["GESTAOCLICK_ORGANIZATION_ID"] = organizacao
    transporte = TransporteFicticio()
    with pytest.raises((AuthorizationError, ContratoInvalido)):
        consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert not transporte.calls


@pytest.mark.parametrize("enabled", [False, "false", None])
def test_conexao_desativada_impede_http(auth, configuracao, enabled):
    """Consentimento não substitui ativação explícita nos Secrets."""
    if enabled is None:
        configuracao.pop("GESTAOCLICK_ENABLED")
    else:
        configuracao["GESTAOCLICK_ENABLED"] = enabled
    with pytest.raises(IntegracaoDesabilitada):
        consultar(auth, configuracao)


@pytest.mark.parametrize("campo", ["user_id", "organization_id", "role", "active", "generation",
                                    "permission", "permissions_changed"])
@pytest.mark.parametrize("numero_get", [1, 2, 3, 4])
def test_mudanca_de_acesso_em_cada_get_aborta_sem_retorno(auth, configuracao, campo, numero_get):
    """Revogação ou troca de sessão em qualquer página descarta todos os catálogos."""
    transporte = TransporteFicticio(lojas(), *(situacoes(i, 300) for i in range(1, 4)),
                                   depois=lambda n: alterar_acesso(auth, campo) if n == numero_get else None)
    with pytest.raises(AuthorizationError):
        consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert len(transporte.calls) == numero_get
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("antes", [True, False])
def test_organizacao_dos_secrets_revalidada_antes_e_depois_de_cada_get(auth, configuracao, antes):
    """Uma troca do vínculo protegido interrompe a próxima requisição ou a resposta corrente."""
    loads = 0

    def carregar():
        """Muda o tenant na fronteira anterior ou posterior ao segundo GET."""
        nonlocal loads
        loads += 1
        if loads == (4 if antes else 5):
            configuracao["GESTAOCLICK_ORGANIZATION_ID"] = OUTRA_ORGANIZACAO
        return configuracao

    transporte = TransporteFicticio(lojas(), situacoes())
    with pytest.raises(AuthorizationError):
        consultar_conta(auth, carregar, autorizado=True, transporte=transporte)
    assert len(transporte.calls) == (1 if antes else 2)


def test_mudanca_de_credencial_descarta_consulta(auth, configuracao):
    """Uma rotação da configuração não mistura catálogos de credenciais diferentes."""
    transporte = TransporteFicticio(lojas(), depois=lambda n: configuracao.update(GESTAOCLICK_ACCESS_TOKEN="NOVO"))
    with pytest.raises(AuthorizationError):
        consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert len(transporte.calls) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 402, 403, 429, 500])
def test_http_recusado_sem_redirect_retry_ou_exposicao(auth, configuracao, status):
    """Erros e redirecionamentos nunca reutilizam tokens nem expõem resposta ou headers."""
    transporte = TransporteFicticio(RespostaGestaoClick(status, (PRIVADO + SECRET_TOKEN).encode(),
                                                      {"Location": "https://externo.invalid/" + ACCESS_TOKEN}))
    with pytest.raises(GestaoClickError) as error:
        consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert_seguro(error.value)
    assert len(transporte.calls) == 1


@pytest.mark.parametrize("failure", [TimeoutError, OSError, GestaoClickError])
@pytest.mark.parametrize("revogar", [False, True])
def test_falha_de_transporte_sanitizada_revalida_acesso(auth, configuracao, failure, revogar):
    """Falha externa não pula a verificação posterior de tenant ou revela exceção privada."""
    transporte = TransporteFicticio(failure(SECRET_TOKEN + PRIVADO),
                                   depois=lambda n: alterar_acesso(auth, "organization_id") if revogar else None)
    with pytest.raises(AuthorizationError if revogar else GestaoClickError) as error:
        consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert_seguro(error.value)
    assert len(transporte.calls) == 1


@pytest.mark.parametrize("data", [None, {}, [[]], ["texto"], [{}], [{"id": "1", "nome": ""}],
                                  [{"id": True, "nome": "Nome"}], [{"id": "1", "nome": "Nome\n"}],
                                  [{"id": "1", "nome": "Nome"}] * 2,
                                  [{"id": str(i), "nome": "Nome"} for i in range(101)]])
def test_lojas_invalidas_duplicadas_ou_excessivas_abortam(auth, configuracao, data):
    """O catálogo deve ser completo e íntegro; registros não são silenciosamente removidos."""
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, resposta(data))


@pytest.mark.parametrize("changes", [{"code": True}, {"code": "200"}, {"code": 401}, {"status": "error"},
                                     {"meta": {"total_registros": 4}}, {"links": {"next": "privado"}}])
def test_envelope_lojas_e_continuacao_inesperada_recusados(auth, configuracao, changes):
    """HTTP 200 ou continuação implícita não comprovam catálogo não paginado completo."""
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, resposta([], **changes))


@pytest.mark.parametrize("raw", [b'[]', b'{', b'\xff',
                                 b'{"code":200,"code":200,"status":"success","data":[]}',
                                 b'{"code":200,"status":"success","data":[{"id":"1","nome":NaN}]}'])
def test_json_invalido_ou_ambiguo_abortado(auth, configuracao, raw):
    """Reutiliza o parser seguro para duplicatas, Unicode e valores não JSON."""
    with pytest.raises(ContratoInvalido) as error:
        consultar(auth, configuracao, RespostaGestaoClick(200, raw))
    assert_seguro(error.value)


@pytest.mark.parametrize("changes", [
    {"total_registros": True}, {"total_registros": "2"}, {"total_registros": -1},
    {"total_da_pagina": 1}, {"pagina_atual": 2}, {"pagina_atual": True},
    {"limite_por_pagina": 20}, {"limite_por_pagina": 101},
    {"proxima_url": "https://externo.invalid/"}, {"proxima_pagina": "2"},
])
def test_metadados_inconsistentes_nao_comprovam_situacoes(auth, configuracao, changes):
    """A lista pequena também exige total, página e encerramento coerentes."""
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, lojas(), situacoes(**changes))


def test_ausencia_de_meta_ou_proxima_url_nao_infere_fim(auth, configuracao):
    """Nem lista vazia nem metadado parcial substituem prova de completude."""
    for page in (resposta([]), resposta([], meta={"total_registros": 0, "total_da_pagina": 0,
                                               "pagina_atual": 1, "limite_por_pagina": 100})):
        with pytest.raises(ContratoInvalido):
            consultar(auth, configuracao, lojas(), page)


@pytest.mark.parametrize("next_url", [
    None, "", "https://externo.invalid/situacoes_vendas?pagina=2&limite=100",
    BASE + "/situacoes_vendas?pagina=3&limite=100",
    BASE + "/situacoes_vendas?pagina=2&limite=100&loja_id=outro-tenant",
    BASE + "/situacoes_vendas?pagina=2&pagina=2&limite=100",
    BASE + "/situacoes_vendas?pagina=2&limite=100#segredo",
    BASE + "/vendas?pagina=2&limite=100",
])
def test_proxima_url_invalida_nunca_e_seguida(auth, configuracao, next_url):
    """Somente a continuação exata da consulta pode comprovar a próxima página."""
    transporte = TransporteFicticio(lojas(), situacoes(total=101, proxima_url=next_url))
    with pytest.raises(ContratoInvalido):
        consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert len(transporte.calls) == 2


def test_catalogo_acima_budget_aborta_antes_de_pedir_outras_paginas(auth, configuracao):
    """Mais de trezentas situações não produz snapshot parcial nem mais requisições."""
    transporte = TransporteFicticio(lojas(), situacoes(total=301))
    with pytest.raises(ContratoInvalido):
        consultar_conta(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert len(transporte.calls) == 2


@pytest.mark.parametrize("problema", ["repetida", "total", "truncada"])
def test_segunda_pagina_nao_pode_repetir_mudar_total_ou_truncar(auth, configuracao, problema):
    """Nenhuma divergência entre páginas é escondida por deduplicação ou truncamento."""
    page = json.loads(situacoes(2, 101).body)
    if problema == "repetida":
        page["data"][0]["id"] = "0"
    elif problema == "total":
        page["meta"]["total_registros"] = 102
    else:
        page["data"] = []
        page["meta"]["total_da_pagina"] = 0
    with pytest.raises(ContratoInvalido):
        consultar(auth, configuracao, lojas(), situacoes(total=101),
                  RespostaGestaoClick(200, json.dumps(page).encode()))


@pytest.mark.parametrize("campo", ["user_id", "organization_id", "role", "active", "generation",
                                    "permission", "permissions_changed"])
def test_snapshot_nao_pode_ser_exibido_ou_registrado_apos_mudanca_de_acesso(auth, configuracao, campo):
    """Rerun e registro da fonte verificam novamente a identidade que consultou os dados."""
    conta, _ = consultar(auth, configuracao)
    alterar_acesso(auth, campo)
    with pytest.raises(AuthorizationError):
        validar_conta(auth, conta)
    with pytest.raises(AuthorizationError):
        registrar_fonte(auth, conta, "001")
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("idade", [-1, 300, 301])
def test_snapshot_expira_em_cinco_minutos_e_recusa_tempo_anterior(auth, configuracao, monkeypatch, idade):
    """Tempo monotônico anterior à emissão também invalida o vínculo temporário."""
    monkeypatch.setattr("integracoes.account_setup.time.monotonic", lambda: 1000.0)
    conta, _ = consultar(auth, configuracao)
    monkeypatch.setattr("integracoes.account_setup.time.monotonic", lambda: 1000.0 + idade)
    with pytest.raises(AuthorizationError if idade < 0 else ContratoInvalido):
        registrar_fonte(auth, conta, "001")
    auth.rpc.assert_not_called()


def test_snapshot_vigente_ate_instante_anterior_a_expiracao(auth, configuracao, monkeypatch):
    """A validade de cinco minutos é aplicada sem depender do relógio de parede."""
    monkeypatch.setattr("integracoes.account_setup.time.monotonic", lambda: 1000.0)
    conta, _ = consultar(auth, configuracao)
    monkeypatch.setattr("integracoes.account_setup.time.monotonic", lambda: 1299.99)
    assert validar_conta(auth, conta) is conta


@pytest.mark.parametrize("campo", ["_lojas", "_situacoes", "organization_id", "_expira_em", "_access_stamp", "dict"])
def test_catalogo_adulterado_ou_reconstruido_no_cliente_nao_autoriza_fonte(auth, configuracao, campo):
    """Um objeto semelhante ou alterado não pode transformar dados da UI em autorização."""
    conta, _ = consultar(auth, configuracao)
    changes = {"_lojas": (("outra", "Injetada"),), "_situacoes": (), "organization_id": OUTRA_ORGANIZACAO,
               "_expira_em": conta._expira_em + 60, "_access_stamp": ("inventado",)}
    invalida = asdict(conta) if campo == "dict" else replace(conta, **{campo: changes[campo]})
    with pytest.raises(AuthorizationError):
        registrar_fonte(auth, invalida, "001")
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("campo", ["_lojas", "_situacoes", "_access_stamp", "permissions"])
def test_snapshot_recusa_listas_mutaveis_mesmo_com_json_assinado_equivalente(auth, configuracao, campo):
    """JSON não distingue tuplas de listas; a verificação também exige a estrutura imutável."""
    conta, _ = consultar(auth, configuracao)
    if campo == "permissions":
        stamp = list(conta._access_stamp)
        stamp[3] = list(stamp[3])
        invalida = replace(conta, _access_stamp=tuple(stamp))
    elif campo == "_access_stamp":
        invalida = replace(conta, _access_stamp=list(conta._access_stamp))
    else:
        invalida = replace(conta, **{campo: [list(row) for row in getattr(conta, campo)]})
    with pytest.raises(AuthorizationError):
        registrar_fonte(auth, invalida, "001")
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("store_id", ["outra", "1", "001 ", "", None, 1, ["001"]])
def test_fonte_exige_id_exato_da_loja_autorizada(auth, configuracao, store_id):
    """Nomes, IDs normalizados ou lojas não consultadas não autorizam registro interno."""
    conta, _ = consultar(auth, configuracao)
    with pytest.raises(AuthorizationError):
        registrar_fonte(auth, conta, store_id)
    auth.rpc.assert_not_called()


def test_registro_interno_e_idempotente_neutro_e_sem_http(auth, configuracao):
    """A chave estável pertence à RPC do gerente e o nome não copia nome empresarial."""
    conta, transporte = consultar(auth, configuracao)
    for _ in range(2):
        assert registrar_fonte(auth, conta, "001") == FONTE
    first, second = auth.rpc.call_args_list
    assert first == second
    name, payload, permission = first.args
    assert name == "integration_register_source" and permission == "integrations.manage"
    assert payload["p_key"].startswith("gestaoclick-") and len(payload["p_key"]) <= 80
    assert payload["p_name"] == "GestãoClick: loja 001"
    assert payload["p_stores"] == ["001"]
    assert len(transporte.calls) == 2
    assert_seguro(payload)


@pytest.mark.parametrize("result", [None, [], {}, {"id": "inventado"}, {"id": PRIVADO}, {"id": 42}])
def test_rpc_nao_pode_inventar_ou_vazar_identificador_da_fonte(auth, configuracao, result):
    """O retorno precisa conter UUID válido e falha nunca inclui dados da resposta."""
    conta, _ = consultar(auth, configuracao)
    auth.rpc.return_value = result
    with pytest.raises(GestaoClickError) as error:
        registrar_fonte(auth, conta, "001")
    assert_seguro(error.value)


@pytest.mark.parametrize("campo", ["organization_id", "generation", "role", "active", "permission"])
def test_revogacao_durante_rpc_impede_entrega_de_uuid(auth, configuracao, campo):
    """A resposta interna também é vinculada à mesma sessão e ao Master ativo."""
    conta, _ = consultar(auth, configuracao)

    def rpc(*args):
        """Muda a autorização durante a fronteira de persistência interna."""
        alterar_acesso(auth, campo)
        return {"id": FONTE}

    auth.rpc.side_effect = rpc
    with pytest.raises(AuthorizationError):
        registrar_fonte(auth, conta, "001")


def test_falha_rpc_sanitizada(auth, configuracao):
    """Nem uma falha da camada interna deve reapresentar informações privadas."""
    conta, _ = consultar(auth, configuracao)
    auth.rpc.side_effect = ServiceUnavailable(SECRET_TOKEN + PRIVADO)
    with pytest.raises(GestaoClickError) as error:
        registrar_fonte(auth, conta, "001")
    assert_seguro(error.value)
