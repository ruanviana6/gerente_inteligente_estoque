"""Dados e transporte fictícios exclusivos dos testes da interface Flatter.

Nada neste módulo é importado pelo aplicativo produtivo. As respostas percorrem
AuthService, SupabaseAPI e Service reais, mas nenhuma requisição sai do processo.
"""
from __future__ import annotations

import calendar
import copy
import json
import time
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlsplit
from uuid import UUID


def make_payload(size: int = 100, month: str = "2026-09-01") -> dict:
    """Gera catálogo determinístico, vendas e snapshots com fechamento verificável."""
    if not 1 <= size <= 10000:
        raise ValueError("A amostra fictícia deve conter de 1 a 10 mil produtos.")
    inicio_mes = date.fromisoformat(month).replace(day=1)
    fechamento = inicio_mes.replace(day=calendar.monthrange(inicio_mes.year, inicio_mes.month)[1])
    inicio = fechamento - timedelta(days=89)
    produtos, vendas, fotos = [], [], []
    for indice in range(size):
        codigo = f"FT-{indice + 1:05d}"
        saldo = 0 if indice % 13 == 0 else 2 if indice % 7 == 0 else 20 + indice % 180
        produtos.append({
            "codigo": codigo, "nome": f"Produto fictício {indice + 1:04d}",
            "fornecedor": f"Fornecedor fictício {indice % 8 + 1}",
            "estoque": saldo, "custo_unitario": None if indice % 19 == 0 else 5.5 + indice % 70,
            "prazo_entrega_dias": 3 + indice % 15, "estoque_seguranca": indice % 6,
            "pedidos_pendentes": 5 if indice % 11 == 0 else 0,
            "reservas": 2 if indice % 17 == 0 else 0, "cobertura_alvo_dias": 30,
        })
        # Produtos sem venda e histórico parcial exercitam os avisos do motor.
        if indice % 23 != 0:
            for dias in range(0, 90, 7):
                vendas.append({"codigo": codigo, "data": (fechamento - timedelta(days=dias)).isoformat(),
                               "quantidade": 1 + (indice + dias) % 12})
        for dias in (0, 7, 30, 60, 89):
            fotos.append({"codigo": codigo, "data": (fechamento - timedelta(days=dias)).isoformat(),
                          "estoque": saldo if dias == 0 else saldo + 10})
    meta = {
        "batch_id": str(UUID(int=501)), "snapshot_date": fechamento.isoformat(),
        "period_start": inicio.isoformat(), "period_end": fechamento.isoformat(),
        "created_at": fechamento.isoformat() + "T18:00:00Z", "source": "Amostra fictícia Flatter",
        "month_complete": True,
    }
    return {"products": produtos, "sales": vendas, "snapshots": fotos, "metadata": meta,
            "imports": [{"id": meta["batch_id"], **meta, "products_count": size, "sales_count": len(vendas)}]}


