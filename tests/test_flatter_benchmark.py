"""Impede dupla contagem de serviços na medição residual da interface."""
from __future__ import annotations

import importlib.util
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "benchmark_flatter", Path(__file__).parents[1] / "scripts" / "benchmark_flatter.py",
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class Scenario:
    """Fornece contadores vazios para controlar somente as fronteiras de tempo."""

    def reset_counts(self):
        """Não simula rede nem interfere no relógio determinístico do teste."""
        self.calls = Counter()
        self.http_seconds = Counter()


class BenchmarkBoundaryTests(unittest.TestCase):
    """Exercita aninhamento, sobreposição e limpeza entre amostras."""

    def test_nested_services_are_counted_once(self):
        """O residual exclui o intervalo externo, sem somar novamente a chamada interna."""
        probe = benchmark.Probe()
        inner = probe.instrument(lambda: None, "interno", boundary=True)
        outer = probe.instrument(inner, "externo", boundary=True)
        with patch.object(benchmark.time, "perf_counter", side_effect=[0, 1, 2, 3, 4, 5]):
            result = probe.sample(Scenario(), outer, ui=True)
        stages = result["stages_seconds"]
        self.assertEqual(stages["interno"], 1)
        self.assertEqual(stages["externo"], 3)
        self.assertEqual(stages["backend_total_sem_sobreposicao"], 3)
        self.assertEqual(stages["execucao_UI_fora_servicos"], 2)
        self.assertEqual(result["seconds"], 5)

    def test_overlapping_threads_do_not_inflate_wall_time(self):
        """União temporal elimina interseções e recorta intervalos fora da amostra."""
        probe = benchmark.Probe()
        probe._backend_intervals.extend([(1, 4), (3, 6), (8, 12), (-2, 2)])
        self.assertEqual(probe.backend_seconds(0, 10), 8)

    def test_new_sample_has_no_previous_intervals(self):
        """Reruns não herdam o tempo de backend de outra ação ou usuário."""
        probe = benchmark.Probe()
        probe._backend_intervals.append((0, 100))
        with patch.object(benchmark.time, "perf_counter", side_effect=[20, 23]):
            result = probe.sample(Scenario(), lambda: None, ui=True)
        self.assertEqual(result["stages_seconds"]["backend_total_sem_sobreposicao"], 0)
        self.assertEqual(result["stages_seconds"]["execucao_UI_fora_servicos"], 3)


if __name__ == "__main__":
    unittest.main()
