"""Coordena validação, autorização e cálculos sem compartilhar dados entre sessões."""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from functools import wraps
from typing import Any, MutableMapping

import pandas as pd

from dados import (
    COLUNAS_PRODUTOS,
    COLUNAS_SNAPSHOTS,
    COLUNAS_VENDAS,
    carregar_de_csv,
    exportar_csv_seguro,
    gerar_dados_exemplo,
    hoje_local,
    validar_produtos,
    validar_snapshots,
    validar_vendas,
)
from motor import gerar_painel
from web.auth import AccessContext, AuthService
from web.errors import AuthenticationError, AuthorizationError
from web.telemetry import measure, performance_scope

FINANCEIRO = {"custo_unitario", "capital_estoque", "classe_abc", "abc_parcial",
              "financeiro_limite_excedido"}
MODULOS = {"dashboard.view", "inventory.view", "replenishment.view", "trends.view",
           "reports.view", "parameters.edit", "decisions.write"}


def _dia(valor: object) -> date:
    """Exige uma data civil válida, nunca um instante convertido silenciosamente."""
    if not isinstance(valor, (date, str, pd.Timestamp)):
        raise ValueError("Informe uma data civil válida, sem horário ou fuso.")
    if isinstance(valor, str) and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", valor):
        raise ValueError("Informe uma data civil no formato AAAA-MM-DD.")
    data = pd.Timestamp(valor)
    if pd.isna(data) or data.tzinfo is not None or data != data.normalize():
        raise ValueError("Informe uma data civil válida, sem horário ou fuso.")
    return data.date()


def _registros(tabela: pd.DataFrame) -> list[dict[str, Any]]:
    """Converte pandas para JSON padrão, incluindo datas civis e custos nulos."""
    copia = tabela.copy()
    if "data" in copia:
        copia["data"] = pd.to_datetime(copia["data"]).dt.strftime("%Y-%m-%d")
    registros = copia.astype(object).where(pd.notna(copia), None).to_dict(orient="records")
    # to_json do pandas arredonda casas decimais. JSON padrão preserva a precisão
    # do float e rejeita infinito em vez de transformar um custo pequeno em zero.
    return json.loads(json.dumps(registros, allow_nan=False))


def normalizar_payload(payload: dict, financeiro: bool = False) -> dict:
    """Valida o retorno remoto e prepara DataFrames para o motor já auditado.

    A política do banco já impede leitura de custos indevida. A remoção aqui
    acrescenta defesa contra mudanças futuras no transporte ou nos contratos.
    """
    bruto = pd.DataFrame(payload.get("products", []))
    if not financeiro:
        bruto = bruto.drop(columns=list(FINANCEIRO), errors="ignore")
    if bruto.empty:
        produtos = pd.DataFrame(columns=COLUNAS_PRODUTOS)
        vendas = pd.DataFrame(columns=COLUNAS_VENDAS)
        fotos = pd.DataFrame(columns=COLUNAS_SNAPSHOTS)
        painel = pd.DataFrame()
    else:
        produtos = validar_produtos(bruto)
        vendas = validar_vendas(pd.DataFrame(payload.get("sales", []), columns=COLUNAS_VENDAS), produtos)
        # O histórico pode conter SKUs arquivados, ausentes no catálogo deste lote.
        fotos = pd.DataFrame(payload.get("snapshots", []), columns=COLUNAS_SNAPSHOTS)
        fotos = fotos.loc[fotos["codigo"].isin(produtos["codigo"])]
        fotos = validar_snapshots(fotos, produtos)
        referencia = payload.get("metadata", {}).get("snapshot_date") or hoje_local()
        with measure("motor"):
            painel = gerar_painel(produtos, vendas, fotos, hoje=referencia)
    if not financeiro:
        produtos = produtos.drop(columns=list(FINANCEIRO), errors="ignore")
        painel = painel.drop(columns=list(FINANCEIRO), errors="ignore")
    return {"products": produtos, "sales": vendas, "snapshots": fotos, "panel": painel,
            "imports": payload.get("imports", []), "metadata": payload.get("metadata", {})}


def _operation(method: Callable) -> Callable:
    """Impede troca de identidade local entre etapas de uma operação da fachada."""
    @wraps(method)
    def protected(self, *args, **kwargs):
        """Usa o lock exclusivo da sessão, sem bloquear outros navegadores."""
        with self.auth.operation():
            return method(self, *args, **kwargs)
    return protected


