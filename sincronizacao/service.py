"""Sincronização manual de cópia analítica, sem movimentar o estoque do ERP.

A leitura integral e a normalização são injetadas. Não há transporte HTTP nem
credenciais neste módulo; uma resposta parcial nunca é promovida ao analítico.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Protocol
from uuid import UUID

from web.errors import AuthorizationError

MAX_ROWS = 20000


class RPCAuth(Protocol):
    """Contrato mínimo da sessão individual; nunca aceita service_role."""

    @property
    def generation(self) -> int:
        """Identifica a vida da sessão para invalidar operações de um login anterior."""

    def require(self, permission: str) -> Any:
        """Consulta identidade e autorização vigentes no servidor."""

    def rpc(self, name: str, payload: dict, permission: str) -> Any:
        """Executa RPC autorizada com JWT individual."""

    def operation(self) -> Any:
        """Serializa os passos da operação nesta sessão."""


def _identity(context: Any) -> tuple[str, str]:
    """Compara UUIDs protegidos, nunca nomes de usuário ou metadados editáveis."""
    profile = context.profile
    if profile.role != 'Master' or not context.can('integrations.manage'):
        raise AuthorizationError('Somente o Master pode sincronizar dados.')
    return profile.user_id, profile.organization_id


def _day(value: str | date) -> str:
    """Recusa horário e datas ambíguas no contrato de sincronização."""
    if type(value) is date:
        return value.isoformat()
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Informe uma data ISO sem horário.')
    return value


def _uuid(value: str) -> str:
    """Normaliza identificador imutável sem aceitar escopo implícito."""
    return str(UUID(str(value)))


def reconcile(rows: Iterable[Mapping], source_id: str, store_id: str, start: str, end: str) -> tuple[list[dict], Decimal]:
    """Valida reconciliação estrutural antes de persistir a primeira página.

    A contagem da API pode contar documentos; esta soma trabalha com as linhas
    normalizadas. Ela não prova que a conta ERP expôs todos os documentos.
    """
    normalized: list[dict] = []
    seen: set[tuple[str, str]] = set()
    net = Decimal(0)
    allowed = {'source_id', 'store_id', 'document_id', 'line_id', 'product_id', 'sku',
               'product_name', 'category', 'supplier', 'sale_date', 'status', 'quantity',
               'returned_quantity', 'source_updated_at'}
    statuses = {'potential', 'separating', 'completed', 'cancelled', 'returned', 'unknown'}
    for raw in rows:
        if len(normalized) >= MAX_ROWS:
            raise ValueError('A execução aceita até vinte mil linhas. Divida o período.')
        row = dict(raw)
        if set(row) - allowed or row.get('source_id') != source_id or row.get('store_id') != store_id:
            raise ValueError('Campo inesperado ou escopo da origem divergente.')
        for key in ('document_id', 'line_id', 'product_id', 'sku'):
            if not isinstance(row.get(key), str) or not 1 <= len(row[key].strip()) <= 120:
                raise ValueError('Identificador de origem inválido.')
        day = _day(row.get('sale_date'))
        if not start <= day <= end or row.get('status') not in statuses:
            raise ValueError('Data fora do período ou status não reconhecido.')
        key = row['document_id'], row['line_id']
        if key in seen:
            raise ValueError('Documento e linha repetidos na extração.')
        seen.add(key)
        try:
            if any(isinstance(row.get(k), bool) for k in ('quantity', 'returned_quantity')):
                raise ValueError
            quantity, returned = (Decimal(str(row[k])) for k in ('quantity', 'returned_quantity'))
            if not quantity.is_finite() or not returned.is_finite() or not 0 <= returned <= quantity <= Decimal('1e9'):
                raise ValueError
            if max(-quantity.as_tuple().exponent, -returned.as_tuple().exponent) > 6:
                raise ValueError
        except (InvalidOperation, KeyError, ValueError, TypeError):
            raise ValueError('Quantidades inválidas ou devolução não informada.') from None
        if row['status'] == 'completed':
            net += quantity - returned
        normalized.append(row)
    return normalized, net


class SyncCoordinator:
    """Coordena staging, pré-visualização e confirmação explícita pelo Master."""

    def __init__(self, auth: RPCAuth) -> None:
        """Recebe a sessão exclusiva; nenhuma autorização é guardada entre ações."""
        self.auth = auth

    def _stamp(self) -> tuple:
        """Vincula a operação à mesma identidade, organização e vida da sessão."""
        return (*_identity(self.auth.require('integrations.manage')), self.auth.generation)

    def _call(self, name: str, payload: dict) -> Any:
        """Verifica identidade antes e depois, além da autorização em PostgreSQL."""
        before = self._stamp()
        result = self.auth.rpc(name, payload, 'integrations.manage')
        if before != self._stamp():
            raise AuthorizationError('A identidade ou organização mudou durante a operação.')
        return result

    def prepare(self, *, source_id: str, store_id: str, request_key: str, start: str | date, end: str | date,
                read_all: Callable[[], Iterable[Mapping]]) -> dict:
        """Extrai tudo antes de gravar e persiste checkpoints de páginas de cem.

        Repetir request_key retoma a execução, revalidando todo o conteúdo. Só
        sync_commit torna os dados visíveis. Cancelamentos nunca removem vendas.
        """
        with self.auth.operation():
            before = self._stamp()
            source_id, request_key = _uuid(source_id), _uuid(request_key)
            start, end = _day(start), _day(end)
            if start > end or not isinstance(store_id, str) or not 1 <= len(store_id.strip()) <= 120:
                raise ValueError('Escopo ou período inválido.')
            self._call('sync_authorize_source', {'p_source_id': source_id, 'p_store_id': store_id})
            rows, quantity = reconcile(read_all(), source_id, store_id, start, end)
            rows.sort(key=lambda row: (row['document_id'], row['line_id']))
            # Vínculo de idempotência do cliente confiável. Não prova abrangência
            # comercial, nem substitui a reconciliação e validação no PostgreSQL.
            content_hash = sha256(json.dumps(rows, sort_keys=True, separators=(',', ':'),
                ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
            if before != self._stamp():
                raise AuthorizationError('A autorização mudou durante a extração.')
            run = self._call('sync_begin', {'p_source_id': source_id, 'p_store_id': store_id,
                'p_request_key': request_key, 'p_period_start': start, 'p_period_end': end,
                'p_expected_rows': len(rows), 'p_expected_quantity': str(quantity), 'p_content_hash': content_hash})
            if run['status'] == 'completed':
                return run
            control = {'p_run_id': run['id'], 'p_claim_token': run['claim_token']}
            # Reenvio de páginas já confirmadas valida seu hash e não duplica itens.
            for offset in range(0, len(rows), 100):
                self._call('sync_stage', {**control, 'p_page': offset // 100 + 1, 'p_rows': rows[offset:offset + 100]})
            return self._call('sync_preview', control)

    def commit(self, run_id: str, claim_token: str, *, coverage_confirmed: bool = False) -> dict:
        """Confirma a prévia; cobertura é uma conferência humana, não inferência."""
        with self.auth.operation():
            if type(coverage_confirmed) is not bool:
                raise ValueError('A confirmação de abrangência deve ser explícita.')
            return self._call('sync_commit', {'p_run_id': _uuid(run_id), 'p_claim_token': _uuid(claim_token),
                'p_coverage_confirmed': coverage_confirmed})

    def cancel(self, run_id: str, claim_token: str | None) -> dict:
        """Descarta somente staging; o analítico consolidado permanece preservado."""
        with self.auth.operation():
            return self._call('sync_cancel', {'p_run_id': _uuid(run_id),
                'p_claim_token': None if claim_token is None else _uuid(claim_token)})
