"""Executor de uma janela autorizada, sem sessão humana ou escrita no ERP."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import UUID

from integracoes.configuration import contract_fingerprint
from integracoes.gestaoclick import TransporteConsulta, TransporteHTTPS
from integracoes.runtime import GestaoClickReader, build_reader
from sincronizacao.periodos import validar_periodo
from sincronizacao.service import reconcile
from web.settings import Settings

MAX_BATCH_DAYS = 30
MAX_JOB_SECONDS = 180


class SyncJobError(RuntimeError):
    """Interrompe o job com mensagem fixa, sem dados da origem ou credenciais."""


def _uuid(value: Any) -> str:
    """Exige identificador textual canônico antes de comparar escopos protegidos."""
    if not isinstance(value, str):
        raise ValueError("Identificador da sincronização inválido.")
    try:
        result = str(UUID(value))
    except ValueError:
        raise ValueError("Identificador da sincronização inválido.") from None
    if result != value:
        raise ValueError("Identificador da sincronização inválido.")
    return result


@dataclass(frozen=True, repr=False)
class JobConfiguration:
    """Agrupa configuração validada sem deixar segredos aparecerem no repr."""

    settings: Settings
    reader: GestaoClickReader
    secret: str
    contract_hash: str
    organization_id: str


def validar_configuracao(values: Mapping[str, Any], transport: TransporteConsulta | None = None) -> JobConfiguration:
    """Confere configuração completa sem rede, sem autenticar ou reservar trabalho."""
    if not isinstance(values, Mapping):
        raise ValueError("Configuração protegida da sincronização inválida.")
    allowed = {"SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SYNC_JOB_SECRET"}
    if any(not isinstance(key, str) or key not in allowed and not key.startswith("GESTAOCLICK_") for key in values):
        raise ValueError("Use somente a configuração dedicada da sincronização, sem chaves administrativas.")
    secret = values.get("SYNC_JOB_SECRET")
    if not isinstance(secret, str) or not re.fullmatch(r"[a-f0-9]{64}", secret):
        raise ValueError("O segredo dedicado da sincronização é inválido.")
    settings = Settings(supabase_url=values.get("SUPABASE_URL", ""),
                        publishable_key=values.get("SUPABASE_PUBLISHABLE_KEY", ""), timeout_seconds=10)
    organization_id = _uuid(values.get("GESTAOCLICK_ORGANIZATION_ID"))
    reader = build_reader(values, transport)
    _uuid(reader.config.source_id)
    # Limites menores por execução. Falha conserva o checkpoint e aguarda outro
    # ciclo, sem repetir a carga completa nem aguardar retries longos no mesmo job.
    reader = replace(reader, config=replace(reader.config, timeout_seconds=10, interval_seconds=1,
                     max_attempts=1, max_pages=100, max_records=10000, max_requests=100))
    fingerprint = contract_fingerprint(values)
    if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise ValueError("A impressão do contrato é inválida.")
    return JobConfiguration(settings, reader, secret, fingerprint, organization_id)


def _validar_trabalho(raw: Any, config: JobConfiguration) -> dict:
    """Confere janela, loja, organização e contrato antes de consultar o ERP."""
    if not isinstance(raw, dict):
        raise SyncJobError("Reserva de sincronização inválida.")
    job = dict(raw)
    for key in ("automation_id", "claim", "source_id", "organization_id", "actor_id", "request_key"):
        _uuid(job.get(key))
    if (job["source_id"] != config.reader.config.source_id
            or job["organization_id"] != config.organization_id
            or job.get("store_id") != config.reader.config.store_id
            or job.get("contract_hash") != config.contract_hash):
        raise SyncJobError("O escopo autorizado não corresponde à configuração protegida.")
    first, last = validar_periodo(job.get("start"), job.get("end"))
    if (last - first).days + 1 > MAX_BATCH_DAYS:
        raise SyncJobError("A janela reservada excede trinta dias.")
    job["start"], job["end"] = first.isoformat(), last.isoformat()
    return job


def _validar_autorizacao(response: Any, job: dict) -> None:
    """Não aceita sucesso genérico nem alteração de identidade durante o job."""
    if (not isinstance(response, dict) or response.get("allowed") is not True
            or any(response.get(key) != job[key] for key in
                   ("organization_id", "actor_id", "source_id", "store_id", "contract_hash"))):
        raise SyncJobError("A autorização atual da sincronização foi recusada.")


def _validar_resultado(result: Any, job: dict, *, completed: bool, count: int | None = None,
                       quantity: Decimal | None = None, control: dict | None = None) -> dict:
    """Confere a reconciliação devolvida pelo banco antes de avançar o checkpoint."""
    expected = "completed" if completed else "previewed"
    if (not isinstance(result, dict) or result.get("status") != expected
            or result.get("period_start") != job["start"] or result.get("period_end") != job["end"]):
        raise SyncJobError("O resultado da sincronização não foi confirmado.")
    _uuid(result.get("id"))
    _uuid(result.get("claim_token"))
    if control is not None and (result["id"] != control["p_run_id"] or result["claim_token"] != control["p_claim_token"]):
        raise SyncJobError("O banco retornou outro lote durante a sincronização.")
    if count is not None and (type(result.get("row_count")) is not int or result["row_count"] != count):
        raise SyncJobError("A contagem reconciliada não foi confirmada.")
    if quantity is not None and Decimal(str(result.get("net_quantity"))) != quantity:
        raise SyncJobError("A quantidade reconciliada não foi confirmada.")
    if completed and result.get("coverage_confirmed") is not False:
        raise SyncJobError("O job não pode declarar conferência humana de abrangência.")
    return result


class _TransporteComPrazo:
    """Limita o GET ao orçamento restante sem introduzir qualquer método de escrita."""

    def __init__(self, delegate: TransporteConsulta | None, clock: Callable[[], float], deadline: float):
        """Guarda o transporte sem criar conexão até uma leitura autorizada."""
        self.delegate, self.clock, self.deadline = delegate, clock, deadline

    def get(self, url: str, headers: Mapping[str, str], timeout: float):
        """Recusa novo GET quando o orçamento termina, preservando o checkpoint."""
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise SyncJobError("O orçamento de execução terminou.")
        if self.delegate is None:
            self.delegate = TransporteHTTPS()
        return self.delegate.get(url, headers, min(timeout, remaining))


def executar_sincronizacao(api: Any, config: JobConfiguration, *, clock: Callable[[], float] = time.monotonic) -> dict:
    """Processa no máximo uma janela e retorna somente códigos técnicos.

    Cada etapa é autorizada pelo segredo delegado no banco. A configuração do
    Master e o contrato devem continuar vigentes antes do GET e do commit.
    Falhas nunca imprimem exceções externas nem promovem dados parciais; finish
    é o único passo que avança o checkpoint após commit comprovado. A retomada
    de um commit já concluído não consulta novamente o ERP.
    """
    deadline = clock() + MAX_JOB_SECONDS
    claim_token = None

    def step(job: dict, action: str, payload: dict | None = None):
        """Encaminha somente ações fechadas e respeita o prazo antes de cada RPC."""
        if clock() >= deadline:
            raise SyncJobError("O orçamento de execução terminou.")
        return api.sync_job_rpc("sync_job_step", {"p_secret": config.secret, "p_claim": job["claim"],
                                "p_action": action, "p_payload": payload or {}})

    def authorize(job: dict) -> None:
        """Revalida autorização e vínculo, sem solicitar uma sessão humana."""
        _validar_autorizacao(step(job, "authorize", {"p_source_id": job["source_id"],
                            "p_store_id": job["store_id"]}), job)

    def finish(job: dict) -> None:
        """Só considera o checkpoint avançado após confirmação explícita do banco."""
        result = step(job, "finish")
        if not isinstance(result, dict) or result.get("status") != "completed" or result.get("finished") is not True:
            raise SyncJobError("A conclusão do checkpoint não foi confirmada.")

    try:
        raw = api.sync_job_rpc("sync_job_claim", {"p_secret": config.secret, "p_contract_hash": config.contract_hash})
        if raw is None:
            return {"status": "not_due"}
        # Só usar um claim estruturalmente válido para registrar falha. Nunca
        # encaminhar identificadores arbitrários de uma resposta incompleta.
        if isinstance(raw, dict):
            claim_token = _uuid(raw.get("claim"))
        job = _validar_trabalho(raw, config)
        authorize(job)
        if job.get("completed_run") is not None:
            _validar_resultado(job["completed_run"], job, completed=True)
            finish(job)
            return {"status": "completed", "resumed": True}
        reader = replace(config.reader, _transport=_TransporteComPrazo(config.reader._transport, clock, deadline))
        rows, quantity = reconcile(reader.read(job["start"], job["end"]), job["source_id"],
                                   job["store_id"], job["start"], job["end"])
        rows.sort(key=lambda row: (row["document_id"], row["line_id"]))
        content_hash = sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
        run = step(job, "begin", {"p_source_id": job["source_id"], "p_store_id": job["store_id"],
                   "p_request_key": job["request_key"], "p_period_start": job["start"], "p_period_end": job["end"],
                   "p_expected_rows": len(rows), "p_expected_quantity": str(quantity), "p_content_hash": content_hash})
        if not isinstance(run, dict):
            raise SyncJobError("O banco não confirmou a reserva do lote.")
        if run.get("status") == "completed":
            _validar_resultado(run, job, completed=True, count=len(rows), quantity=quantity)
            finish(job)
            return {"status": "completed", "resumed": True}
        if run.get("status") not in {"staging", "previewed"}:
            raise SyncJobError("O estado do lote não permite preparar a sincronização.")
        control = {"p_run_id": _uuid(run.get("id")), "p_claim_token": _uuid(run.get("claim_token"))}
        for offset in range(0, len(rows), 100):
            step(job, "stage", {**control, "p_page": offset // 100 + 1, "p_rows": rows[offset:offset + 100]})
        preview = step(job, "preview", control)
        _validar_resultado(preview, job, completed=False, count=len(rows), quantity=quantity, control=control)
        authorize(job)
        completed = step(job, "commit", {**control, "p_coverage_confirmed": False})
        _validar_resultado(completed, job, completed=True, count=len(rows), quantity=quantity, control=control)
        finish(job)
        return {"status": "completed", "resumed": False}
    except Exception:
        # Mensagem externa pode conter tokens, PII ou parâmetros. Nada disso é
        # impresso, retornado ou enviado ao banco. Falha ao registrar fail mantém
        # o lease até expirar; a mesma janela será retomada sem avanço antecipado.
        if claim_token:
            try:
                api.sync_job_rpc("sync_job_step", {"p_secret": config.secret, "p_claim": claim_token,
                                 "p_action": "fail", "p_payload": {}})
            except Exception:
                pass
        return {"status": "failed", "error_code": "SYNC_FAILED"}
