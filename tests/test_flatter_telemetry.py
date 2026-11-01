"""Verifica que a medição opcional nunca registre dados ou compartilhe escopos."""
from __future__ import annotations

import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from web.telemetry import measure, performance_scope


class TelemetryTests(unittest.TestCase):
    """Exercita desativação, sanitização e isolamento em callbacks concorrentes."""

    def test_disabled_by_default(self):
        """Nenhuma métrica aparece sem opção explícita no ambiente."""
        records = []
        with patch.dict("os.environ", {}, clear=True):
            with performance_scope(sink=records.append):
                with measure("motor"):
                    pass
        self.assertEqual(records, [])

    def test_records_only_approved_fields(self):
        """Exceções são propagadas, mas conteúdo sensível não entra no registro."""
        records = []
        with self.assertRaisesRegex(ValueError, "segredo-do-teste"):
            with performance_scope("export", enabled=True, sink=records.append):
                with measure("export"):
                    raise ValueError("segredo-do-teste")
        self.assertEqual(set(records[0]), {"operation", "total_ms", "stages"})
        self.assertEqual(records[0]["stages"]["export"]["count"], 1)
        self.assertNotIn("segredo-do-teste", json.dumps(records))

    def test_unknown_labels_and_stages_are_rejected(self):
        """Nomes não podem ser usados como canal acidental para identificar usuários."""
        with self.assertRaises(ValueError):
            with performance_scope("usuario@example.test", enabled=True):
                pass
        with self.assertRaises(ValueError):
            with measure("token_secreto"):
                pass

    def test_nested_scope_does_not_duplicate_logs(self):
        """Um rerun com operações internas gera um único conjunto de contadores."""
        records = []
        with performance_scope(enabled=True, sink=records.append):
            with measure("render"):
                with performance_scope("export", enabled=True, sink=records.append):
                    with measure("auth_user"):
                        pass
        self.assertEqual(len(records), 1)
        self.assertEqual(set(records[0]["stages"]), {"render", "auth_user"})

    def test_threads_have_independent_counters(self):
        """Callback de download e rerun simultâneo não misturam métricas de sessões."""
        barrier = threading.Barrier(2)

        def worker(stage):
            """Produz métricas isoladas enquanto outra thread mede etapa diferente."""
            records = []
            with performance_scope(enabled=True, sink=records.append):
                with measure(stage):
                    barrier.wait(timeout=5)
            return records[0]

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(worker, "motor")
            second = executor.submit(worker, "export")
            a, b = first.result(), second.result()
        self.assertEqual(set(a["stages"]), {"motor"})
        self.assertEqual(set(b["stages"]), {"export"})

    def test_scope_is_removed_after_failure(self):
        """Uma falha não deixa acumuladores ativos para a próxima execução."""
        records = []
        with self.assertRaises(RuntimeError):
            with performance_scope(enabled=True, sink=records.append):
                raise RuntimeError
        with measure("motor"):
            pass
        with performance_scope(enabled=True, sink=records.append):
            with measure("render"):
                pass
        self.assertEqual(records[0]["stages"], {})
        self.assertEqual(set(records[1]["stages"]), {"render"})

    def test_environment_enables_sanitized_logger(self):
        """A configuração aceita true explicitamente e usa apenas o logger técnico."""
        with patch.dict("os.environ", {"GERENTE_PERFORMANCE_LOG": "true"}):
            with self.assertLogs("gerente.desempenho") as logs:
                with performance_scope("watcher"):
                    with measure("app_context"):
                        pass
        self.assertIn('"operation": "watcher"', logs.output[0])
        self.assertNotIn("access_token", logs.output[0])


if __name__ == "__main__":
    unittest.main()
