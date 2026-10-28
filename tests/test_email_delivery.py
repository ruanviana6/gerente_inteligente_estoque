"""Exercita a rotina de e-mail sem abrir rede nem usar destinatários reais."""

import json
from copy import deepcopy
from datetime import date

import pytest

from web.email_delivery import (
    EntregaIncerta,
    ProvedorRejeitou,
    ProvedorResend,
    RepositorioRPC,
    executar_relatorios,
)

MES = date(2026, 9, 1)


def fechamento():
    """Representa o fechamento fictício mínimo que o worker deve calcular."""
    return {
        "products": [{"codigo": "FICTICIO", "nome": "Produto fictício", "estoque": 10, "custo_unitario": 57}],
        "sales": [{"codigo": "FICTICIO", "data": "2026-09-15", "quantidade": 30}],
        "snapshots": [{"codigo": "FICTICIO", "data": "2026-09-30", "estoque": 10}],
        "metadata": {
            "month_complete": True, "snapshot_date": "2026-09-30",
            "period_start": "2026-07-01", "period_end": "2026-09-30", "batch_id": "lote-ficticio",
        },
    }


class FilaFicticia:
    """Dublê do contrato de lease; a política SQL é testada separadamente no PostgreSQL."""

    def __init__(self):
        """Cria uma reserva e uma autorização independentes para simular revogação."""
        self.trabalho = {
            "id": "job-ficticio", "claim_token": "claim-ficticio", "idempotency_key": "relatorio/2026-09/ficticio",
            "permissions": ["reports.view", "reports.receive", "inventory.view", "finance.view"],
            "blocks": ["estoque", "financeiro"], "payload": fechamento(),
        }
        self.atual = {
            "allowed": True, "active": True, "permissions": self.trabalho["permissions"].copy(),
            "payload": fechamento(), "blocks": self.trabalho["blocks"].copy(),
            "email": "destinatario@example.invalid", "real_enabled": True,
        }
        self.reservado = False
        self.finalizado = False
        self.resultados = []
        self.consultas_autorizacao = 0

    def claim(self, mes, simular):
        """Entrega cada reserva uma vez, como exige a fronteira transacional."""
        if self.reservado or self.finalizado:
            return []
        self.reservado = True
        return [deepcopy(self.trabalho)]

    def authorize(self, trabalho):
        """Permite retirar acesso depois que o relatório inicial foi gerado."""
        self.consultas_autorizacao += 1
        return deepcopy(self.atual)

    def finish(self, trabalho, status, erro=None):
        """Armazena só códigos seguros, nunca objetos de exceção ou conteúdo."""
        self.finalizado = True
        self.resultados.append((status, erro))


def provedor_ficticio(respostas):
    """Retorna adaptador real com transporte inteiramente em memória."""
    def transportar(pedido):
        """Registra o pedido para inspeção, sem realizar conexão externa."""
        respostas.append(pedido)
        return {"id": "entrega-ficticia"}

    return ProvedorResend("chave-ficticia-de-teste", "remetente@example.invalid", transportar)


def test_simulacao_e_padrao_e_nao_dispara_provedor():
    """Um worker configurado continua sem enviar enquanto simular for padrão."""
    fila, pedidos = FilaFicticia(), []
    resultado = executar_relatorios(fila, MES, provedor=provedor_ficticio(pedidos))
    assert resultado["simulados"] == 1
    assert pedidos == []
    assert fila.consultas_autorizacao == 1
    assert fila.resultados == [("simulado", None)]


def test_reexecucao_nao_repete_reserva_concluida():
    """O scheduler respeita o contrato idempotente da fila entre execuções."""
    fila = FilaFicticia()
    assert executar_relatorios(fila, MES)["simulados"] == 1
    assert sum(executar_relatorios(fila, MES).values()) == 0
    assert len(fila.resultados) == 1


