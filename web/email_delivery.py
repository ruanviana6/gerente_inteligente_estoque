"""Entrega mensal com simulação, fila persistente e autorização revalidada.

O e-mail contém somente aviso de disponibilidade e link autenticado. Os
indicadores permanecem no aplicativo, evitando anexos com permissões antigas.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from web.reports import BLOCOS_PERMISSOES, gerar_relatorio_mensal


class ProvedorRejeitou(Exception):
    """O provedor confirmou rejeição; uma tentativa futura pode ser segura."""


class EntregaIncerta(Exception):
    """A resposta não prova se houve entrega; proíbe repetição automática."""


class RepositorioRelatorios(Protocol):
    """Contrato mínimo de fila; as implementações devem validar concessões no banco."""

    def claim(self, mes: date, simular: bool) -> list[dict]:
        """Reserva trabalhos ainda não concluídos sob lease exclusivo."""
        ...

    def authorize(self, trabalho: dict) -> dict:
        """Confere conta, destinatário, permissões e configuração atuais."""
        ...

    def finish(self, trabalho: dict, status: str, erro: str | None = None) -> None:
        """Conclui somente o lease reservado, sem armazenar conteúdo sensível."""
        ...


class RepositorioRPC:
    """Usa um segredo dedicado e apenas três RPCs; não recebe chave administrativa."""

    def __init__(self, api: Any, segredo_job: str) -> None:
        """Exige segredo aleatório forte, guardado exclusivamente pelo executor."""
        if not isinstance(segredo_job, str) or len(segredo_job) < 32:
            raise ValueError("Configure REPORT_JOB_SECRET com pelo menos 32 caracteres aleatórios.")
        self._api = api
        self._segredo = segredo_job

    def claim(self, mes: date, simular: bool) -> list[dict]:
        """Pede a reserva transacional de tarefas autorizadas para o mês."""
        return self._api.job_rpc("report_job_claim", {
            "p_job_secret": self._segredo, "p_month": mes.isoformat(), "p_dry_run": simular,
        })

    def authorize(self, trabalho: dict) -> dict:
        """Reobtém permissões e dados financeiros já filtrados na fronteira do banco."""
        return self._api.job_rpc("report_job_authorize", {
            "p_job_secret": self._segredo, "p_job_id": trabalho["id"],
            "p_claim_token": trabalho["claim_token"],
        })

    def finish(self, trabalho: dict, status: str, erro: str | None = None) -> None:
        """Registra um código fixo de resultado, nunca uma exceção externa integral."""
        self._api.job_rpc("report_job_finish", {
            "p_job_secret": self._segredo, "p_job_id": trabalho["id"],
            "p_claim_token": trabalho["claim_token"], "p_status": status, "p_error_code": erro,
        })


class _SemRedirecionamento(HTTPRedirectHandler):
    """Evita encaminhar o header de autenticação para um destino inesperado."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Recusa redirecionamentos do endpoint fixo do provedor."""
        return None


def _enviar_http(request: Request) -> dict:
    """Envia somente para o endpoint oficial, com TLS e timeout finito."""
    try:
        with build_opener(_SemRedirecionamento()).open(request, timeout=20) as resposta:
            return json.loads(resposta.read(65536))
    except HTTPError as erro:
        # 408, conflitos e erros de servidor podem ocorrer após aceitação.
        if 400 <= erro.code < 500 and erro.code not in {408, 409}:
            raise ProvedorRejeitou("O provedor recusou a solicitação.") from None
        raise EntregaIncerta("Não foi possível confirmar o resultado da entrega.") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise EntregaIncerta("Não foi possível confirmar o resultado da entrega.") from None


def _email_valido(valor: str) -> bool:
    """Rejeita quebras de linha e campos ambíguos antes de montar o pedido JSON."""
    return bool(re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", valor or ""))


def _url_aplicativo(url: str) -> str:
    """Permite só link HTTPS sem credenciais, tokens de consulta ou fragmento."""
    try:
        partes = urlsplit(url)
        valida = (
            partes.scheme == "https" and partes.hostname and not partes.username
            and not partes.password and not partes.query and not partes.fragment
            and partes.port in {None, 443} and not any(c.isspace() for c in url)
        )
    except ValueError:
        valida = False
    if not valida:
        raise ValueError("Configure REPORT_APP_URL com a URL HTTPS verificada do aplicativo.")
    return url


@dataclass
class ProvedorResend:
    """Adaptador opcional de Resend; a criação não efetua nenhum envio.

    A API documenta idempotência durante 24 horas. Ainda assim, timeouts são
    registrados como incertos e não repetidos automaticamente pelo scheduler.
    Não é criada conta nem escolhido plano; isso depende do proprietário.
    """

    api_key: str = field(repr=False)
    remetente: str
    transport: Callable[[Request], dict] = field(default=_enviar_http, repr=False)

    def __post_init__(self) -> None:
        """Valida apenas configuração estrutural, sem testar credencial ou domínio."""
        if not self.api_key or any(c.isspace() for c in self.api_key):
            raise ValueError("Configure a credencial protegida do provedor de relatórios.")
        if not _email_valido(self.remetente):
            raise ValueError("Configure um endereço de remetente validado no provedor.")

    def enviar(self, destinatario: str, mes: date, app_url: str, chave: str) -> None:
        """Envia aviso sem indicadores ou anexos, identificado por chave idempotente."""
        if not _email_valido(destinatario):
            raise ProvedorRejeitou("O destinatário não possui endereço válido.")
        if not re.fullmatch(r"[A-Za-z0-9_:/.-]{1,256}", chave or ""):
            raise ValueError("A chave de idempotência é inválida.")
        url = _url_aplicativo(app_url)
        conteudo = {
            "from": self.remetente, "to": [destinatario],
            "subject": f"Relatório de estoque disponível: {mes:%m/%Y}",
            "text": (
                f"O relatório de {mes:%m/%Y} está disponível no Gerente Inteligente de Estoque.\n"
                "Entre com sua conta para consultar somente os indicadores autorizados.\n"
                f"{url}\n\nEste aviso não contém valores, dados de produtos ou anexos."
            ),
        }
        pedido = Request(
            "https://api.resend.com/emails", data=json.dumps(conteudo).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json",
                     "Idempotency-Key": chave}, method="POST",
        )
        resposta = self.transport(pedido)
        if not isinstance(resposta, dict) or not isinstance(resposta.get("id"), str):
            raise EntregaIncerta("O provedor não confirmou o identificador da entrega.")


