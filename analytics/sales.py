"""Inteligência de vendas determinística sobre linhas canônicas, sem acesso à rede.

Este módulo não transforma saldo nem calcula receita. A identificação de pedido
usa fonte, loja e documento; a de linha acrescenta o identificador do item.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from typing import Any

MAX_LINHAS = 200_000
MAX_DIAS = 3660
MAX_PRODUTOS_CESTA = 100
MAX_PARES_PROCESSADOS = 2_000_000
STATUS = frozenset({"potential", "separating", "completed", "cancelled", "returned", "unknown"})
CAMPOS = frozenset({
    "source_id", "store_id", "document_id", "line_id", "product_id", "sku", "product_name",
    "category", "supplier", "sale_date", "status", "quantity", "returned_quantity", "source_updated_at",
})


def _data(valor: Any, campo: str) -> date:
    """Aceita data civil ISO explícita, sem inferência de mês ou fuso."""
    if isinstance(valor, datetime):
        raise ValueError(f"{campo}: informe uma data civil, sem horário.")
    if isinstance(valor, date):
        return valor
    try:
        if not isinstance(valor, str) or len(valor) != 10:
            raise ValueError
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{campo}: data inválida; use AAAA-MM-DD.") from None


def _texto(valor: Any, campo: str, limite: int = 120, vazio: bool = False) -> str:
    """Impede identificadores ambíguos e mensagens com conteúdo de entrada."""
    if vazio and valor is None:
        return ""
    if (not isinstance(valor, str) or len(valor) > limite or (not vazio and not valor.strip())
            or any(ord(caractere) < 32 for caractere in valor)):
        raise ValueError(f"{campo}: texto inválido.")
    return valor.strip()


def _quantidade(valor: Any, campo: str) -> float:
    """Recusa negativos, booleanos, infinitos e números fora do limite analítico."""
    try:
        if isinstance(valor, bool):
            raise ValueError
        numero = float(valor)
        if not math.isfinite(numero) or numero < 0 or numero > 1e12:
            raise ValueError
        return numero
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{campo}: quantidade inválida.") from None


def _instante(valor: Any) -> datetime | None:
    """Compara versões somente com horário e fuso explícitos da origem."""
    if valor in (None, ""):
        return None
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        if instante.tzinfo is None:
            raise ValueError
        return instante.astimezone(timezone.utc)
    except (AttributeError, ValueError, TypeError):
        raise ValueError("source_updated_at: informe horário com fuso explícito.") from None


def normalizar_linhas(linhas: Sequence[Mapping[str, Any]]) -> tuple[list[dict], int]:
    """Valida allowlist e escolhe a última versão comprovada de cada linha.

    Duplicatas idênticas são idempotentes. Alterações conflitantes sem dois
    instantes comparáveis são recusadas, pois a ordem de chegada não prova qual
    é o estado vigente. Não se aceitam campos pessoais nem valores financeiros.
    """
    if not isinstance(linhas, (list, tuple)) or len(linhas) > MAX_LINHAS:
        raise ValueError("Informe uma lista de até 200 mil linhas canônicas.")
    selecionadas: dict[tuple, dict] = {}
    duplicadas = 0
    for entrada in linhas:
        if not isinstance(entrada, Mapping) or set(entrada) - CAMPOS:
            raise ValueError("A linha contém campos fora do contrato analítico autorizado.")
        linha = {campo: _texto(entrada.get(campo), campo) for campo in (
            "source_id", "store_id", "document_id", "line_id", "product_id", "sku",
        )}
        nome = _texto(entrada.get("product_name"), "product_name", 300, vazio=True)
        linha["product_name"] = nome or f"SKU {linha['sku']} (nome indisponível)"
        for campo in ("category", "supplier"):
            linha[campo] = _texto(entrada.get(campo, ""), campo, 300, vazio=True)
        linha["sale_date"] = _data(entrada.get("sale_date"), "sale_date").isoformat()
        if not isinstance(entrada.get("status"), str) or entrada["status"] not in STATUS:
            raise ValueError("Status canônico desconhecido; confira o mapeamento da origem.")
        linha["status"] = entrada["status"]
        linha["quantity"] = _quantidade(entrada.get("quantity"), "quantity")
        linha["returned_quantity"] = _quantidade(entrada.get("returned_quantity", 0), "returned_quantity")
        if linha["returned_quantity"] > linha["quantity"]:
            raise ValueError("A devolução não pode superar a quantidade da linha.")
        instante = _instante(entrada.get("source_updated_at"))
        linha["source_updated_at"] = instante.isoformat() if instante else None
        chave = tuple(linha[campo] for campo in ("source_id", "store_id", "document_id", "line_id"))
        anterior = selecionadas.get(chave)
        if anterior is not None:
            duplicadas += 1
            if anterior == linha:
                continue
            instante_anterior = _instante(anterior["source_updated_at"])
            if instante is None or instante_anterior is None or instante == instante_anterior:
                raise ValueError("Versões conflitantes da mesma linha sem ordenação comprovável.")
            if instante < instante_anterior:
                continue
        selecionadas[chave] = linha
    return [selecionadas[chave] for chave in sorted(selecionadas)], duplicadas


def _liquida(linha: Mapping[str, Any]) -> float:
    """Conta venda efetiva líquida; nenhum estado intermediário reduz o estoque."""
    return linha["quantity"] - linha["returned_quantity"] if linha["status"] == "completed" else 0.0


def _pedido(linha: Mapping[str, Any]) -> tuple:
    """Evita colisão de documentos iguais entre lojas ou fontes distintas."""
    return linha["source_id"], linha["store_id"], linha["document_id"]


def _agrupar(linhas: list[dict], campo: str) -> list[dict]:
    """Agrupa unidades líquidas e pedidos distintos sem transformar custo em receita."""
    grupos: dict[str, dict] = {}
    for linha in linhas:
        quantidade = _liquida(linha)
        if quantidade <= 0:
            continue
        chave = linha[campo] or "Não informado"
        grupo = grupos.setdefault(chave, {"nome": chave, "unidades": 0.0, "pedidos": set()})
        grupo["unidades"] += quantidade
        grupo["pedidos"].add(_pedido(linha))
    return sorted(({**grupo, "pedidos": len(grupo["pedidos"])} for grupo in grupos.values()),
                  key=lambda grupo: (-grupo["unidades"], grupo["nome"]))


def _series(linhas: list[dict], inicio: date, fim: date, completa: bool) -> dict:
    """Preenche dias sem vendas apenas quando a abrangência foi declarada completa."""
    dias: Counter[str] = Counter()
    for linha in linhas:
        if _liquida(linha) > 0:
            dias[linha["sale_date"]] += _liquida(linha)
    if completa:
        for deslocamento in range((fim - inicio).days + 1):
            dias.setdefault((inicio + timedelta(days=deslocamento)).isoformat(), 0.0)
    semanas: Counter[str] = Counter()
    meses: Counter[str] = Counter()
    for dia, unidades in dias.items():
        data = date.fromisoformat(dia)
        semanas[(data - timedelta(days=data.weekday())).isoformat()] += unidades
        meses[dia[:7]] += unidades
    return {nome: [{"data": chave, "unidades": valores[chave]} for chave in sorted(valores)]
            for nome, valores in (("diaria", dias), ("semanal", semanas), ("mensal", meses))}


def _cestas(linhas: list[dict], min_pedidos: int, min_coocorrencias: int,
            product_id: str | None, category: str | None) -> dict:
    """Calcula associação por pedido real e mantém o denominador do escopo completo."""
    pedidos: dict[tuple, set[str]] = defaultdict(set)
    produtos: dict[str, dict] = {}
    for linha in linhas:
        if _liquida(linha) > 0:
            pedidos[_pedido(linha)].add(linha["product_id"])
            produtos[linha["product_id"]] = linha
    total = len(pedidos)
    base = {"total_pedidos": total, "min_pedidos": min_pedidos, "min_coocorrencias": min_coocorrencias,
            "pares": [], "disponivel": False,
            "explicacao": "Suporte = pedidos com A e B / todos os pedidos concluídos do escopo. "
                          "Confiança A→B = pedidos com A e B / pedidos com A. "
                          "Lift = suporte / (frequência de A × frequência de B). "
                          "Filtros de produto/categoria selecionam pares, preservando o denominador."}
    if total < min_pedidos:
        return {**base, "motivo": "Amostra de pedidos insuficiente para associação."}
    if any(len(cesta) > MAX_PRODUTOS_CESTA for cesta in pedidos.values()):
        return {**base, "motivo": "Cesta excede 100 produtos; análise recusada para evitar custo excessivo."}
    if sum(len(cesta) * (len(cesta) - 1) // 2 for cesta in pedidos.values()) > MAX_PARES_PROCESSADOS:
        return {**base, "motivo": "Limite de dois milhões de pares atingido; reduza o período."}
    frequencias: Counter[str] = Counter()
    pares: Counter[tuple] = Counter()
    for cesta in pedidos.values():
        frequencias.update(cesta)
        pares.update(combinations(sorted(cesta), 2))
    resultados = []
    for (a, b), quantidade in pares.items():
        if quantidade < min_coocorrencias:
            continue
        if product_id and product_id not in (a, b):
            continue
        if category and all(produtos[p]["category"] != category for p in (a, b)):
            continue
        suporte = quantidade / total
        resultados.append({
            "produto_a": a, "sku_a": produtos[a]["sku"], "nome_a": produtos[a]["product_name"],
            "produto_b": b, "sku_b": produtos[b]["sku"], "nome_b": produtos[b]["product_name"],
            "pedidos_juntos": quantidade, "frequencia_coocorrencia": suporte, "suporte": suporte,
            "confianca_a_b": quantidade / frequencias[a], "confianca_b_a": quantidade / frequencias[b],
            "lift": suporte / ((frequencias[a] / total) * (frequencias[b] / total)),
            "pedidos_a": frequencias[a], "pedidos_b": frequencias[b],
        })
    resultados.sort(key=lambda item: (-item["pedidos_juntos"], item["produto_a"], item["produto_b"]))
    return {**base, "disponivel": True, "motivo": "Coocorrência observada; não comprova causalidade.",
            "pares": resultados[:500], "pares_omitidos": max(0, len(resultados) - 500)}


def _sazonalidade(linhas: list[dict], inicio: date, fim: date, completa: bool) -> dict:
    """Resume calendário e só sinaliza recorrência após dois anos civis completos."""
    nomes = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")
    grupos: dict[str, Counter] = {nome: Counter() for nome in ("mes", "semana", "dia_semana", "trimestre")}
    anos: dict[int, Counter] = defaultdict(Counter)
    for linha in linhas:
        quantidade = _liquida(linha)
        if quantidade <= 0:
            continue
        dia = date.fromisoformat(linha["sale_date"])
        grupos["mes"][dia.month] += quantidade
        grupos["semana"][dia.isocalendar().week] += quantidade
        grupos["dia_semana"][dia.weekday()] += quantidade
        grupos["trimestre"][(dia.month - 1) // 3 + 1] += quantidade
        anos[dia.year][dia.month] += quantidade
    ciclos = [ano for ano in range(inicio.year, fim.year + 1)
              if completa and inicio <= date(ano, 1, 1) and fim >= date(ano, 12, 31)]
    recorrencias = []
    pedidos = {_pedido(linha) for linha in linhas if _liquida(linha) > 0}
    pedidos_por_ano: dict[int, set[tuple]] = defaultdict(set)
    for linha in linhas:
        if _liquida(linha) > 0:
            pedidos_por_ano[date.fromisoformat(linha["sale_date"]).year].add(_pedido(linha))
    if len(ciclos) >= 2 and len(pedidos) >= 40 and all(len(pedidos_por_ano[ano]) >= 20 for ano in ciclos):
        # O índice usa unidades/dia, evitando confundir fevereiro curto com queda.
        taxas: dict[int, dict[int, float]] = {}
        for ano in ciclos:
            taxas[ano] = {}
            for mes in range(1, 13):
                seguinte = date(ano + 1, 1, 1) if mes == 12 else date(ano, mes + 1, 1)
                taxas[ano][mes] = anos[ano][mes] / (seguinte - date(ano, mes, 1)).days
        for mes in range(1, 13):
            indices = []
            for ano in ciclos:
                media = sum(taxas[ano].values()) / 12
                indices.append(taxas[ano][mes] / media if media > 0 else 0.0)
            if all(indice >= 1.15 for indice in indices):
                recorrencias.append({"mes": mes, "anos": ciclos, "indices_relativos": indices,
                                     "rotulo": "Aumento recorrente observado; não é previsão."})
    resultado = {nome: [{"grupo": nomes[chave] if nome == "dia_semana" else chave,
                         "unidades": valores[chave]} for chave in sorted(valores)]
                 for nome, valores in grupos.items()}
    return {**resultado, "anos_completos": ciclos, "recorrencias": recorrencias,
            "classificacao": "Recorrência observada" if recorrencias else "Variação observada",
            "limitacao": "Não é previsão sazonal. Recorrência exige ao menos dois anos civis completos, "
                         "20 pedidos por ciclo e elevação mensal acima de 15% em cada ciclo. Promoções, feriados, "
                         "rupturas e mudanças de catálogo podem explicar a variação. "
                         "Datas comerciais não são inferidas sem um calendário autorizado."}


def analisar_vendas(
    linhas: Sequence[Mapping[str, Any]], inicio: date | str, fim: date | str, *,
    coverage_start: date | str | None = None, coverage_end: date | str | None = None,
    source_id: str | None = None, store_id: str | None = None, product_id: str | None = None,
    category: str | None = None, snapshots: Sequence[Mapping[str, Any]] | None = None,
    min_pedidos: int = 20, min_coocorrencias: int = 3,
) -> dict:
    """Produz análise explicável de uma única fonte/loja, sem consolidar contas.

    A abrangência é uma declaração da origem, não uma prova de completude.
    Comparações usam o mesmo número de dias imediatamente anterior; sem
    cobertura de ambos os períodos, percentuais não são apresentados.
    """
    inicio, fim = _data(inicio, "inicio"), _data(fim, "fim")
    dias = (fim - inicio).days + 1
    if dias <= 0 or dias > MAX_DIAS:
        raise ValueError("Escolha um período ordenado de até 3660 dias.")
    if type(min_pedidos) is not int or type(min_coocorrencias) is not int:
        raise ValueError("Os limites de amostra devem ser inteiros.")
    if not 20 <= min_pedidos <= MAX_LINHAS or not 3 <= min_coocorrencias <= MAX_LINHAS:
        raise ValueError("Amostra mínima: 20 pedidos e três coocorrências.")
    normalizadas, duplicadas = normalizar_linhas(linhas)
    escopo = [linha for linha in normalizadas
              if (source_id is None or linha["source_id"] == source_id)
              and (store_id is None or linha["store_id"] == store_id)]
    fontes_lojas = sorted({(linha["source_id"], linha["store_id"]) for linha in escopo})
    if len(fontes_lojas) > 1:
        raise ValueError("Selecione uma única fonte e loja; não há consolidação implícita.")
    if fontes_lojas:
        source_id, store_id = fontes_lojas[0]
    anterior_fim = inicio - timedelta(days=1)
    anterior_inicio = inicio - timedelta(days=dias)
    cobertura_inicio = _data(coverage_start, "coverage_start") if coverage_start is not None else None
    cobertura_fim = _data(coverage_end, "coverage_end") if coverage_end is not None else None
    if (cobertura_inicio is None) != (cobertura_fim is None):
        raise ValueError("Informe o início e o fim da abrangência juntos.")
    if cobertura_inicio is not None and cobertura_inicio > cobertura_fim:
        raise ValueError("Abrangência de dados invertida.")
    completa = cobertura_inicio is not None and cobertura_inicio <= inicio and cobertura_fim >= fim
    comparavel = completa and cobertura_inicio <= anterior_inicio
    filtradas = [linha for linha in escopo if (product_id is None or linha["product_id"] == product_id)
                 and (category is None or linha["category"] == category)]
    atuais = [linha for linha in filtradas if inicio.isoformat() <= linha["sale_date"] <= fim.isoformat()]
    anteriores = [linha for linha in filtradas
                  if anterior_inicio.isoformat() <= linha["sale_date"] <= anterior_fim.isoformat()]
    cesta_periodo = [linha for linha in escopo if inicio.isoformat() <= linha["sale_date"] <= fim.isoformat()]
    status_atuais_completos = not any(linha["status"] == "unknown" for linha in atuais)
    status_cesta_completos = not any(linha["status"] == "unknown" for linha in cesta_periodo)
    status_anteriores_completos = not any(linha["status"] == "unknown" for linha in anteriores)
    completa = completa and status_atuais_completos
    comparavel = comparavel and status_atuais_completos and status_anteriores_completos
    vendas = [linha for linha in atuais if _liquida(linha) > 0]
    unidades = math.fsum(_liquida(linha) for linha in atuais)
    unidades_anteriores = math.fsum(_liquida(linha) for linha in anteriores)
    pedidos = {_pedido(linha) for linha in vendas}
    pedidos_anteriores = {_pedido(linha) for linha in anteriores if _liquida(linha) > 0}
    percentual = ((unidades / unidades_anteriores - 1) * 100
                  if comparavel and unidades_anteriores > 0 else None)
    limitacoes = ["Unidades líquidas de vendas concluídas, sem faturamento, receita ou ticket médio.",
                  "Abrangência declarada depende de reconciliação com a origem; HTTP 200 não comprova completude.",
                  "A análise não realiza baixas nem altera o estoque oficial."]
    if not completa:
        limitacoes.append("Período sem abrangência completa comprovada pela declaração; zeros não são presumidos.")
    if not comparavel:
        limitacoes.append("Sem abrangência comparável no período anterior; variação percentual indisponível.")
    elif unidades_anteriores == 0:
        limitacoes.append("Base anterior igual a zero; crescimento percentual indefinido.")
    desconhecidos = sum(linha["status"] == "unknown" for linha in atuais)
    if desconhecidos:
        limitacoes.append("Existem status desconhecidos; essas linhas foram excluídas das vendas efetivas.")
    elif not status_cesta_completos:
        limitacoes.append("Há status desconhecidos em outros produtos do período; associação indisponível.")
    devolucoes_incompletas = sum(linha["status"] == "returned" and linha["returned_quantity"] == 0
                                for linha in atuais)
    if devolucoes_incompletas:
        limitacoes.append("Há linhas devolvidas sem quantidade devolvida confirmada; total de devoluções é parcial.")
    contexto_estoque: dict[str, dict] = {}
    if snapshots is not None:
        if not isinstance(snapshots, (list, tuple)) or len(snapshots) > MAX_LINHAS:
            raise ValueError("Snapshots excedem o limite analítico.")
        for snapshot in snapshots:
            if (snapshot.get("source_id") != source_id or snapshot.get("store_id") != store_id):
                continue
            dia = _data(snapshot.get("date"), "snapshot.date")
            if inicio <= dia <= fim:
                produto = _texto(snapshot.get("product_id"), "snapshot.product_id")
                saldo = _quantidade(snapshot.get("stock"), "snapshot.stock")
                contexto = contexto_estoque.setdefault(produto, {"dias_zero": set(), "saldos": {}, "ultimo": None})
                if dia in contexto["saldos"] and contexto["saldos"][dia] != saldo:
                    raise ValueError("Snapshots conflitantes no mesmo dia; selecione a versão do lote.")
                contexto["saldos"][dia] = saldo
                if saldo == 0:
                    contexto["dias_zero"].add(dia)
                if contexto["ultimo"] is None or dia > contexto["ultimo"][0]:
                    contexto["ultimo"] = (dia, snapshot)
    if not contexto_estoque:
        limitacoes.append("Sem snapshots associados ao escopo; indisponibilidade de estoque é desconhecida.")
    produtos = []
    grupos_atuais: dict[str, list[dict]] = defaultdict(list)
    grupos_anteriores: dict[str, list[dict]] = defaultdict(list)
    for linha in atuais:
        grupos_atuais[linha["product_id"]].append(linha)
    for linha in anteriores:
        grupos_anteriores[linha["product_id"]].append(linha)
    for produto in sorted(grupos_atuais.keys() | grupos_anteriores.keys()):
        linhas_atuais = grupos_atuais[produto]
        linhas_anteriores = grupos_anteriores[produto]
        referencia = (linhas_atuais or linhas_anteriores)[-1]
        atual = math.fsum(_liquida(linha) for linha in linhas_atuais)
        anterior = math.fsum(_liquida(linha) for linha in linhas_anteriores)
        n_atual = len({_pedido(linha) for linha in linhas_atuais if _liquida(linha) > 0})
        n_anterior = len({_pedido(linha) for linha in linhas_anteriores if _liquida(linha) > 0})
        diferenca = atual - anterior
        variacao = ((atual / anterior - 1) * 100 if comparavel and anterior > 0 else None)
        amostra = dias >= 14 and atual >= 5 and anterior >= 5 and n_atual >= 5 and n_anterior >= 5
        metades = []
        meio = dias // 2
        for primeira in (True, False):
            atual_metade = math.fsum(_liquida(linha) for linha in linhas_atuais
                                    if ((date.fromisoformat(linha["sale_date"]) - inicio).days < meio) == primeira)
            anterior_metade = math.fsum(_liquida(linha) for linha in linhas_anteriores
                                       if ((date.fromisoformat(linha["sale_date"]) - anterior_inicio).days < meio)
                                       == primeira)
            metades.append((atual_metade, anterior_metade))
        sustentado = bool(comparavel and amostra and diferenca != 0
                          and all((a > b if diferenca > 0 else a < b) for a, b in metades))
        contexto = contexto_estoque.get(produto)
        avisos = []
        if not comparavel:
            avisos.append("Abrangência anterior insuficiente.")
        if anterior == 0:
            avisos.append("Base anterior zero; pode ser produto novo ou histórico insuficiente.")
        if not amostra:
            avisos.append("Amostra insuficiente para afirmar crescimento ou queda sustentados.")
        dias_zero = len(contexto["dias_zero"]) if contexto else None
        if dias_zero:
            avisos.append("Snapshot com saldo zero no período; vendas podem subestimar a demanda.")
            sustentado = False
        dias_desconhecidos = dias - len(contexto["saldos"]) if contexto else dias
        if dias_desconhecidos:
            avisos.append(f"Disponibilidade desconhecida em {dias_desconhecidos} dias sem snapshot.")
        cobertura = prazo = None
        if contexto:
            ultimo = contexto["ultimo"][1]
            if ultimo.get("coverage_days") is not None:
                cobertura = _quantidade(ultimo["coverage_days"], "snapshot.coverage_days")
            if ultimo.get("lead_time_days") is not None:
                prazo = _quantidade(ultimo["lead_time_days"], "snapshot.lead_time_days")
        produtos.append({
            "product_id": produto, "sku": referencia["sku"], "nome": referencia["product_name"],
            "categoria": referencia["category"], "fornecedor": referencia["supplier"],
            "unidades": atual, "unidades_anteriores": anterior if comparavel else None,
            "diferenca_absoluta": diferenca if comparavel else None, "variacao_percentual": variacao,
            "pedidos": n_atual, "pedidos_anteriores": n_anterior if comparavel else None,
            "sustentado": sustentado,
            "classificacao": ("Crescimento sustentado" if diferenca > 0 else "Queda sustentada")
                              if sustentado else "Variação observada",
            "demanda_diaria_observada": atual / dias if completa else None,
            "dias_snapshot_zero": dias_zero, "dias_snapshot_desconhecido": dias_desconhecidos,
            "snapshot_referencia": contexto["ultimo"][0].isoformat() if contexto else None,
            "cobertura_dias": cobertura, "prazo_entrega_dias": prazo,
            "avisos": avisos,
        })
    produtos.sort(key=lambda item: (-item["unidades"], item["sku"], item["product_id"]))
    insights = []
    for produto in produtos:
        if produto["sustentado"]:
            insights.append({"titulo": f"{produto['nome']}: {produto['classificacao'].lower()}",
                             "explicacao": f"{produto['unidades']:g} contra {produto['unidades_anteriores']:g} "
                                           f"unidades em períodos de {dias} dias, com a mesma direção nas duas metades.",
                             "product_id": produto["product_id"], "limitacoes": produto["avisos"]})
        if (produto["variacao_percentual"] is not None and produto["variacao_percentual"] > 0
                and produto["cobertura_dias"] is not None and produto["prazo_entrega_dias"] is not None
                and produto["cobertura_dias"] < produto["prazo_entrega_dias"]):
            insights.append({"titulo": f"{produto['nome']}: aumento observado e cobertura abaixo do prazo",
                             "explicacao": f"Cobertura de {produto['cobertura_dias']:g} dias; "
                                           f"prazo de {produto['prazo_entrega_dias']:g} dias. "
                                           f"Snapshot de {produto['snapshot_referencia']}. "
                                           "Conferir o cálculo do estoque antes de decidir a reposição.",
                             "product_id": produto["product_id"], "limitacoes": produto["avisos"]})
    cestas = _cestas(cesta_periodo, min_pedidos, min_coocorrencias, product_id, category)
    cestas["abrangencia_completa"] = completa and status_cesta_completos
    if not cestas["abrangencia_completa"]:
        # O denominador de suporte só é interpretável com todos os pedidos do
        # período declarado. Linhas faltantes podem mudar confiança e lift.
        cestas.update({"disponivel": False, "pares": [],
                       "motivo": "Sem abrangência completa ou com status desconhecidos; associação indisponível."})
    if cestas["pares"]:
        par = cestas["pares"][0]
        insights.append({"titulo": f"{par['nome_a']} e {par['nome_b']} aparecem juntos",
                         "explicacao": f"{par['pedidos_juntos']} de {cestas['total_pedidos']} pedidos concluídos. "
                                       "Associação observada, sem evidência de causalidade.",
                         "limitacoes": [cestas["explicacao"]]})
    sazonalidade = _sazonalidade(atuais, inicio, fim, completa)
    return {
        "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat(), "dias": dias},
        "origem": {"source_id": source_id, "store_id": store_id},
        "abrangencia": {"inicio": cobertura_inicio.isoformat() if cobertura_inicio else None,
                        "fim": cobertura_fim.isoformat() if cobertura_fim else None,
                        "periodo_completo": completa},
        "comparacao": {"inicio": anterior_inicio.isoformat(), "fim": anterior_fim.isoformat(),
                       "comparavel": comparavel, "unidades_anteriores": unidades_anteriores if comparavel else None,
                       "diferenca_absoluta": unidades - unidades_anteriores if comparavel else None,
                       "variacao_percentual": percentual},
        "resumo": {"unidades": unidades, "pedidos_concluidos": len(pedidos),
                   "pedidos_anteriores": len(pedidos_anteriores) if comparavel else None,
                   "produtos_vendidos": len({linha["product_id"] for linha in vendas}),
                   "linhas_canceladas": sum(linha["status"] == "cancelled" for linha in atuais),
                   "unidades_devolvidas": math.fsum(linha["returned_quantity"] for linha in atuais
                                                   if linha["status"] in {"completed", "returned"}),
                   "linhas_status_desconhecido": desconhecidos, "duplicatas_descartadas": duplicadas},
        "series": _series(atuais, inicio, fim, completa), "produtos": produtos,
        "categorias": _agrupar(atuais, "category"), "fornecedores": _agrupar(atuais, "supplier"),
        "lojas": _agrupar(atuais, "store_id"), "sazonalidade": sazonalidade,
        "cestas": cestas, "insights": insights, "limitacoes": limitacoes,
    }
