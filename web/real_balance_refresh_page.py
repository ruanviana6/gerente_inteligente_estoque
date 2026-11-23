"""Saldo pontual da loja por GET explícito do catálogo integral GestãoClick.

Não modifica o ERP, não grava fotografia e não transforma o saldo consultado
em disponibilidade garantida. A confirmação humana segue obrigatória.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import streamlit as st

from integracoes.account_setup import consultar_conta, validar_conta
from integracoes.catalog_full import CatalogoCompleto, consultar_catalogo_completo
from integracoes.gestaoclick import ContratoInvalido, GestaoClickError
from reposicao.planejamento import LIMITE_UNIDADES
from web.errors import AppError, AuthenticationError, AuthorizationError
from web.integration_page import _secrets
from web.sales_service import access_stamp

_CACHE = "_reposicao_saldo_get_consultado"
_TTL = 300


@dataclass(frozen=True)
class SaldoERP:
    """Saldo retornado pela API, restrito a uma loja e um produto nomeado."""

    loja_id: str
    produto_id: str
    nome: str
    saldo: Decimal
    consultado_em: datetime


def extrair_saldo_erp(
    catalogo: CatalogoCompleto, loja_id: str, produto_id: str,
    nome_esperado: str, codigo_esperado: str | None,
) -> Decimal:
    """Rejeita troca de loja, identidade divergente e saldo não íntegro."""
    if (not isinstance(catalogo, CatalogoCompleto)
            or catalogo.loja_solicitada != loja_id
            or len(catalogo.produtos) != catalogo.total_informado
            or not isinstance(produto_id, str) or not produto_id.isascii()
            or not produto_id.isdigit()
            or not isinstance(nome_esperado, str) or not nome_esperado.strip()):
        raise ContratoInvalido("Consulta do saldo não corresponde à loja e ao catálogo selecionados.")
    produtos = [p for p in catalogo.produtos if p.id == produto_id]
    if len(produtos) != 1:
        raise ContratoInvalido("O produto não foi encontrado de forma única na loja consultada.")
    produto = produtos[0]
    if produto.nome is None or produto.nome != nome_esperado:
        raise ContratoInvalido(
            "O nome do produto mudou desde a fotografia. Atualize o catálogo antes de simular."
        )
    if (codigo_esperado is not None and codigo_esperado != "Não informado"
            and produto.codigo_interno != codigo_esperado):
        raise ContratoInvalido(
            "O código do produto diverge da fotografia. Confira o cadastro no GestãoClick."
        )
    if produto.variacoes:
        raise ContratoInvalido(
            "Produto com variações exige conferência da unidade/variação antes de copiar o saldo."
        )
    saldo = produto.estoque
    if (not isinstance(saldo, Decimal) or not saldo.is_finite()
            or saldo < 0 or saldo > LIMITE_UNIDADES):
        raise ContratoInvalido("O saldo da API não é uma quantidade utilizável em unidades.")
    return saldo


def consultar_saldo_erp(
    auth, loja_id: str, produto_id: str, nome_esperado: str,
    codigo_esperado: str | None, *, autorizado: bool,
    carregar_configuracao=_secrets,
) -> SaldoERP:
    """Consulta catálogo via GET apenas após consentimento Master explícito."""
    if autorizado is not True:
        raise AuthorizationError("Autorize a consulta pontual de saldo antes de continuar.")
    with auth.operation():
        acesso = auth.require("integrations.manage")
        if acesso.profile.role != "Master":
            raise AuthorizationError("A consulta de saldo real exige perfil Master.")
        stamp = access_stamp(acesso, auth.generation)
        conta = consultar_conta(auth, carregar_configuracao, autorizado=True)
        if loja_id not in {loja["id"] for loja in conta.lojas}:
            raise AuthorizationError("A loja selecionada não pertence à conta consultada.")
        catalogo = consultar_catalogo_completo(
            auth, carregar_configuracao, conta, loja_id, autorizado=True,
        )
        validar_conta(auth, conta)
        if stamp != access_stamp(auth.require("integrations.manage"), auth.generation):
            raise AuthorizationError("A autorização mudou durante a consulta do saldo.")
        saldo = extrair_saldo_erp(
            catalogo, loja_id, produto_id, nome_esperado, codigo_esperado,
        )
        return SaldoERP(loja_id, produto_id, nome_esperado, saldo,
                        datetime.now(timezone.utc))


def mostrar_saldo_erp_reposicao(servico, loja_id: str, produto_id: str, row: dict) -> str:
    """Exibe GET por escolha humana e permite copiar somente saldo inteiro recente.

    A referência do widget é específica do produto/loja para evitar carregar o
    saldo digitado de outro item. Um saldo copiado por API expira em cinco minutos.
    """
    auth = servico.auth
    chave_campo = f"reposicao_saldo_atual_{loja_id}_{produto_id}"
    chave_origem = f"_reposicao_origem_get_{loja_id}_{produto_id}"
    atual = st.session_state.get(_CACHE)
    acesso = auth.require("integrations.manage")
    stamp = access_stamp(acesso, auth.generation)
    if atual and (
        atual.get("loja") != loja_id or atual.get("produto") != produto_id
        or atual.get("stamp") != stamp or time.monotonic() >= atual.get("expira", 0)
    ):
        st.session_state.pop(_CACHE, None)
        atual = None
    origem = st.session_state.get(chave_origem)
    if origem and (origem["stamp"] != stamp or time.monotonic() >= origem["expira"]):
        st.session_state.pop(chave_origem, None)
        st.session_state.pop(chave_campo, None)
    with st.container(border=True):
        st.markdown("**Consultar saldo do produto diretamente no GestãoClick**")
        st.caption(
            "Consulta manual somente por GET, conferindo todas as páginas da loja. "
            "Não altera estoque nem salva fotografia. Pode levar alguns segundos e "
            "consome chamadas da API. O saldo é válido apenas no momento da leitura."
        )
        with st.form(f"reposicao_get_saldo_{loja_id}_{produto_id}", clear_on_submit=True):
            permitido = st.checkbox(
                "Autorizo a consulta completa do catálogo desta loja somente para "
                "conferir o saldo deste produto"
            )
            consultar = st.form_submit_button("Consultar saldo no GestãoClick")
        if consultar:
            st.session_state.pop(_CACHE, None)
            atual = None
            if not permitido:
                st.warning("É necessário autorizar a consulta GET para este produto.")
            else:
                try:
                    with st.spinner("Consultando catálogo completo por loja, sem modificar o ERP…"):
                        consulta = consultar_saldo_erp(
                            auth, loja_id, produto_id, row["Produto"],
                            row["Código interno"], autorizado=True,
                        )
                except (AuthenticationError, AuthorizationError):
                    raise
                except (AppError, GestaoClickError, TypeError, ValueError):
                    st.warning(
                        "O saldo não pôde ser conferido. Nenhuma quantidade foi copiada. "
                        "Confira o produto, a loja e a integração."
                    )
                else:
                    atual = {
                        "loja": loja_id, "produto": produto_id,
                        "stamp": stamp, "expira": time.monotonic() + _TTL,
                        "consulta": consulta,
                    }
                    st.session_state[_CACHE] = atual
        if atual is not None:
            consulta = atual["consulta"]
            st.metric("Saldo na consulta ao ERP (UN)", format(consulta.saldo, "f"))
            st.caption(
                f"Loja {loja_id} · Lido em "
                f'{consulta.consultado_em:%d/%m/%Y %H:%M:%S} UTC. '
                "Não se atualiza sozinho; o ERP continua sendo a fonte oficial."
            )
            if consulta.saldo != consulta.saldo.to_integral_value():
                st.warning(
                    "O saldo retornou quantidade fracionária. O simulador trabalha "
                    "em unidades inteiras; confira a unidade no GestãoClick."
                )
            elif st.button(
                "Copiar saldo consultado para a simulação",
                key=f"reposicao_copiar_get_{loja_id}_{produto_id}",
            ):
                st.session_state[chave_campo] = format(consulta.saldo, ".0f")
                st.session_state[chave_origem] = {
                    "stamp": stamp, "expira": atual["expira"],
                }
                st.success("Saldo copiado. Confira reservas, unidade e demanda antes de simular.")
    return chave_campo
