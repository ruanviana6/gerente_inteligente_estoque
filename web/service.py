"""Coordena validação, autorização e cálculos sem compartilhar dados entre sessões."""
from __future__ import annotations

import json
import os
import re
from datetime import date
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
from web.auth import AuthService
from web.errors import AuthorizationError

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
        painel = gerar_painel(produtos, vendas, fotos, hoje=referencia)
    if not financeiro:
        produtos = produtos.drop(columns=list(FINANCEIRO), errors="ignore")
        painel = painel.drop(columns=list(FINANCEIRO), errors="ignore")
    return {"products": produtos, "sales": vendas, "snapshots": fotos, "panel": painel,
            "imports": payload.get("imports", []), "metadata": payload.get("metadata", {})}


class Service:
    """Fachada de uma sessão: cada operação consulta a autorização vigente."""

    def __init__(self, auth: AuthService):
        """Recebe uma instância exclusiva de autenticação, sem cache global."""
        self.auth = auth

    def context(self):
        """Reconsulta identidade, estado da conta e permissões no servidor."""
        return self.auth.context()

    def load_inventory(self, module: str, month: object | None = None) -> dict:
        """Obtém somente o lote permitido e calcula indicadores com o mesmo motor."""
        if module not in MODULOS:
            raise AuthorizationError("Funcionalidade não autorizada.")
        payload = self.auth.rpc("load_inventory", {
            "p_module": module, "p_month": _dia(month).isoformat() if month is not None else None,
        }, module)
        return normalizar_payload(payload, self.context().can("finance.view"))

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

    def import_csv(self, products: bytes, sales: bytes, snapshot_date, period_start, period_end,
                   separator: str = ";", decimal: str = ","):
        """Importa CSV completo usando as validações e limites aprovados no PR #4."""
        self.auth.require("inventory.import")
        produtos, vendas = carregar_de_csv(products, sales, separator, decimal)
        return self._import_frames(produtos, vendas, snapshot_date, period_start, period_end)

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

    def export_replenishment(self, filters: dict | None = None) -> bytes:
        """Reconsulta a autorização e os dados antes de gerar um CSV permitido."""
        self.auth.require("replenishment.export")
        tabela = self.load_inventory("replenishment.view")["panel"]
        if tabela.empty:
            return exportar_csv_seguro(tabela)
        filtros = filters or {}
        if filtros.get("somente_sugestao", True):
            tabela = tabela.loc[tabela["quantidade_sugerida"].gt(0)].copy()
        for campo in ("fornecedor", "status"):
            if campo in filtros and filtros[campo] is not None:
                valores = filtros[campo] if isinstance(filtros[campo], list) else [filtros[campo]]
                tabela = tabela.loc[tabela[campo].isin(valores)]
        busca = str(filtros.get("busca", "")).strip()
        if busca:
            tabela = tabela.loc[tabela["nome"].str.contains(busca, case=False, regex=False)
                                | tabela["codigo"].str.contains(busca, case=False, regex=False)]
        self.auth.require("replenishment.export")
        if not self.context().can("finance.view"):
            tabela = tabela.drop(columns=list(FINANCEIRO), errors="ignore")
        return exportar_csv_seguro(tabela)

    def monthly_report(self, month):
        """Gera relatório de mês fechado com lotes históricos e permissões atuais."""
        from web.reports import gerar_relatorio_mensal

        mes = _dia(month).replace(day=1)
        payload = self.auth.rpc("load_inventory", {"p_module": "reports.view", "p_month": mes.isoformat()},
                                "reports.view")
        anterior = None
        # Um mês anterior ausente não impede o relatório: apenas a comparação.
        mes_anterior = (pd.Timestamp(mes) - pd.Timedelta(days=1)).date().replace(day=1)
        from web.errors import AppError
        try:
            anterior = self.auth.rpc("load_inventory", {
                "p_module": "reports.view", "p_month": mes_anterior.isoformat(),
            }, "reports.view")
        except AppError:
            pass
        contexto = self.auth.require("reports.view")
        return gerar_relatorio_mensal(payload, mes, contexto.permissions, anterior=anterior)

    def export_monthly_report(self, month):
        """Não exporta conteúdo previamente armazenado sem revalidar permissões."""
        from web.reports import exportar_relatorio_csv

        self.auth.require("reports.export")
        relatorio = self.monthly_report(month)
        contexto = self.auth.require("reports.export")
        if not contexto.can("reports.view"):
            raise AuthorizationError("O acesso ao relatório foi revogado.")
        return exportar_relatorio_csv(relatorio, contexto.permissions)

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