def _permissoes_dos_blocos(permissoes: list[str], blocos: list[str]) -> set[str]:
    """Respeita a seleção do Master sem ampliar concessões do destinatário."""
    permitidas = set(permissoes)
    for bloco, permissao in BLOCOS_PERMISSOES.items():
        if bloco not in blocos:
            permitidas.discard(permissao)
    return permitidas


def executar_relatorios(
    repositorio: RepositorioRelatorios,
    mes: date,
    *,
    simular: bool = True,
    provedor: ProvedorResend | None = None,
    permitir_envio_real: bool = False,
    app_url: str | None = None,
) -> dict[str, int]:
    """Processa um lote reservado com dupla autorização e sem logs de destinatários.

    A fila usa chaves distintas para simulação e envio real, limite de três
    tentativas e leases. Erros ambíguos nunca viram tentativas automáticas.
    A configuração externa e a autorização atual do Master devem permitir
    explicitamente o envio real; a ausência de uma delas mantém o bloqueio.
    """
    if type(simular) is not bool or type(permitir_envio_real) is not bool:
        raise ValueError("As opções de envio devem ser booleanas explícitas.")
    resultado = {"simulados": 0, "enviados": 0, "bloqueados": 0, "falhas": 0, "incertos": 0}
    for trabalho in repositorio.claim(mes, simular):
        status, erro, contador = "falha", "FALHA_LOCAL", "falhas"
        envio_iniciado = False
        try:
            permitidas = _permissoes_dos_blocos(trabalho.get("permissions", []), trabalho.get("blocks", []))
            if not {"reports.receive", "reports.view"}.issubset(permitidas):
                raise PermissionError("Recebimento não autorizado.")
            gerar_relatorio_mensal(trabalho["payload"], mes, permitidas)
            atual = repositorio.authorize(trabalho)
            permitidas = _permissoes_dos_blocos(atual.get("permissions", []), atual.get("blocks", []))
            if (atual.get("allowed") is not True or atual.get("active") is not True
                    or not {"reports.receive", "reports.view"}.issubset(permitidas)):
                raise PermissionError("Recebimento não autorizado.")
            # Uma revogação financeira não pode reutilizar o conteúdo obtido na reserva.
            gerar_relatorio_mensal(atual["payload"], mes, permitidas)
            if simular:
                status, erro, contador = "simulado", None, "simulados"
            elif not permitir_envio_real or atual.get("real_enabled") is not True or provedor is None or not app_url:
                status, erro, contador = "falha", "CONFIGURACAO_AUSENTE", "bloqueados"
            else:
                envio_iniciado = True
                provedor.enviar(atual.get("email", ""), mes, app_url, trabalho["idempotency_key"])
                status, erro, contador = "enviado", None, "enviados"
        except PermissionError:
            status, erro, contador = "falha", "ACESSO_REVOGADO", "bloqueados"
        except (ValueError, KeyError, TypeError):
            status, erro, contador = "falha", "DADOS_INCOMPLETOS", "bloqueados"
        except ProvedorRejeitou:
            status, erro, contador = "falha", "PROVEDOR_REJEITOU", "falhas"
        except EntregaIncerta:
            status, erro, contador = "incerto", "ENTREGA_INCERTA", "incertos"
        except Exception:
            # Exceções externas não são impressas nem persistidas, pois podem conter headers.
            if envio_iniciado:
                status, erro, contador = "incerto", "ENTREGA_INCERTA", "incertos"
        try:
            repositorio.finish(trabalho, status, erro)
        except Exception:
            # O lease real expirado vira incerto no banco e nunca é reenviado sozinho.
            contador = "incertos" if envio_iniciado else "falhas"
        resultado[contador] += 1
    return resultado
