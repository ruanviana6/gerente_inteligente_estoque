"""Análise comercial explicável de linhas pseudonimizadas e abrangência comprovada.

Este módulo não consulta ERP, banco ou rede. O conector real continua sem cliente
e município até que o contrato autorizado seja conhecido e revisado.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Mapping, Sequence

from analytics.sales import analisar_vendas

MAX_LINHAS = 100_000
UF_IBGE = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
           "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
           "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR",
           "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF"}
CAMPOS = frozenset({"source_id", "store_id", "document_id", "line_id", "customer_ref", "city_code",
                    "city_name", "state", "sale_date", "sku", "quantity", "returned_quantity", "status"})


def pseudonimizar_cliente(source: str, store: str, identifier: str, secret: bytes) -> str:
    """Deriva referência estável por origem e loja, sem guardar a identidade de origem."""
    if not all(isinstance(x, str) and 0 < len(x) <= 120 and "\0" not in x for x in (source, store, identifier)):
        raise ValueError("Identificador de origem inválido.")
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("O segredo de pseudonimização precisa ter pelo menos 32 bytes.")
    payload = b"\0".join(x.encode("utf-8") for x in (source, store, identifier))
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _texto(valor: object, nome: str, limite: int = 120) -> str:
    """Valida identificadores sem aceitar estruturas ou controles ocultos."""
    if not isinstance(valor, str) or not valor.strip() or len(valor) > limite or any(ord(c) < 32 for c in valor):
        raise ValueError(f"Campo {nome} inválido.")
    return valor.strip()


def _data(valor: object) -> date:
    """Exige data civil ISO sem supor fuso ou horário."""
    if type(valor) is date:
        return valor
    if not isinstance(valor, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
        raise ValueError("Data comercial inválida.")
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise ValueError("Data comercial inválida.") from None


def normalizar_linhas_comerciais(linhas: Sequence[Mapping]) -> tuple[list[dict], int]:
    """Rejeita PII e duplicatas conflitantes, mantendo documento e linha por origem."""
    if not isinstance(linhas, (list, tuple)) or len(linhas) > MAX_LINHAS:
        raise ValueError("A análise aceita até 100 mil linhas por execução controlada.")
    unicas: dict[tuple, dict] = {}
    pedidos: dict[tuple, tuple] = {}
    cidades: dict[tuple, tuple] = {}
    duplicadas = 0
    for entrada in linhas:
        if not isinstance(entrada, Mapping) or set(entrada) - CAMPOS:
            raise ValueError("A linha contém dados fora do contrato comercial minimizado.")
        row = {key: _texto(entrada.get(key), key) for key in ("source_id", "store_id", "document_id", "line_id", "sku")}
        customer = entrada.get("customer_ref")
        if not isinstance(customer, str) or not re.fullmatch(r"[0-9a-f]{64}", customer):
            raise ValueError("Cliente deve usar referência pseudonimizada.")
        row["customer_ref"] = customer
        row["sale_date"] = _data(entrada.get("sale_date")).isoformat()
        for key in ("quantity", "returned_quantity"):
            value = entrada.get(key, 0)
            if type(value) is not int or not 0 <= value <= 2_147_483_647:
                raise ValueError("Quantidade comercial inválida.")
            row[key] = value
        if row["returned_quantity"] > row["quantity"]:
            raise ValueError("Devolução excede a quantidade da linha.")
        status = entrada.get("status")
        if status not in {"completed", "returned", "cancelled", "pending"}:
            raise ValueError("Status comercial não está confirmado.")
        if status == "returned" and row["returned_quantity"] == 0:
            raise ValueError("Devolução sem quantidade exige reconciliação.")
        row["status"] = status
        city, city_name, state = entrada.get("city_code"), entrada.get("city_name"), entrada.get("state")
        if city is None and city_name is None and state is None:
            row.update(city_code=None, city_name=None, state=None)
        elif (isinstance(city, str) and re.fullmatch(r"\d{7}", city)
              and isinstance(state, str) and UF_IBGE.get(city[:2]) == state):
            row.update(city_code=city, city_name=_texto(city_name, "cidade", 100), state=state)
        else:
            raise ValueError("Cidade, código IBGE e UF precisam estar validados em conjunto.")
        if row["city_code"]:
            city_key = (row["source_id"], row["store_id"], row["city_code"])
            city_identity = (row["city_name"].casefold(), row["state"])
            if city_key in cidades and cidades[city_key] != city_identity:
                raise ValueError("Código de cidade apresenta nome ou UF conflitante.")
            cidades[city_key] = city_identity
        key = (row["source_id"], row["store_id"], row["document_id"], row["line_id"])
        if key in unicas:
            if unicas[key] != row:
                raise ValueError("Há versões conflitantes sem ordem comprovada.")
            duplicadas += 1
            continue
        order = key[:3]
        identity = (customer, row["city_code"], row["state"], row["sale_date"])
        if order in pedidos and pedidos[order] != identity:
            raise ValueError("Um pedido apresenta cliente, cidade ou data conflitante.")
        pedidos[order] = identity
        unicas[key] = row
    return list(unicas.values()), duplicadas


def analisar_comercial(linhas: Sequence[Mapping], inicio: date, fim: date, *, source: str, store: str,
                      coverage_start: date | None, coverage_end: date | None,
                      min_clientes_cidade: int = 3) -> dict:
    """Compara períodos equivalentes e suprime grupos pequenos ou sem cobertura."""
    inicio, fim = _data(inicio), _data(fim)
    source, store = _texto(source, "origem"), _texto(store, "loja")
    if inicio > fim or (fim-inicio).days > 365 or type(min_clientes_cidade) is not int or min_clientes_cidade < 3:
        raise ValueError("Período ou limite de privacidade inválido.")
    normalized, duplicates = normalizar_linhas_comerciais(linhas)
    previous_start = inicio - timedelta(days=(fim-inicio).days + 1)
    result = {"periodo": [inicio.isoformat(), fim.isoformat()], "anterior": [previous_start.isoformat(),
              (inicio-timedelta(days=1)).isoformat()], "source": source, "store": store,
              "duplicadas": duplicates, "clientes": [], "cidades": [], "cestas": None, "avisos": []}
    if coverage_start is None or coverage_end is None or _data(coverage_start) > previous_start or _data(coverage_end) < fim:
        result["avisos"].append("Abrangência insuficiente para comparar períodos ou calcular associações.")
        return result
    scope = [r for r in normalized if r["source_id"] == source and r["store_id"] == store
             and previous_start <= _data(r["sale_date"]) <= fim and r["status"] == "completed"]
    orders: dict[tuple, dict] = {}
    for row in scope:
        units = row["quantity"] - row["returned_quantity"]
        if units <= 0:
            continue
        key = (row["source_id"], row["store_id"], row["document_id"])
        order = orders.setdefault(key, {"customer": row["customer_ref"], "city": row["city_code"],
            "city_name": row["city_name"], "state": row["state"], "date": _data(row["sale_date"]),
            "units": 0, "products": set()})
        order["units"] += units
        order["products"].add(row["sku"])
    customers: dict[str, list[dict]] = defaultdict(list)
    cities: dict[tuple, list[dict]] = defaultdict(list)
    for order in orders.values():
        customers[order["customer"]].append(order)
        if order["city"]:
            cities[(order["city"], order["city_name"], order["state"])].append(order)
    for customer, group in customers.items():
        current = [o for o in group if o["date"] >= inicio]
        previous = [o for o in group if o["date"] < inicio]
        dates = sorted({o["date"] for o in group})
        if len(dates) < 4 or len(previous) < 2:
            continue
        intervals = [(b-a).days for a, b in zip(dates, dates[1:])]
        cadence = median(intervals)
        elapsed = (fim - dates[-1]).days
        old_units, new_units = sum(o["units"] for o in previous), sum(o["units"] for o in current)
        old_mix = set().union(*(o["products"] for o in previous))
        new_mix = set().union(*(o["products"] for o in current)) if current else set()
        flags = []
        if cadence > 0 and elapsed > cadence * 1.5 and elapsed > cadence + 7:
            flags.append("cadencia_atrasada")
        if len(current) >= 2 and old_units and new_units >= old_units * 1.25 and len(current) >= len(previous):
            flags.append("volume_crescente")
        if old_units and new_units <= old_units * 0.5:
            flags.append("volume_reduzido")
        if len(current) >= 2 and old_mix and new_mix and len(old_mix & new_mix) / len(old_mix | new_mix) < 0.5:
            flags.append("mix_alterado")
        if flags:
            result["clientes"].append({"customer_ref": customer, "alertas": flags, "pedidos_atuais": len(current),
                "pedidos_anteriores": len(previous), "unidades_atuais": new_units, "unidades_anteriores": old_units,
                "intervalo_mediano_dias": cadence, "dias_desde_ultimo": elapsed,
                "similaridade_mix": len(old_mix & new_mix) / len(old_mix | new_mix) if new_mix else 0.0,
                "limitacao": "Vendas observadas não explicam o motivo; disponibilidade histórica pode ser incompleta."})
    for (code, city_name, state), group in cities.items():
        current = [o for o in group if o["date"] >= inicio]
        previous = [o for o in group if o["date"] < inicio]
        if len({o["customer"] for o in current}) < min_clientes_cidade or len({o["customer"] for o in previous}) < min_clientes_cidade:
            continue
        old, new = sum(o["units"] for o in previous), sum(o["units"] for o in current)
        compradores = {"current": defaultdict(set), "previous": defaultdict(set)}
        for periodo, pedidos_periodo in (("current", current), ("previous", previous)):
            for pedido in pedidos_periodo:
                for produto in pedido["products"]:
                    compradores[periodo][produto].add(pedido["customer"])
        products = Counter(product for order in current for product in order["products"]
                           if len(compradores["current"][product]) >= min_clientes_cidade)
        old_products = Counter(product for order in previous for product in order["products"]
                               if len(compradores["previous"][product]) >= min_clientes_cidade)
        result["cidades"].append({"city_code": code, "city_name": city_name, "state": state,
            "unidades_atuais": new, "unidades_anteriores": old, "pedidos_atuais": len(current),
            "pedidos_anteriores": len(previous), "variacao_pct": (new-old)/old*100 if old else None,
            "clientes_atuais": len({o["customer"] for o in current}),
            "clientes_anteriores": len({o["customer"] for o in previous}),
            "produtos_mais_pedidos": products.most_common(5),
            "produtos_anteriores": old_products.most_common(5),
            "limitacao": "Agrupamento mínimo preservado; variação não identifica a causa."})
    sales_rows = [{"source_id": r["source_id"], "store_id": r["store_id"], "document_id": r["document_id"],
                   "line_id": r["line_id"], "product_id": r["sku"], "sku": r["sku"], "product_name": r["sku"],
                   "sale_date": r["sale_date"], "quantity": r["quantity"], "returned_quantity": r["returned_quantity"],
                   "status": r["status"]} for r in scope]
    result["cestas"] = analisar_vendas(sales_rows, inicio, fim, coverage_start=coverage_start,
                                      coverage_end=coverage_end, source_id=source, store_id=store)["cestas"]
    result["clientes"].sort(key=lambda x: (-abs(x["unidades_atuais"]-x["unidades_anteriores"]), x["customer_ref"]))
    result["cidades"].sort(key=lambda x: (-abs(x["unidades_atuais"]-x["unidades_anteriores"]), x["city_code"]))
    return result
