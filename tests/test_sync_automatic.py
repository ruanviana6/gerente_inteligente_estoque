"""Execução agendada com contratos, banco e transporte totalmente fictícios."""

import json
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from integracoes.gestaoclick import RespostaGestaoClick
from scripts.run_sync import main
from sincronizacao.automatic import executar_sincronizacao, validar_configuracao
from web.api import HTTPResponse, SupabaseAPI
from web.errors import AppError, AuthenticationError, AuthorizationError
from web.settings import PROJECT_URL, Settings

SOURCE = "30000000-0000-4000-8000-000000000001"
ORGANIZATION = "10000000-0000-4000-8000-000000000001"
ACTOR = "20000000-0000-4000-8000-000000000001"
REQUEST = "40000000-0000-4000-8000-000000000001"
RUN = "50000000-0000-4000-8000-000000000001"
TOKEN = "60000000-0000-4000-8000-000000000001"
AUTOMATION = "70000000-0000-4000-8000-000000000001"
CLAIM = "80000000-0000-4000-8000-000000000001"
SECRET = "a" * 64


def configuration_values():
    """Fornece contrato integral fictício, sem presumir campos da empresa."""
    names = ("document_id", "line_id", "product_id", "sku", "sale_date", "status", "quantity", "returned_quantity", "store_id")
    return {
        "SUPABASE_URL": PROJECT_URL, "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_chave-ficticia",
        "SYNC_JOB_SECRET": SECRET, "GESTAOCLICK_ENABLED": True,
        "GESTAOCLICK_BASE_URL": "https://api.gestaoclick.com",
        "GESTAOCLICK_ACCESS_TOKEN": "token-ficticio-de-teste",
        "GESTAOCLICK_SECRET_ACCESS_TOKEN": "segredo-api-ficticio-de-teste",
        "GESTAOCLICK_ORGANIZATION_ID": ORGANIZATION,
        "GESTAOCLICK_SOURCE_ID": SOURCE, "GESTAOCLICK_STORE_ID": "loja-ficticia",
        "GESTAOCLICK_READ_CONTRACT_JSON": json.dumps({
            "version": 1, "base_url": "https://api.gestaoclick.com",
            "documentation_urls": ["https://api.gestaoclick.com/referencia-ficticia"],
            "resources": {"vendas": {"method": "GET", "filters": ["pagina", "limite", "loja_id", "inicio", "fim"]}},
        }),
        "GESTAOCLICK_FIELD_MAP_JSON": json.dumps({"mode": "lines", "fields": {key: [key] for key in names}}),
        "GESTAOCLICK_STATUS_MAP_JSON": '{"concluida_ficticia":"completed"}',
        "GESTAOCLICK_PERIOD_FILTERS_JSON": '{"start":"inicio","end":"fim"}',
    }


def raw_line(**changes):
    """Produz uma linha sem nomes de clientes, valores financeiros ou dados reais."""
    return {"document_id": "documento-ficticio", "line_id": "linha-1", "product_id": "produto-1", "sku": "SKU-1",
            "sale_date": "2020-01-02", "status": "concluida_ficticia", "quantity": 4, "returned_quantity": 1,
            "store_id": "loja-ficticia", **changes}


