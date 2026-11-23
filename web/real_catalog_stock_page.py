"""Exibição da última fotografia real do catálogo, sem atualizar ou modificar o ERP."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo

import streamlit as st

from integracoes.gestaoclick import ContratoInvalido, GestaoClickError
from web.errors import AppError, AuthenticationError, AuthorizationError
from web.real_home_status import selecionar_fonte_real
from web.real_store_selector import escolher_loja_real
from web.sales_service import access_stamp


def validar_fotografia(value, loja):
    """Recusa lotes ambíguos, parciais ou com somatório inconsistente."""
    if not isinstance(value, dict) or value.get("store_id") != loja:
        raise ContratoInvalido("A fotografia não pertence à loja autorizada.")
    if value.get("found") is False and set(value) == {"found", "store_id"}:
        return None
    produtos = value.get("products")
    n = value.get("product_count")
    if value.get("found") is not True or not isinstance(produtos, list) or type(n) is not int or not 1 <= n <= 1000 or len(produtos) != n:
        raise ContratoInvalido("A fotografia não possui cobertura reconciliada.")
    try:
        data = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        total = Decimal(str(value["total_stock"]))
    except (KeyError, ValueError, TypeError, InvalidOperation, AttributeError):
        raise ContratoInvalido("Metadados da fotografia inválidos.") from None
    if data.utcoffset() is None or not total.is_finite() or total < 0:
        raise ContratoInvalido("A fotografia não possui data ou saldo válido.")
    linhas = []
    vistos = set()
    soma = Decimal("0")
    for row in produtos:
        if not isinstance(row, dict):
            raise ContratoInvalido("Produto inválido na fotografia.")
        codigo = row.get("id")
        if not isinstance(codigo, str) or not 1 <= len(codigo) <= 20 or not codigo.isascii() or not codigo.isdigit() or codigo in vistos:
            raise ContratoInvalido("ID de produto duplicado ou inválido.")
        valor = row.get("estoque")
        if isinstance(valor, bool) or not isinstance(valor, (int, float, str, Decimal)):
            raise ContratoInvalido("Quantidade de estoque inválida.")
        try:
            saldo = Decimal(str(valor))
        except InvalidOperation:
            raise ContratoInvalido("Quantidade de estoque inválida.") from None
        if not saldo.is_finite() or saldo < 0 or saldo > Decimal("1000000000000"):
            raise ContratoInvalido("Quantidade de estoque fora do limite.")
        nome, categoria, codigo_interno = (row.get(k) for k in ("nome", "categoria", "codigo_interno"))
        for campo, rotulo in ((nome, "nome"), (categoria, "categoria"), (codigo_interno, "código interno")):
            if campo is not None and (
                not isinstance(campo, str) or not 1 <= len(campo.strip()) <= 200
                or any(ord(c) < 32 or ord(c) == 127 or c in "<>" for c in campo)
            ):
                raise ContratoInvalido(f"Identificação inválida no {rotulo} do produto.")
        vistos.add(codigo)
        soma += saldo
        linhas.append({
            "Produto": nome or "Não informado",
            "Categoria": categoria or "Não informada",
            "Código interno": codigo_interno or "Não informado",
            "Produto (ID)": codigo,
            "Saldo na fotografia (não atual)": float(saldo),
        })
    if soma != total:
        raise ContratoInvalido("Os saldos da fotografia não conciliam.")
    linhas.sort(key=lambda x: int(x["Produto (ID)"]))
    return {"store_id": loja, "created_at": data, "total_stock": total, "rows": linhas}



def exportar_fotografia_csv(foto: dict) -> bytes:
    """Exporta apenas produtos e saldos da foto validada, sem fórmulas no Excel."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([
        "Loja (ID)", "Fotografia em (ISO)", "Produto", "Categoria",
        "Código interno", "Produto (ID)", "Saldo histórico (não atual)",
    ])
    for row in foto["rows"]:
        valores = [
            foto["store_id"], foto["created_at"].isoformat(),
            row["Produto"], row["Categoria"], row["Código interno"],
            row["Produto (ID)"], row["Saldo na fotografia (não atual)"],
        ]
        writer.writerow([
            "'" + value if isinstance(value, str) and
            value.lstrip().startswith(("=", "+", "-", "@")) else value
            for value in valores
        ])
    return buffer.getvalue().encode("utf-8-sig")