class Scenario:
    """Mantém uma identidade e dados controlados, sem segredo ou acesso externo."""

    def __init__(self, size: int = 100, role: str = "Master", latency_ms: float = 0,
                 authenticated: bool = True, user_id: str | None = None):
        """Prepara um perfil real do contrato e uma latência simulada identificada."""
        from web.auth import PERMISSIONS

        if role not in {"Master", "Administrador", "Operador", "Visualizador"}:
            raise ValueError("Perfil fictício desconhecido.")
        if not 0 <= latency_ms <= 2000:
            raise ValueError("Latência simulada fora do limite de teste.")
        self.size = size
        self.role = role
        self.latency_ms = latency_ms
        self.authenticated = authenticated
        self.active = True
        self.user_id = user_id or str(UUID(int={"Master": 101, "Administrador": 102,
                                                "Operador": 103, "Visualizador": 104}[role]))
        self.organization_id = str(UUID(int=201))
        operador = {"dashboard.view", "inventory.view", "replenishment.view", "trends.view",
                    "decisions.write", "reports.view"}
        self.permissions = set(PERMISSIONS) if role == "Master" else (
            set(PERMISSIONS) - {"users.manage", "permissions.manage", "reports.manage", "integrations.manage"}
            if role == "Administrador" else operador if role == "Operador" else {"dashboard.view"}
        )
        self.calls: Counter[str] = Counter()
        self.http_seconds: Counter[str] = Counter()
        self._payloads = {"2026-09-01": make_payload(size)}
        self.decisions: list[dict] = []
        self.users = [{"user_id": str(UUID(int=103)), "display_name": "Operador fictício",
                       "role": "Operador", "active": True, "permissions": {}}]

    def reset_counts(self) -> None:
        """Reinicia apenas os contadores sanitizados, mantendo a mesma amostra."""
        self.calls.clear()
        self.http_seconds.clear()

    def _context(self) -> dict:
        """Produz a resposta de app_context sem credenciais ou e-mail."""
        return {"profile": {"user_id": self.user_id, "organization_id": self.organization_id,
                            "display_name": f"{self.role} fictício", "role": self.role, "active": self.active},
                "permissions": sorted(self.permissions)}

    def transport(self, method, url, headers, payload, timeout):
        """Substitui somente a rede, exercitando autenticação e cálculos reais."""
        from web.api import HTTPResponse
        from web.settings import PROJECT_URL

        if not url.startswith(PROJECT_URL + "/"):
            raise AssertionError("O transporte fictício recusou origem inesperada.")
        caminho = urlsplit(url).path
        if caminho == "/auth/v1/user":
            chave = "auth_user"
        elif caminho.startswith("/rest/v1/rpc/"):
            chave = "rpc_" + caminho.rsplit("/", 1)[-1]
        else:
            chave = "auth_other"
        inicio = time.perf_counter()
        self.calls[chave] += 1
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        parametros = json.loads(payload) if payload else {}
        status, resultado = 200, {}
        if caminho == "/auth/v1/token":
            self.authenticated = True
            resultado = {"access_token": "token_ficticio_de_teste", "refresh_token": "refresh_ficticio_de_teste",
                         "expires_at": time.time() + 86400, "user": {"id": self.user_id, "is_anonymous": False}}
        elif caminho == "/auth/v1/logout":
            self.authenticated = False
        elif not self.authenticated or not self.active:
            status = 403
        elif caminho == "/auth/v1/user":
            resultado = {"id": self.user_id, "is_anonymous": False}
        elif chave == "rpc_app_context":
            resultado = self._context()
        elif chave == "rpc_load_inventory":
            if parametros.get("p_module") not in self.permissions:
                status = 403
            else:
                mes = parametros.get("p_month") or "2026-09-01"
                if mes not in self._payloads:
                    self._payloads[mes] = make_payload(self.size, mes)
                resultado = copy.deepcopy(self._payloads[mes])
                if "finance.view" not in self.permissions:
                    for produto in resultado["products"]:
                        produto.pop("custo_unitario", None)
        elif chave == "rpc_admin_list_users":
            resultado = copy.deepcopy(self.users) if "users.manage" in self.permissions else []
        elif chave in {"rpc_report_job_history", "rpc_admin_audit"}:
            resultado = []
        elif chave == "rpc_report_settings":
            resultado = {"enabled": False, "real_enabled": False, "recipients": [], "send_day": 1,
                         "blocks": ["estoque", "reposicao", "tendencias"]}
        elif chave == "rpc_list_decisions":
            resultado = copy.deepcopy(self.decisions)
        elif chave in {"rpc_update_parameters", "rpc_save_decision", "rpc_admin_set_user",
                        "rpc_admin_report_settings", "rpc_import_inventory"}:
            resultado = {"updated": True}
        else:
            raise AssertionError("Operação não prevista pelo transporte de teste: " + chave)
        resposta = HTTPResponse(status, json.dumps(resultado, allow_nan=False).encode())
        self.http_seconds[chave] += time.perf_counter() - inicio
        return resposta

    def service_factory(self, state):
        """Cria uma fachada exclusiva no estado do teste, sem bypass no app produtivo."""
        from web.api import SupabaseAPI
        from web.auth import AuthService, Session
        from web.service import Service
        from web.settings import PROJECT_URL, Settings

        if "_flatter_test_service" not in state:
            auth = AuthService(SupabaseAPI(Settings(
                PROJECT_URL, "sb_publishable_exclusivamente_ficticia",
                backend_secret="backend_exclusivamente_ficticio_para_teste",
            ), self.transport))
            if self.authenticated:
                auth._session = Session("token_ficticio_de_teste", "refresh_ficticio_de_teste",
                                        time.time() + 86400, self.user_id)
            state["_flatter_test_service"] = Service(auth)
        return state["_flatter_test_service"]