class FakeERP:
    """Possui somente GET e captura chamadas para verificar consumo e bloqueios."""

    def __init__(self, rows=None):
        """Guarda apenas linhas e erros deliberadamente fictícios."""
        self.rows = [raw_line()] if rows is None else rows
        self.calls = []
        self.after = None
        self.fail = False

    def get(self, url, headers, timeout):
        """Retorna um envelope completo ou uma falha simulada, sem rede."""
        self.calls.append((url, timeout))
        if self.after:
            self.after()
        if self.fail:
            raise RuntimeError("token-ficticio-de-teste " + SECRET + " dados-comerciais-protegidos")
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query))
        page = int(query["pagina"])
        pages = max(1, (len(self.rows) + 99) // 100)
        next_url = urlunsplit(parsed._replace(query=urlencode({**query, "pagina": str(page + 1)}))) if page < pages else None
        return RespostaGestaoClick(200, json.dumps({"code": 200, "status": "success",
            "data": self.rows[(page - 1) * 100:page * 100],
            "meta": {"pagina_atual": page, "total_paginas": pages, "total_registros": len(self.rows), "proxima_url": next_url}}).encode())


class FakeAPI:
    """Simula a fronteira delegada; testes SQL cobrem autorização efetiva do banco."""

    def __init__(self, config):
        """Cria uma reserva vinculada ao mesmo escopo e contrato da configuração."""
        self.calls = []
        self.job = {"automation_id": AUTOMATION, "claim": CLAIM, "source_id": SOURCE, "store_id": "loja-ficticia",
                    "organization_id": ORGANIZATION, "actor_id": ACTOR, "contract_hash": config.contract_hash,
                    "start": "2020-01-01", "end": "2020-01-30", "request_key": REQUEST}
        self.row_count = 0
        self.quantity = "0"
        self.revoke = False
        self.authorizations = 0
        self.preview_override = {}
        self.fail_action = None

    def run_result(self, status):
        """Devolve estado reconciliado sem inserir dados na aplicação real."""
        return {"id": RUN, "claim_token": TOKEN, "status": status, "period_start": self.job["start"],
                "period_end": self.job["end"], "row_count": self.row_count, "net_quantity": self.quantity,
                "coverage_confirmed": False}

    def sync_job_rpc(self, name, payload):
        """Registra exatamente as ações permitidas e simula revogação entre etapas."""
        self.calls.append((name, deepcopy(payload)))
        assert payload["p_secret"] == SECRET
        if name == "sync_job_claim":
            return deepcopy(self.job)
        assert name == "sync_job_step"
        assert payload["p_claim"] == CLAIM
        action = payload["p_action"]
        if self.fail_action == action:
            raise RuntimeError("Falha externa protegida " + SECRET)
        if action == "authorize":
            self.authorizations += 1
            return {"allowed": not self.revoke or self.authorizations == 1,
                    **{key: self.job[key] for key in ("organization_id", "actor_id", "source_id", "store_id", "contract_hash")}}
        if action == "begin":
            self.row_count = payload["p_payload"]["p_expected_rows"]
            self.quantity = payload["p_payload"]["p_expected_quantity"]
            return self.run_result("staging")
        if action == "preview":
            return {**self.run_result("previewed"), **self.preview_override}
        if action == "commit":
            assert payload["p_payload"]["p_coverage_confirmed"] is False
            return self.run_result("completed")
        return {"status": "completed" if action == "finish" else "ok", "finished": action == "finish"}


def setup_job(rows=None):
    """Monta os dois transportes fictícios sem abrir sockets."""
    erp = FakeERP(rows)
    config = validar_configuracao(configuration_values(), erp)
    return config, FakeAPI(config), erp


def actions(api):
    """Extrai somente nomes de operações, sem imprimir payloads protegidos."""
    return [payload["p_action"] for name, payload in api.calls if name == "sync_job_step"]


def test_job_completo_reconcilia_reautoriza_e_preserva_cobertura_humana():
    """Uma execução faz uma janela GET, sem usuário Master ou cobertura inventada."""
    config, api, erp = setup_job()
    assert executar_sincronizacao(api, config) == {"status": "completed", "resumed": False}
    assert actions(api) == ["authorize", "begin", "stage", "preview", "authorize", "commit", "finish"]
    assert len(erp.calls) == 1
    assert "loja_id=loja-ficticia" in erp.calls[0][0]
    assert config.settings.service_role_key is config.settings.backend_secret is None


def test_sem_trabalho_nao_consulta_erp_nem_persiste():
    """Próximo horário ainda não atingido não gera consumo de API nem loop."""
    config, api, erp = setup_job()
    api.job = None
    assert executar_sincronizacao(api, config) == {"status": "not_due"}
    assert actions(api) == []
    assert erp.calls == []


@pytest.mark.parametrize("field,value", [
    ("source_id", "30000000-0000-4000-8000-000000000099"),
    ("organization_id", "10000000-0000-4000-8000-000000000099"),
    ("actor_id", "identidade-invalida"), ("request_key", "chave-invalida"),
    ("store_id", "outra-loja"), ("contract_hash", "a" * 64),
    ("end", "2020-01-31"), ("end", "2019-12-31"),
    ("start", "2999-01-01"), ("end", "2999-01-01"),
])
def test_reserva_invalida_bloqueada_antes_get(field, value):
    """Escopo, prazo ou contrato divergente nunca usam as chaves da origem."""
    config, api, erp = setup_job()
    api.job[field] = value
    assert executar_sincronizacao(api, config)["status"] == "failed"
    assert erp.calls == []
    assert actions(api) == ["fail"]


def test_commit_ja_concluido_retomado_sem_nova_leitura():
    """Falha entre commit e finish não duplica registros nem requisita o ERP novamente."""
    config, api, erp = setup_job()
    api.job["completed_run"] = api.run_result("completed")
    assert executar_sincronizacao(api, config) == {"status": "completed", "resumed": True}
    assert erp.calls == []
    assert actions(api) == ["authorize", "finish"]


def test_revogacao_antes_commit_impede_promocao_e_checkpoint():
    """Um Master revogado durante a leitura não promove staging para o analítico."""
    config, api, erp = setup_job()
    api.revoke = True
    assert executar_sincronizacao(api, config)["status"] == "failed"
    assert len(erp.calls) == 1
    assert "commit" not in actions(api)
    assert "finish" not in actions(api)


@pytest.mark.parametrize("rows", [
    [raw_line(), raw_line()], [raw_line(sale_date="2019-12-31")], [raw_line(returned_quantity=5)],
])
def test_dados_invalidos_nao_iniciam_persistencia(rows):
    """Duplicação, filtro ignorado e devolução inválida não criam um lote parcial."""
    config, api, _ = setup_job(rows)
    assert executar_sincronizacao(api, config)["status"] == "failed"
    assert actions(api) == ["authorize", "fail"]


def test_paginas_estaveis_em_ordem_diferente():
    """Conteúdo normalizado determina hash e páginas, independente da ordem da origem."""
    rows = [raw_line(line_id=str(index)) for index in range(201)]
    config, first, _ = setup_job(rows)
    assert executar_sincronizacao(first, config)["status"] == "completed"
    config, second, _ = setup_job(list(reversed(rows)))
    assert executar_sincronizacao(second, config)["status"] == "completed"
    assert first.calls == second.calls
    stages = [payload["p_payload"] for _, payload in first.calls if payload.get("p_action") == "stage"]
    assert [len(stage["p_rows"]) for stage in stages] == [100, 100, 1]


@pytest.mark.parametrize("override", [
    {"row_count": 99}, {"net_quantity": "99"}, {"status": "staging"},
    {"id": "50000000-0000-4000-8000-000000000099"},
    {"claim_token": "60000000-0000-4000-8000-000000000099"},
])
def test_previa_nao_reconciliada_nao_confirma(override):
    """Confirmação depende da reconciliação devolvida pelo banco, não só do HTTP200."""
    config, api, _ = setup_job()
    api.preview_override = override
    assert executar_sincronizacao(api, config)["status"] == "failed"
    assert "commit" not in actions(api)
    assert "finish" not in actions(api)


@pytest.mark.parametrize("action", ["stage", "commit", "finish"])
def test_falhas_nao_sao_anunciadas_como_sucesso(action):
    """Uma resposta perdida conserva o resultado como falha recuperável do job."""
    config, api, _ = setup_job()
    api.fail_action = action
    assert executar_sincronizacao(api, config) == {"status": "failed", "error_code": "SYNC_FAILED"}
    assert actions(api)[-1] == "fail"


def test_erro_externo_nao_expoe_segredos_ou_conteudo(capsys):
    """A exceção deliberadamente sensível não aparece no resultado nem em logs."""
    config, api, erp = setup_job()
    erp.fail = True
    result = executar_sincronizacao(api, config)
    assert result == {"status": "failed", "error_code": "SYNC_FAILED"}
    assert capsys.readouterr().out == ""
    assert "segredo" not in repr(config)
    assert api.calls[-1][1]["p_payload"] == {}


def test_orcamento_expirado_nao_inicia_gravacao():
    """Leitura lenta para ao atingir o orçamento sem avançar o histórico."""
    config, api, erp = setup_job()
    elapsed = [0]
    erp.after = lambda: elapsed.__setitem__(0, 181)
    assert executar_sincronizacao(api, config, clock=lambda: elapsed[0])["status"] == "failed"
    assert actions(api) == ["authorize", "fail"]


@pytest.mark.parametrize("response", [None, {"allowed": False}, {"allowed": True}])
def test_autorizacao_incompleta_bloqueia_get(response):
    """O segredo do worker não substitui uma concessão válida reobtida do banco."""
    config, api, erp = setup_job()
    original = api.sync_job_rpc
    def rpc(name, payload):
        """Simula recusa ou identidade incompleta antes da leitura real."""
        if payload.get("p_action") == "authorize":
            return response
        return original(name, payload)
    api.sync_job_rpc = rpc
    assert executar_sincronizacao(api, config)["status"] == "failed"
    assert erp.calls == []


def test_finish_sem_confirmacao_nao_e_anunciado_como_sucesso():
    """HTTP bem-sucedido sem confirmação do checkpoint continua sendo incerto."""
    config, api, _ = setup_job()
    original = api.sync_job_rpc
    def rpc(name, payload):
        """Retorna corpo vazio apenas ao concluir o checkpoint."""
        result = original(name, payload)
        return None if payload.get("p_action") == "finish" else result
    api.sync_job_rpc = rpc
    assert executar_sincronizacao(api, config)["status"] == "failed"


def test_cli_run_reserva_no_maximo_um_job(monkeypatch, capsys):
    """--run dispara uma execução; o script não faz polling nem loop de carga."""
    monkeypatch.setenv("SYNC_JOB_CONFIG_JSON", json.dumps(configuration_values()))
    with patch("scripts.run_sync.SupabaseAPI") as api:
        with patch("scripts.run_sync.executar_sincronizacao", return_value={"status": "not_due"}) as execute:
            assert main(["--run"]) == 0
            api.assert_called_once()
            execute.assert_called_once()
    assert json.loads(capsys.readouterr().out) == {"status": "not_due"}


@pytest.mark.parametrize("field,value", [
    ("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_nao-permitido"),
    ("SUPABASE_BACKEND_SECRET", "segredo-backend-nao-permitido"),
    ("SUPABASE_PUBLISHABLE_KEY", "sb_secret_nao-permitido"),
    ("GESTAOCLICK_SOURCE_ID", "fonte-sem-uuid"),
    ("GESTAOCLICK_ORGANIZATION_ID", "organizacao-sem-uuid"),
    ("SYNC_JOB_SECRET", "curto"), ("SYNC_JOB_SECRET", "segredo com espaços" * 3),
    ("SYNC_JOB_SECRET", "f" * 32), ("SYNC_JOB_SECRET", "z" * 64),
])
def test_configuracao_nao_admite_chave_administrativa_nem_escopo_invalido(field, value):
    """A configuração de job fica separada das credenciais administrativas do app."""
    with pytest.raises((ValueError, AppError)):
        validar_configuracao({**configuration_values(), field: value}, FakeERP())


@pytest.mark.parametrize("arguments", [[], ["--check"]])
def test_cli_padrao_valida_sem_criar_cliente_ou_fazer_rede(monkeypatch, capsys, arguments):
    """Nem o modo padrão nem --check reservam trabalho ou consultam a origem."""
    monkeypatch.setenv("SYNC_JOB_CONFIG_JSON", json.dumps(configuration_values()))
    with patch("scripts.run_sync.SupabaseAPI", side_effect=AssertionError("Cliente indevido")):
        with patch("urllib.request.build_opener", side_effect=AssertionError("Rede indevida")):
            assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "configuration_valid", "network_accessed": False}