def consultar_catalogo_real(auth, loja_id: str | None = None):
    """Usa somente duas RPCs autorizadas no Supabase, sem conexão ao GestãoClick."""
    with auth.operation():
        antes = auth.require("integrations.manage")
        if antes.profile.role != "Master":
            raise AuthorizationError("Catálogo real disponível somente para Master.")
        stamp = access_stamp(antes, auth.generation)
        fonte = selecionar_fonte_real(auth.rpc("integration_sources", {}, "trends.view"), loja_id)
        if fonte is None:
            return None
        fonte_id, loja_reconhecida = fonte
        UUID(fonte_id)
        bruto = auth.rpc("catalog_stock_latest", {
            "p_source_id": fonte_id, "p_store_id": loja_reconhecida,
        }, "integrations.manage")
        if stamp != access_stamp(auth.require("integrations.manage"), auth.generation):
            raise AuthorizationError("A autorização mudou durante a consulta.")
        return validar_fotografia(bruto, loja_reconhecida)


def real_catalog_controls(servico, contexto):
    """Permite conferir todos os produtos reais no Estoque, sem importar DEMO."""
    if contexto.profile.role != "Master" or not contexto.can("integrations.manage"):
        return
    st.subheader("Catálogo real · última fotografia do GestãoClick")
    loja_id = escolher_loja_real(servico.auth)
    if loja_id is None:
        return
    try:
        foto = consultar_catalogo_real(servico.auth, loja_id)
    except (AuthenticationError, AuthorizationError):
        raise
    except (AppError, GestaoClickError, ValueError, TypeError):
        st.warning("Não foi possível carregar a fotografia real. Confira Administração → Integrações.")
        return
    from web.real_stock_refresh_page import atualizar_fotografia_estoque

    # A primeira fotografia também deve poder ser criada a partir de Estoque.
    atualizar_fotografia_estoque(servico, contexto, loja_id)
    if foto is None:
        st.info(
            f"A loja {loja_id} ainda não possui fotografia real salva. "
            "Use a conferência acima para consultar e registrar o primeiro catálogo."
        )
        return
    data = foto["created_at"].astimezone(ZoneInfo("America/Fortaleza"))
    a, b = st.columns(2)
    a.metric("Produtos na fotografia", len(foto["rows"]))
    b.metric("Saldo agregado da fotografia", f'{foto["total_stock"]:,.0f}'.replace(",", "."))
    st.caption(f'Loja {foto["store_id"]} · Última fotografia: {data:%d/%m/%Y %H:%M} (Fortaleza).')
    st.warning(
        "Esta é a última fotografia gravada no Supabase, NÃO o estoque ao vivo. "
        "Não subtraia vendas observadas deste saldo nem use a lista para fazer pedidos automaticamente."
    )
    sem_nome = sum(r["Produto"] == "Não informado" for r in foto["rows"])
    sem_categoria = sum(r["Categoria"] == "Não informada" for r in foto["rows"])
    if sem_nome or sem_categoria:
        st.warning(
            f"Identificação pendente nesta fotografia: {sem_nome} produto(s) sem nome e "
            f"{sem_categoria} sem categoria. No painel de atualização ACIMA, escolha "
            "'Conferir conexão e loja do estoque', depois 'Conferir catálogo completo' "
            "e, após validar os dados do ERP, 'Salvar fotografia analítica no Supabase'. "
            "O saldo da foto antiga continuará identificado como histórico. "
            "Se a consulta completa não devolver os nomes, não serão inventados."
        )
    busca = st.text_input("Buscar por nome, categoria, código ou ID",
                          key="estoque_catalogo_real_busca").strip()
    linhas = foto["rows"]
    if busca:
        termo = busca.casefold()
        linhas = [r for r in linhas if any(
            termo in str(r[c]).casefold() for c in
            ("Produto", "Categoria", "Código interno", "Produto (ID)")
        )]
    st.dataframe(linhas, hide_index=True, width="stretch", key="estoque_catalogo_real_tabela")
    if contexto.can("inventory.export"):
        st.download_button(
            "Exportar catálogo real identificado (CSV)",
            data=exportar_fotografia_csv(foto),
            file_name=f'catalogo_real_loja_{foto["store_id"]}.csv',
            mime="text/csv; charset=utf-8",
            key="estoque_catalogo_real_csv",
            on_click="ignore",
        )
