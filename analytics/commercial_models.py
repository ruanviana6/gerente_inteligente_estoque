"""Experimentos locais com validação temporal; nunca executados na navegação."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta
from numbers import Real
from typing import Sequence

HORIZONTES = (7, 14, 21, 30)
FEATURES = ("lag_1", "lag_7", "media_7", "media_28", "dia_semana")


def _validar_serie(serie: Sequence[tuple[date, float]], disponibilidade_comprovada: bool) -> list[tuple[date, float]]:
    """Recusa lacunas, ruptura não tratada e séries grandes demais para o plano leve."""
    if disponibilidade_comprovada is not True:
        raise ValueError("A disponibilidade histórica precisa ser comprovada para avaliar o modelo.")
    if not isinstance(serie, (list, tuple)) or not 180 <= len(serie) <= 1825:
        raise ValueError("A avaliação exige entre 180 e 1.825 dias completos.")
    resultado = []
    for indice, item in enumerate(serie):
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("Série diária inválida.")
        dia, quantidade = item
        if type(dia) is not date or isinstance(quantidade, bool) or not isinstance(quantidade, Real):
            raise ValueError("Data ou quantidade inválida.")
        quantidade = float(quantidade)
        if not math.isfinite(quantidade) or not 0 <= quantidade <= 2_147_483_647:
            raise ValueError("Quantidade deve ser finita e não negativa.")
        if indice and dia != resultado[-1][0] + timedelta(days=1):
            raise ValueError("A série deve ser diária, ordenada e sem lacunas.")
        resultado.append((dia, quantidade))
    return resultado


def _features(anteriores: list[float], dia: date) -> list[float]:
    """Usa apenas observações anteriores à data a prever, sem vazamento futuro."""
    return [anteriores[-1], anteriores[-7], sum(anteriores[-7:]) / 7,
            sum(anteriores[-28:]) / 28, float(dia.weekday())]


def _prever_recursivo(modelo, historico: list[float], inicio: date, horizonte: int) -> list[float]:
    """Avança recursivamente usando apenas histórico e previsões já produzidas."""
    acumulado = list(historico)
    resultado = []
    for passo in range(horizonte):
        estimativa = float(modelo.predict([_features(acumulado, inicio + timedelta(days=passo))])[0])
        if not math.isfinite(estimativa):
            raise ValueError("O modelo produziu uma previsão inválida.")
        estimativa = max(0.0, estimativa)
        resultado.append(estimativa)
        acumulado.append(estimativa)
    return resultado


def avaliar_modelo_temporal(serie: Sequence[tuple[date, float]], *, disponibilidade_comprovada: bool) -> dict:
    """Compara árvore leve e média de 7 dias em três cortes e quatro horizontes.

    A importação e o treino de scikit-learn ocorrem apenas nesta função explícita.
    O modelo só é selecionado se reduzir o MAE em pelo menos 5% em todos os
    horizontes; sem ganho comprovado permanece a média simples.
    """
    dados = _validar_serie(serie, disponibilidade_comprovada)
    from sklearn.ensemble import HistGradientBoostingRegressor

    erros = {h: {"baseline": [], "modelo": [], "real": []} for h in HORIZONTES}
    cortes = [len(dados) - 90, len(dados) - 60, len(dados) - 30]
    if cortes[0] < 90:
        raise ValueError("Histórico insuficiente para três cortes temporais.")
    for corte in cortes:
        treino = dados[:corte]
        valores = [v for _, v in treino]
        x = [_features(valores[:i], treino[i][0]) for i in range(28, len(treino))]
        y = valores[28:]
        modelo = HistGradientBoostingRegressor(max_iter=40, max_leaf_nodes=7, min_samples_leaf=5,
                                              early_stopping=False, random_state=17)
        modelo.fit(x, y)
        previsao_ml = _prever_recursivo(modelo, valores, dados[corte][0], max(HORIZONTES))
        baseline = sum(valores[-7:]) / 7
        for horizonte in HORIZONTES:
            realizados = [v for _, v in dados[corte:corte+horizonte]]
            for real, previsto in zip(realizados, previsao_ml[:horizonte]):
                erros[horizonte]["real"].append(real)
                erros[horizonte]["modelo"].append(previsto - real)
                erros[horizonte]["baseline"].append(baseline - real)
    metricas = {}
    for horizonte, valores in erros.items():
        n = len(valores["real"])
        volume = sum(valores["real"])
        metricas[horizonte] = {
            "amostras": n,
            "mae_baseline": sum(abs(x) for x in valores["baseline"]) / n,
            "mae_modelo": sum(abs(x) for x in valores["modelo"]) / n,
            "wape_baseline": sum(abs(x) for x in valores["baseline"]) / volume if volume else None,
            "wape_modelo": sum(abs(x) for x in valores["modelo"]) / volume if volume else None,
            "vies_modelo": sum(valores["modelo"]) / n,
        }
    aprovado = all(m["mae_baseline"] > 0 and m["mae_modelo"] <= m["mae_baseline"] * 0.95
                   for m in metricas.values())
    return {"selecionado": "arvore" if aprovado else "media_7_dias", "metricas": metricas,
            "cortes": [dados[c][0].isoformat() for c in cortes], "treino_ate": dados[-31][0].isoformat(),
            "limites": "Validação temporal local; não comprova ganho em dados empresariais nem sazonalidade anual."}


def avaliar_alertas_clientes(treino: Sequence[dict], validacao: Sequence[dict], *, rotulos_confirmados: bool) -> dict:
    """Compara anomalias de comportamento com uma regra simples em períodos separados.

    Os rótulos devem representar eventos já observados e definidos pela empresa.
    Sem amostra ou rótulos suficientes, mantém a regra e não atribui probabilidade.
    """
    if rotulos_confirmados is not True or len(treino) < 50 or len(validacao) < 20:
        return {"selecionado": "regra", "motivo": "Amostra ou rótulos confirmados insuficientes."}
    if len(treino) > 5000 or len(validacao) > 5000:
        raise ValueError("A avaliação excede o limite leve de amostras.")
    campos = {"customer_ref", "feature_end", "label_end", "cadence_ratio", "frequency_ratio",
              "volume_ratio", "mix_similarity", "observado"}
    def validar(registro):
        """Valida somente atributos agregados e datas realmente observadas."""
        if not isinstance(registro, dict) or set(registro) != campos:
            raise ValueError("Amostra de comportamento contém campos inválidos.")
        if (not isinstance(registro["customer_ref"], str) or len(registro["customer_ref"]) != 64
                or any(c not in "0123456789abcdef" for c in registro["customer_ref"])):
            raise ValueError("Amostra exige pseudônimo válido.")
        if (type(registro["feature_end"]) is not date or type(registro["label_end"]) is not date
                or registro["label_end"] <= registro["feature_end"] or type(registro["observado"]) is not bool):
            raise ValueError("Datas ou rótulo não demonstram observação posterior.")
        valores = [registro[k] for k in ("cadence_ratio", "frequency_ratio", "volume_ratio", "mix_similarity")]
        if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) or not 0 <= v <= 1000 for v in valores):
            raise ValueError("Atributo de comportamento inválido.")
        if registro["mix_similarity"] > 1:
            raise ValueError("Similaridade de mix deve ficar entre zero e um.")
        return [float(v) for v in valores]
    x_treino = [validar(row) for row in treino]
    x_validacao = [validar(row) for row in validacao]
    if ({r["customer_ref"] for r in treino} & {r["customer_ref"] for r in validacao}
            or max(r["label_end"] for r in treino) >= min(r["feature_end"] for r in validacao)):
        raise ValueError("Treino e validação devem separar clientes e evitar dados futuros.")
    from sklearn.ensemble import IsolationForest

    modelo = IsolationForest(n_estimators=32, max_samples=min(256, len(treino)),
                             contamination=0.1, n_jobs=1, random_state=17)
    modelo.fit(x_treino)
    previstos = [int(x) == -1 for x in modelo.predict(x_validacao)]
    referencia = [row["cadence_ratio"] > 1.5 and row["volume_ratio"] < 0.5 for row in validacao]
    verdade = [row["observado"] for row in validacao]
    def metricas(alertas):
        """Conta acertos e falsos alertas sem gerar probabilidade de abandono."""
        acertos = sum(a and b for a, b in zip(alertas, verdade))
        emitidos = sum(alertas)
        positivos = sum(verdade)
        return {"precisao": acertos / emitidos if emitidos else 0.0,
                "sensibilidade": acertos / positivos if positivos else 0.0,
                "falsos_alertas": sum(a and not b for a, b in zip(alertas, verdade)),
                "alertas": emitidos}
    simples, ml = metricas(referencia), metricas(previstos)
    aprovado = (sum(verdade) >= 5 and sum(not v for v in verdade) >= 5
                and ml["precisao"] >= simples["precisao"] + 0.1
                and ml["sensibilidade"] >= simples["sensibilidade"])
    return {"selecionado": "isolation_forest" if aprovado else "regra", "baseline": simples, "modelo": ml,
            "treino_ate": max(r["label_end"] for r in treino).isoformat(),
            "limitacao": "Desvio observado não comprova abandono nem sua causa."}


def atualizar_previsao(serie: Sequence[tuple[date, float]], *, disponibilidade_comprovada: bool) -> dict:
    """Avalia, treina com vendas confirmadas e infere fora do ciclo do Streamlit.

    Guarda somente manifesto e previsões numéricas. O estimador permanece em
    memória no processo controlado: não existe pickle nem cópia do treino no
    banco. Uma nova execução pode reproduzi-lo pelo hash do histórico e versão.
    """
    dados = _validar_serie(serie, disponibilidade_comprovada)
    avaliacao = avaliar_modelo_temporal(dados, disponibilidade_comprovada=True)
    valores = [v for _, v in dados]
    inicio = dados[-1][0] + timedelta(days=1)
    if avaliacao["selecionado"] == "arvore":
        from sklearn.ensemble import HistGradientBoostingRegressor

        estimador = HistGradientBoostingRegressor(max_iter=40, max_leaf_nodes=7, min_samples_leaf=5,
                                                early_stopping=False, random_state=17)
        estimador.fit([_features(valores[:i], dados[i][0]) for i in range(28, len(dados))], valores[28:])
        previstos = _prever_recursivo(estimador, valores, inicio, 30)
    else:
        previstos = [sum(valores[-7:]) / 7] * 30
    import sklearn

    conteudo = json.dumps([(d.isoformat(), v) for d, v in dados], separators=(",", ":"), allow_nan=False)
    return {"model": avaliacao["selecionado"], "training_start": dados[0][0].isoformat(),
            "training_end": dados[-1][0].isoformat(), "data_hash": hashlib.sha256(conteudo.encode()).hexdigest(),
            "features": list(FEATURES), "library_version": sklearn.__version__, "evaluation": avaliacao,
            "predictions": [{"date": (inicio + timedelta(days=i)).isoformat(), "units": round(v, 6)}
                            for i, v in enumerate(previstos)]}


def inferir_alertas_clientes(treino, validacao, atuais, *, rotulos_confirmados: bool) -> dict:
    """Aplica o método escolhido a atributos posteriores à avaliação temporal.

    Uma anomalia é um desvio a investigar. Nem a decisão da árvore nem a regra
    são convertidas em probabilidade de abandono ou em motivo de uma queda.
    """
    avaliacao = avaliar_alertas_clientes(treino, validacao, rotulos_confirmados=rotulos_confirmados)
    if not isinstance(atuais, (list, tuple)) or len(atuais) > 1000:
        raise ValueError("A inferência aceita até mil clientes por lote.")
    atributos = ("cadence_ratio", "frequency_ratio", "volume_ratio", "mix_similarity")
    campos = {"customer_ref", "feature_end", *atributos}
    vistos, x = set(), []
    for row in atuais:
        if not isinstance(row, dict) or set(row) != campos or type(row.get("feature_end")) is not date:
            raise ValueError("Atributos atuais inválidos.")
        ref = row.get("customer_ref")
        if not isinstance(ref, str) or len(ref) != 64 or any(c not in "0123456789abcdef" for c in ref) or ref in vistos:
            raise ValueError("Referência atual inválida ou duplicada.")
        if validacao and row["feature_end"] <= max(r["label_end"] for r in validacao):
            raise ValueError("A inferência precisa ocorrer depois dos rótulos de validação.")
        valores = [row[k] for k in atributos]
        if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) or not 0 <= v <= 1000 for v in valores):
            raise ValueError("Atributos atuais inválidos.")
        if row["mix_similarity"] > 1:
            raise ValueError("Similaridade atual inválida.")
        vistos.add(ref)
        x.append(valores)
    if avaliacao["selecionado"] == "isolation_forest" and x:
        from sklearn.ensemble import IsolationForest

        modelo = IsolationForest(n_estimators=32, max_samples=min(256, len(treino) + len(validacao)),
                                 contamination=0.1, n_jobs=1, random_state=17)
        modelo.fit([[row[k] for k in atributos] for row in [*treino, *validacao]])
        alertas = [int(v) == -1 for v in modelo.predict(x)]
    else:
        alertas = [r["cadence_ratio"] > 1.5 and r["volume_ratio"] < 0.5 for r in atuais]
    return {"evaluation": avaliacao, "alerts": [{"customer_ref": row["customer_ref"], "alert": flag,
            "method": avaliacao["selecionado"], "feature_end": row["feature_end"].isoformat(),
            "cadence_ratio": row["cadence_ratio"], "volume_ratio": row["volume_ratio"]}
            for row, flag in zip(atuais, alertas)]}


def monitorar_previsao(previsoes: Sequence[dict], observadas: Sequence[tuple[date, float]]) -> dict:
    """Mede o erro realizado; previsões nunca voltam como exemplos de venda."""
    if not previsoes or len(previsoes) > 30:
        raise ValueError("A medição aceita de 1 a 30 previsões.")
    reais = {}
    for dia, valor in observadas:
        if type(dia) is not date or dia in reais or isinstance(valor, bool) or not isinstance(valor, Real):
            raise ValueError("Observação confirmada inválida.")
        if not math.isfinite(valor) or valor < 0:
            raise ValueError("Observação confirmada inválida.")
        reais[dia] = float(valor)
    erros, volume, datas = [], 0.0, set()
    for ponto in previsoes:
        if not isinstance(ponto, dict) or set(ponto) != {"date", "units"}:
            raise ValueError("Previsão contém campos inválidos.")
        dia = date.fromisoformat(ponto["date"])
        if dia in datas:
            raise ValueError("Há previsões duplicadas para a mesma data.")
        datas.add(dia)
        valor = ponto["units"]
        if isinstance(valor, bool) or not isinstance(valor, Real) or not math.isfinite(valor) or valor < 0:
            raise ValueError("Previsão inválida.")
        if dia in reais:
            erros.append(float(valor) - reais.pop(dia))
            volume += float(valor) - erros[-1]
    if not erros:
        return {"samples": 0, "mae": None, "wape": None, "bias": None}
    return {"samples": len(erros), "mae": sum(abs(v) for v in erros) / len(erros),
            "wape": sum(abs(v) for v in erros) / volume if volume else None,
            "bias": sum(erros) / len(erros)}
