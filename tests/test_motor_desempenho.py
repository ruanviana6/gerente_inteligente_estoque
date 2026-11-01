"""Compara a otimização em blocos com o motor publicado, sem mudar fórmulas."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import motor
from dados import COLUNAS_SNAPSHOTS, COLUNAS_VENDAS, validar_produtos, validar_snapshots, validar_vendas


def _fixture(name):
    """Carrega oráculos de teste sem acrescentar caminhos ao app produtivo."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "fixtures" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


medias_referencia = _fixture("motor_referencia").medias_referencia
make_payload = _fixture("flatter_data").make_payload
HOJE = pd.Timestamp("2026-09-30")


class MotorDesempenhoTests(unittest.TestCase):
    """Protege cobertura, ruptura, arredondamentos, histórico ausente e ordenação."""

    def test_medias_equivalentes_em_amostras_aleatorias(self):
        """Compara exatamente 60 amostras com lacunas, zeros e vendas conflitantes."""
        rng = np.random.default_rng(20261029)
        for numero in range(60):
            janela = [1, 7, 30, 90][numero % 4]
            codigos = [f"SKU-{x}" for x in rng.permutation(12)]
            vendas, fotos = [], []
            for codigo in codigos + ["ARQUIVADO"]:
                for dia in range(-3, janela + 4):
                    data = HOJE - pd.Timedelta(days=dia)
                    if rng.random() < 0.3:
                        vendas.append({"codigo": codigo, "data": data, "quantidade": int(rng.integers(0, 100))})
                        if rng.random() < 0.2:
                            vendas.append({"codigo": codigo, "data": data, "quantidade": 2})
                    if rng.random() < 0.5:
                        fotos.append({"codigo": codigo, "data": data,
                                      "estoque": 0 if rng.random() < 0.5 else 20})
            vendas = pd.DataFrame(vendas, columns=COLUNAS_VENDAS)
            fotos = pd.DataFrame(fotos, columns=COLUNAS_SNAPSHOTS)
            args = (vendas, fotos, codigos, HOJE, janela)
            with self.subTest(amostra=numero, janela=janela):
                pd.testing.assert_frame_equal(motor.medias_por_produto(*args), medias_referencia(*args),
                                              check_exact=True)

    def test_painel_completo_preserva_resultados_e_dados_originais(self):
        """Mais de mil SKUs cruzam blocos sem alterar indicadores derivados ou entradas."""
        payload = make_payload(1100)
        produtos = validar_produtos(pd.DataFrame(payload["products"]))
        vendas = validar_vendas(pd.DataFrame(payload["sales"]), produtos)
        fotos = validar_snapshots(pd.DataFrame(payload["snapshots"]), produtos)
        originais = [tabela.copy(deep=True) for tabela in (produtos, vendas, fotos)]
        atual = motor.gerar_painel(produtos, vendas, fotos, hoje=HOJE)
        with patch.object(motor, "medias_por_produto", medias_referencia):
            esperado = motor.gerar_painel(produtos, vendas, fotos, hoje=HOJE)
        pd.testing.assert_frame_equal(atual, esperado, check_exact=True)
        for tabela, original in zip((produtos, vendas, fotos), originais):
            pd.testing.assert_frame_equal(tabela, original, check_exact=True)

    def test_ausencia_total_ruptura_total_e_catalogo_vazio(self):
        """Zero elegíveis e nenhuma evidência mantêm seus significados anteriores."""
        vendas = pd.DataFrame(columns=COLUNAS_VENDAS)
        fotos_vazias = pd.DataFrame(columns=COLUNAS_SNAPSHOTS)
        fotos_ruptura = pd.DataFrame({"codigo": ["Z"] * 90,
                                      "data": pd.date_range(HOJE - pd.Timedelta(days=89), HOJE),
                                      "estoque": [0] * 90})
        for codigos, fotos in [([], fotos_vazias), (["Z"], fotos_vazias), (["Z"], fotos_ruptura)]:
            args = (vendas, fotos, codigos, HOJE, 90)
            pd.testing.assert_frame_equal(motor.medias_por_produto(*args), medias_referencia(*args),
                                          check_exact=True)

    def test_limite_de_quantidades_preserva_precisao(self):
        """Múltiplos dias próximos ao limite de importação não sofrem arredondamento novo."""
        datas = pd.date_range(HOJE - pd.Timedelta(days=89), HOJE)
        vendas = pd.DataFrame({"codigo": ["L"] * 90, "data": datas,
                               "quantidade": [2_147_483_647] * 90})
        fotos = pd.DataFrame(columns=COLUNAS_SNAPSHOTS)
        args = (vendas, fotos, ["L"], HOJE, 90)
        pd.testing.assert_frame_equal(motor.medias_por_produto(*args), medias_referencia(*args),
                                      check_exact=True)

    def test_alocacao_diaria_tem_limite_por_bloco(self):
        """Otimização não aloca catálogo vezes janela inteiro para catálogos grandes."""
        vendas = pd.DataFrame(columns=COLUNAS_VENDAS)
        fotos = pd.DataFrame(columns=COLUNAS_SNAPSHOTS)
        original = pd.MultiIndex.from_product
        sizes = []

        def observed(iterables, *args, **kwargs):
            """Observa apenas dimensões da alocação, preservando o índice real."""
            sizes.append(len(iterables[0]) * len(iterables[1]))
            return original(iterables, *args, **kwargs)

        with patch.object(pd.MultiIndex, "from_product", observed):
            result = motor.medias_por_produto(vendas, fotos, [str(i) for i in range(2400)], HOJE, 180)
        self.assertEqual(len(result), 2400)
        self.assertGreater(len(sizes), 1)
        self.assertLessEqual(max(sizes), 100_000)


if __name__ == "__main__":
    unittest.main()