@pytest.mark.parametrize("campo,valor", [
    ("allowed", False), ("active", False),
    ("permissions", ["reports.view"]), ("permissions", ["reports.receive"]),
])
def test_revogacao_entre_geracao_e_entrega_impede_envio(campo, valor):
    """A concessão obtida ao reservar não substitui a autorização atual."""
    fila, pedidos = FilaFicticia(), []
    fila.atual[campo] = valor
    resultado = executar_relatorios(
        fila, MES, simular=False, permitir_envio_real=True,
        provedor=provedor_ficticio(pedidos), app_url="https://exemplo.streamlit.app",
    )
    assert resultado["bloqueados"] == 1
    assert pedidos == []
    assert fila.resultados == [("falha", "ACESSO_REVOGADO")]


@pytest.mark.parametrize("permitir,global_real,tem_provedor,url", [
    (False, True, True, "https://exemplo.streamlit.app"),
    (True, False, True, "https://exemplo.streamlit.app"),
    (True, True, False, "https://exemplo.streamlit.app"),
    (True, True, True, None),
])
def test_envio_real_exige_todas_as_configuracoes(permitir, global_real, tem_provedor, url):
    """Nenhuma credencial isolada habilita um envio externo."""
    fila, pedidos = FilaFicticia(), []
    fila.atual["real_enabled"] = global_real
    resultado = executar_relatorios(
        fila, MES, simular=False, permitir_envio_real=permitir,
        provedor=provedor_ficticio(pedidos) if tem_provedor else None, app_url=url,
    )
    assert resultado["bloqueados"] == 1
    assert pedidos == []
    assert fila.resultados == [("falha", "CONFIGURACAO_AUSENTE")]


def test_fechamento_incompleto_nao_chega_ao_envio():
    """A fila não autoriza inventar um relatório com estoque atual ou mês parcial."""
    fila = FilaFicticia()
    fila.trabalho["payload"]["metadata"]["month_complete"] = False
    resultado = executar_relatorios(fila, MES)
    assert resultado["bloqueados"] == 1
    assert fila.consultas_autorizacao == 0
    assert fila.resultados == [("falha", "DADOS_INCOMPLETOS")]


def test_financeiro_revogado_e_gerado_novamente_sem_custo():
    """A segunda leitura é usada mesmo se o primeiro relatório tinha financeiro."""
    fila, pedidos = FilaFicticia(), []
    fila.atual["permissions"].remove("finance.view")
    fila.atual["payload"]["products"][0]["custo_unitario"] = "VALOR_RESTRITO"
    resultado = executar_relatorios(
        fila, MES, simular=False, permitir_envio_real=True,
        provedor=provedor_ficticio(pedidos), app_url="https://exemplo.streamlit.app",
    )
    assert resultado["enviados"] == 1
    corpo = json.loads(pedidos[0].data)
    assert "VALOR_RESTRITO" not in str(corpo)
    assert "FICTICIO" not in str(corpo)
    assert "attachments" not in corpo
    assert "https://exemplo.streamlit.app" in corpo["text"]
    assert pedidos[0].get_header("Idempotency-key") == fila.trabalho["idempotency_key"]


def test_master_pode_desabilitar_bloco_financeiro_sem_revogar_permissao():
    """A seleção de conteúdo do Master é uma restrição adicional."""
    fila = FilaFicticia()
    fila.trabalho["blocks"] = []
    fila.atual["blocks"] = []
    fila.trabalho["payload"]["products"][0]["custo_unitario"] = "RESTRITO"
    fila.atual["payload"]["products"][0]["custo_unitario"] = "RESTRITO"
    assert executar_relatorios(fila, MES)["simulados"] == 1