class Service:
    """Fachada de uma sessão: cada operação consulta a autorização vigente."""

    def __init__(self, auth: AuthService):
        """Recebe uma instância exclusiva de autenticação, sem cache global."""
        self.auth = auth

    @contextmanager
    def execution(self) -> Iterator[None]:
        """Mantém a identidade estável até terminar o rerun ou fragmento atual.

        O lock abrange consulta, cálculo e apresentação. Sem isso, um login em
        outra thread poderia trocar a sessão entre a resposta RPC e a saída.
        Não se guardam dados nem permissões entre execuções.
        """
        with self.auth.operation(), self.auth.execution():
            yield

    def context(self):
        """Reconsulta identidade, estado da conta e permissões no servidor."""
        return self.auth.context()

    @_operation
    def load_inventory(self, module: str, month: object | None = None) -> dict:
        """Obtém somente o lote permitido e calcula indicadores com o mesmo motor."""
        if module not in MODULOS:
            raise AuthorizationError("Funcionalidade não autorizada.")
        with measure("load_inventory"):
            payload, original = self.auth.rpc_with_context("load_inventory", {
                "p_module": module, "p_month": _dia(month).isoformat() if month is not None else None,
            }, module)
        resultado = normalizar_payload(payload, original.can("finance.view"))
        # A consulta final acontece depois do motor: não entregar dados cuja
        # autorização mudou durante uma operação potencialmente demorada.
        contexto = self.auth.require(module)
        self._same_identity(original, contexto)
        if not contexto.can("finance.view"):
            for name in ("products", "panel"):
                resultado[name] = resultado[name].drop(columns=list(FINANCEIRO), errors="ignore")
        resultado["_access_context"] = contexto
        return resultado

    @_operation
    def _import_frames(self, produtos, vendas, snapshot_date, period_start, period_end):
        """Valida a abrangência declarada antes de enviar um lote transacional."""
        contexto = self.auth.require("inventory.import")
        foto, inicio, fim = map(_dia, (snapshot_date, period_start, period_end))
        if not inicio <= fim <= foto <= hoje_local().date():
            raise ValueError("O período deve terminar até a data do saldo e não pode estar no futuro.")
        if not vendas.empty and (vendas["data"].min().date() < inicio or vendas["data"].max().date() > fim):
            raise ValueError("Há vendas fora da abrangência declarada.")
        if not contexto.can("finance.view") and produtos["custo_unitario"].notna().any():
            raise AuthorizationError("Importar custos exige permissão financeira.")
        return self.auth.rpc("import_inventory", {
            "p_products": _registros(produtos), "p_sales": _registros(vendas),
            "p_snapshot_date": foto.isoformat(), "p_period_start": inicio.isoformat(),
            "p_period_end": fim.isoformat(),
        }, "inventory.import")

    @_operation
    def import_csv(self, products: bytes, sales: bytes, snapshot_date, period_start, period_end,
                   separator: str = ";", decimal: str = ","):
        """Importa CSV completo usando as validações e limites aprovados no PR #4."""
        self.auth.require("inventory.import")
        produtos, vendas = carregar_de_csv(products, sales, separator, decimal)
        return self._import_frames(produtos, vendas, snapshot_date, period_start, period_end)

    @_operation
    def import_demo(self, snapshot_date, period_start=None, period_end=None):
        """Gera um lote fictício apenas para um Master autenticado e autorizado."""
        contexto = self.auth.require("inventory.import")
        if contexto.profile.role != "Master":
            raise AuthorizationError("Somente o Master pode preparar a demonstração.")
        foto = _dia(snapshot_date)
        inicio = _dia(period_start) if period_start else (pd.Timestamp(foto) - pd.Timedelta(days=119)).date()
        fim = _dia(period_end) if period_end else foto
        if inicio < (pd.Timestamp(foto) - pd.Timedelta(days=119)).date():
            raise ValueError("A demonstração fornece no máximo 120 dias de histórico.")
        produtos, vendas, _ = gerar_dados_exemplo(foto)
        vendas = vendas.loc[vendas["data"].between(pd.Timestamp(inicio), pd.Timestamp(fim))]
        return self._import_frames(produtos, vendas, foto, inicio, fim)

    def update_parameters(self, codigo: str, values: dict):
        """Atualiza parâmetros autorizados, mantendo a validação final no banco."""
        return self.auth.rpc("update_parameters", {"p_codigo": codigo, "p_values": values}, "parameters.edit")

    def save_decision(self, codigo: str, acao: str, quantidade: int, observacao: str):
        """Registra uma decisão interna; nenhuma ação é enviada ao ERP."""
        return self.auth.rpc("save_decision", {"p_codigo": codigo, "p_acao": acao,
                             "p_quantidade": quantidade, "p_observacao": observacao}, "decisions.write")

    def list_decisions(self):
        """Lista decisões com escopo definido no banco, sem identificador manipulável."""
        return self.auth.rpc("list_decisions", {}, "decisions.write")

    @_operation
    def export_replenishment(self, filters: dict | None = None) -> bytes:
        """Reconsulta a autorização e os dados antes de gerar um CSV permitido."""
        original = self.auth.require("replenishment.export")
        tabela = self.load_inventory("replenishment.view")["panel"]
        filtros = filters or {}
        if not tabela.empty and filtros.get("somente_sugestao", True):
            tabela = tabela.loc[tabela["quantidade_sugerida"].gt(0)].copy()
        for campo in ("fornecedor", "status"):
            if not tabela.empty and campo in filtros and filtros[campo] is not None:
                valores = filtros[campo] if isinstance(filtros[campo], list) else [filtros[campo]]
                tabela = tabela.loc[tabela[campo].isin(valores)]
        busca = str(filtros.get("busca", "")).strip()
        if busca and not tabela.empty:
            tabela = tabela.loc[tabela["nome"].str.contains(busca, case=False, regex=False)
                                | tabela["codigo"].str.contains(busca, case=False, regex=False)]
        prioridade = filtros.get("prioridade")
        if not tabela.empty:
            if prioridade == "ruptura":
                tabela = tabela.loc[tabela["status"].eq("Ruptura")]
            elif prioridade == "risco":
                tabela = tabela.loc[tabela["risco_prazo"]]
            elif prioridade == "qualidade":
                tabela = tabela.loc[tabela["requer_revisao"]]
        contexto = self.auth.require("replenishment.export")
        self._same_identity(original, contexto)
        if not contexto.can("replenishment.view"):
            raise AuthorizationError("O acesso à reposição foi revogado.")
        if not contexto.can("finance.view"):
            tabela = tabela.drop(columns=list(FINANCEIRO), errors="ignore")
        return exportar_csv_seguro(tabela)

    @_operation
    def monthly_report(self, month):
        """Gera relatório de mês fechado com lotes históricos e permissões atuais."""
        from web.reports import filtrar_relatorio, gerar_relatorio_mensal

        mes = _dia(month).replace(day=1)
        with measure("load_inventory"):
            payload, original = self.auth.rpc_with_context(
                "load_inventory", {"p_module": "reports.view", "p_month": mes.isoformat()}, "reports.view",
            )
        anterior = None
        # Um mês anterior ausente não impede o relatório: apenas a comparação.
        mes_anterior = (pd.Timestamp(mes) - pd.Timedelta(days=1)).date().replace(day=1)
        from web.errors import AppError
        try:
            with measure("load_inventory"):
                anterior = self.auth.rpc("load_inventory", {
                    "p_module": "reports.view", "p_month": mes_anterior.isoformat(),
                }, "reports.view")
        except AppError:
            pass
        contexto = self.auth.require("reports.view")
        with measure("motor"):
            relatorio = gerar_relatorio_mensal(payload, mes, contexto.permissions, anterior=anterior)
        atual = self.auth.require("reports.view")
        self._same_identity(original, atual)
        resultado = filtrar_relatorio(relatorio, atual.permissions)
        resultado["_access_context"] = atual
        return resultado

    @_operation
    def export_monthly_report(self, month):
        """Não exporta conteúdo previamente armazenado sem revalidar permissões."""
        from web.reports import exportar_relatorio_csv

        original = self.auth.require("reports.export")
        relatorio = self.monthly_report(month)
        contexto = self.auth.require("reports.export")
        self._same_identity(original, contexto)
        if not contexto.can("reports.view"):
            raise AuthorizationError("O acesso ao relatório foi revogado.")
        return exportar_relatorio_csv(relatorio, contexto.permissions)

    @staticmethod
    def _same_identity(original: AccessContext, current: AccessContext) -> None:
        """Recusa mudança de organização ou identidade entre leitura e entrega."""
        if (original.profile.user_id != current.profile.user_id
                or original.profile.organization_id != current.profile.organization_id):
            raise AuthorizationError("O acesso mudou durante a consulta. Atualize a página.")

    @staticmethod
    def _access_signature(context: AccessContext) -> tuple:
        """Compara os limites de acesso sem conservar resultados ou credenciais."""
        return (context.profile.user_id, context.profile.organization_id,
                context.profile.role, context.permissions)

    def deferred_export(
        self, kind: str, params: dict | None = None, *, context: AccessContext | None = None,
    ) -> Callable[[], bytes]:
        """Prepara download por clique, vinculado à sessão e às permissões da tela.

        A ação é executada pelo rerun autorizado, sem registrá-la no gerenciador
        global de downloads do Streamlit. O fechamento contém somente filtros e
        vínculo de autorização, nunca tabelas ou bytes prontos. Cada execução
        recalcula e revalida Auth e banco antes e depois da geração.
        """
        permissions = {
            "replenishment": ("replenishment.view", "replenishment.export"),
            "monthly": ("reports.view", "reports.export"),
        }
        if kind not in permissions or (params is not None and not isinstance(params, dict)):
            raise ValueError("Tipo ou parâmetros de exportação inválidos.")
        parameters = deepcopy(params or {})
        allowed = ({"month"} if kind == "monthly" else
                   {"fornecedor", "status", "busca", "somente_sugestao", "prioridade"})
        if parameters.keys() - allowed:
            raise ValueError("Há parâmetros desconhecidos na exportação.")
        if kind == "monthly":
            parameters["month"] = _dia(parameters.get("month")).isoformat()
        with self.auth.operation():
            current = context if context is not None else self.context()
            if self.auth.session is None or self.auth.session.user_id != current.profile.user_id:
                raise AuthenticationError("A sessão mudou. Entre novamente.")
            required = permissions[kind]
            if not all(current.can(permission) for permission in required):
                raise AuthorizationError("Seu usuário não tem permissão para exportar este conteúdo.")
            generation = self.auth.generation
            signature = self._access_signature(current)

        def validate() -> None:
            """Recusa sessão anterior ou alteração de acesso, inclusive acesso financeiro."""
            if self.auth.session is None or self.auth.generation != generation:
                raise AuthenticationError("A sessão mudou. Atualize a página antes de exportar.")
            fresh = self.context()
            if (self.auth.generation != generation or self._access_signature(fresh) != signature
                    or not all(fresh.can(permission) for permission in required)):
                raise AuthorizationError("As permissões mudaram. Atualize a página antes de exportar.")

        def generate() -> bytes:
            """Gera um arquivo autorizado por clique, sem reutilizar bytes anteriores."""
            with self.auth.operation(), performance_scope("export"), measure("export"), self.execution():
                validate()
                content = (self.export_replenishment(parameters) if kind == "replenishment"
                           else self.export_monthly_report(parameters["month"]))
                # Um novo escopo força GET Auth novamente no fim, mesmo que a
                # geração tenha levado tempo ou a conta tenha sido revogada.
                with self.execution():
                    validate()
                return content

        return generate

    def admin_list_users(self):
        """Obtém somente as contas administráveis da organização do Master."""
        return self.auth.rpc("admin_list_users", {}, "users.manage")

    def admin_set_user(self, user_id, display_name, role, active, permissions):
        """Aplica perfil e exceções individuais; o banco bloqueia alteração de Master."""
        return self.auth.rpc("admin_set_user", {"p_user_id": user_id, "p_display_name": display_name,
                             "p_role": role, "p_active": active, "p_permissions": permissions}, "users.manage")

    def admin_audit(self):
        """Lê a trilha de alterações sem payloads de credenciais."""
        return self.auth.rpc("admin_audit", {}, "audit.view")

    def reports_config(self):
        """Lê a configuração do Master e apresenta simulação como padrão."""
        config = self.auth.rpc("report_settings", {}, "reports.manage")
        return {**config, "dry_run": not config.get("real_enabled", False)}

    def save_reports_config(self, enabled, dry_run, recipients, blocks=None, send_day=1):
        """Configura destinatários sem enviar mensagens nem expor a chave do worker."""
        return self.auth.rpc("admin_report_settings", {
            "p_enabled": enabled, "p_real_enabled": not dry_run, "p_recipients": recipients,
            "p_blocks": blocks if blocks is not None else ["estoque", "reposicao", "tendencias"],
            "p_send_day": send_day,
        }, "reports.manage")

    def report_runs(self):
        """Consulta o histórico sanitizado das execuções, sem tokens de processamento."""
        return self.auth.rpc("report_job_history", {}, "reports.manage")


def create_service(state: MutableMapping, settings_mapping: dict | None = None) -> Service:
    """Cria o cliente exclusivo da sessão Streamlit sem armazenar nada em disco."""
    if "_service" in state:
        return state["_service"]
    from web.api import SupabaseAPI
    from web.settings import Settings

    if settings_mapping is None:
        import streamlit as st

        settings_mapping = {nome: os.environ[nome] for nome in (
            "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY",
            "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_BACKEND_SECRET", "AUTH_EMAIL_ENABLED",
        ) if nome in os.environ}
        try:
            settings_mapping.update(dict(st.secrets))
        except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
            pass
    service = Service(AuthService(SupabaseAPI(Settings.from_mapping(settings_mapping))))
    state["_service"] = service
    return service