def test_cli_json_invalido_falha_sem_imprimir_conteudo(monkeypatch, capsys):
    """O processo não imprime JSON protegido mesmo quando a configuração é inválida."""
    monkeypatch.setenv("SYNC_JOB_CONFIG_JSON", '{"segredo":"' + SECRET)
    assert main(["--run"]) == 2
    assert SECRET not in capsys.readouterr().out


def test_cliente_supabase_job_nao_envia_jwt_nem_admin():
    """O canal delegado usa só publishable e segredo no corpo, nunca bearer Master."""
    calls = []
    def transport(method, url, headers, body, timeout):
        """Captura envelope HTTPS em memória sem autenticar em serviço real."""
        calls.append((method, url, headers, json.loads(body)))
        return HTTPResponse(200, b'null')
    api = SupabaseAPI(Settings(PROJECT_URL, "sb_publishable_ficticia"), transport)
    assert api.sync_job_rpc("sync_job_claim", {"p_secret": SECRET, "p_contract_hash": "a" * 64}) is None
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/rest/v1/rpc/sync_job_claim")
    assert "Authorization" not in calls[0][2]
    assert calls[0][2]["apikey"].startswith("sb_publishable_")
    with pytest.raises(AuthenticationError):
        api.rpc("sync_begin", {}, None)
    with pytest.raises(AuthorizationError):
        api.sync_job_rpc("load_inventory", {"p_secret": SECRET})
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"p_action": "emitir_nfe"}, {"p_action": []}, {"p_secret": "curto"}, {"p_secret": "z" * 64},
    {"p_backend_secret": "nao-permitido"},
    {"p_payload": "nao-objeto"}, {"p_payload": {"valor": float("nan")}},
    {"p_payload": {"valor": "x" * (2 * 1024 * 1024)}},
])
def test_canal_job_rejeita_payload_indevido_antes_http(changes):
    """Operação, forma, JSON e tamanho ficam limitados também no cliente."""
    def transport(*args):
        """Falha se um envelope inválido atravessar a fronteira."""
        raise AssertionError("HTTP indevido")
    api = SupabaseAPI(Settings(PROJECT_URL, "sb_publishable_ficticia"), transport)
    with pytest.raises(AppError):
        api.sync_job_rpc("sync_job_step", {"p_secret": SECRET, "p_claim": CLAIM,
                         "p_action": "authorize", "p_payload": {}, **changes})