@pytest.mark.parametrize("excecao,status,contador,codigo", [
    (ProvedorRejeitou("corpo com segredo"), "falha", "falhas", "PROVEDOR_REJEITOU"),
    (EntregaIncerta("header com segredo"), "incerto", "incertos", "ENTREGA_INCERTA"),
    (RuntimeError("token privado"), "incerto", "incertos", "ENTREGA_INCERTA"),
])
def test_falhas_de_provedor_nao_persistem_segredos(excecao, status, contador, codigo):
    """Resultado ambíguo não é convertido em retry seguro nem erro técnico público."""
    fila = FilaFicticia()

    def falhar(pedido):
        """Simula resposta externa com conteúdo que nunca pode chegar ao log."""
        raise excecao

    provedor = ProvedorResend("chave-ficticia", "remetente@example.invalid", falhar)
    resultado = executar_relatorios(
        fila, MES, simular=False, permitir_envio_real=True,
        provedor=provedor, app_url="https://exemplo.streamlit.app",
    )
    assert resultado[contador] == 1
    assert fila.resultados == [(status, codigo)]
    assert "segredo" not in str(fila.resultados)
    assert "token" not in str(fila.resultados)


def test_confirmacao_banco_indisponivel_depois_envio_vira_incerta():
    """Uma falha ao confirmar não comprova ausência de entrega e não permite repetir."""
    fila, pedidos = FilaFicticia(), []

    def falhar(*args):
        """Representa perda de resposta do banco depois da aceitação pelo provedor."""
        raise RuntimeError("Detalhe secreto")

    fila.finish = falhar
    resultado = executar_relatorios(
        fila, MES, simular=False, permitir_envio_real=True,
        provedor=provedor_ficticio(pedidos), app_url="https://exemplo.streamlit.app",
    )
    assert resultado["incertos"] == 1
    assert len(pedidos) == 1


@pytest.mark.parametrize("url", [
    "http://exemplo.streamlit.app", "https://usuario:senha@exemplo.streamlit.app",
    "https://exemplo.streamlit.app?token=secreto", "https://exemplo.streamlit.app#token",
])
def test_link_de_notificacao_nao_aceita_tokens_ou_http(url):
    """O e-mail aponta apenas para a entrada autenticada e não distribui sessões."""
    pedidos = []
    with pytest.raises(ValueError):
        provedor_ficticio(pedidos).enviar("destinatario@example.invalid", MES, url, "chave-ficticia")
    assert pedidos == []


def test_provedor_oculta_credencial_na_representacao():
    """Um repr acidental não deve conter a chave usada pelo transporte."""
    provedor = ProvedorResend("valor-confidencial-ficticio", "remetente@example.invalid")
    assert "valor-confidencial-ficticio" not in repr(provedor)


def test_resposta_sem_id_e_incerta():
    """HTTP recebido sem confirmação reconhecida não autoriza um segundo envio."""
    provedor = ProvedorResend("chave-ficticia", "remetente@example.invalid", lambda pedido: {})
    with pytest.raises(EntregaIncerta):
        provedor.enviar("destinatario@example.invalid", MES, "https://exemplo.streamlit.app", "chave-ficticia")


def test_repositorio_limita_operacoes_ao_contrato_de_job():
    """O executor não requer chave administrativa nem endpoint de consulta genérico."""
    chamadas = []

    class API:
        """Transporta RPCs apenas em memória para verificar os parâmetros."""

        def job_rpc(self, nome, payload):
            """Guarda o pedido sem expô-lo ao stdout."""
            chamadas.append((nome, payload))
            return [] if nome == "report_job_claim" else {}

    fila = RepositorioRPC(API(), "x" * 32)
    fila.claim(MES, True)
    fila.authorize({"id": "job", "claim_token": "claim"})
    fila.finish({"id": "job", "claim_token": "claim"}, "simulado")
    assert [nome for nome, _ in chamadas] == ["report_job_claim", "report_job_authorize", "report_job_finish"]
    assert all(payload["p_job_secret"] == "x" * 32 for _, payload in chamadas)
    assert chamadas[0][1]["p_month"] == "2026-09-01"


def test_job_recusa_segredo_curto():
    """Sem segredo forte, o worker falha antes de qualquer chamada."""
    with pytest.raises(ValueError):
        RepositorioRPC(None, "curto")
