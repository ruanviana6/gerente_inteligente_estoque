"""Mede código real com dados fictícios, distinguindo rede simulada de produção.

Execute o mesmo arquivo com --repository apontando para cada checkout. O modo
ui usa AppTest e percorre a interface real; service isola o custo do backend.
Nenhum resultado contém token, identidade, catálogo ou valor financeiro.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import platform
import statistics
import sys
import threading
import time
from collections import Counter
from contextlib import nullcontext
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], fraction: float) -> float:
    """Interpola percentis, preservando inclusive a amostra de primeira carga."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    left = int(position)
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def summarize(samples: list[dict]) -> dict:
    """Resume durações e chamadas sem esconder a variação entre execuções."""
    durations = [sample["seconds"] for sample in samples]
    keys = sorted({key for sample in samples for key in sample["calls"]})
    stages = sorted({key for sample in samples for key in sample["stages_seconds"]})
    return {
        "samples": len(samples), "first_seconds": durations[0],
        "p50_seconds": statistics.median(durations), "p95_seconds": percentile(durations, 0.95),
        "calls_per_sample": {key: [sample["calls"].get(key, 0) for sample in samples] for key in keys},
        "stage_p50_seconds": {
            key: statistics.median(sample["stages_seconds"].get(key, 0) for sample in samples)
            for key in stages
        },
        "raw_seconds": durations,
    }


class Probe:
    """Observa fronteiras reais sem mudar resultados nem ignorar autorização."""

    def __init__(self):
        """Prepara contadores exclusivos desta execução de benchmark."""
        self.times = Counter()
        self.counts = Counter()
        self._local = threading.local()
        self._lock = threading.Lock()
        self._backend_intervals = []

    def instrument(self, original, stage, *, boundary=False):
        """Observa a função sem somar novamente serviços aninhados na mesma thread."""
        def measured(*args, **kwargs):
            """Registra apenas etapa, duração e fronteiras temporais internas."""
            started = time.perf_counter()
            depth = getattr(self._local, "depth", 0)
            if boundary:
                self._local.depth = depth + 1
            with self._lock:
                self.counts[stage] += 1
            try:
                return original(*args, **kwargs)
            finally:
                ended = time.perf_counter()
                if boundary:
                    self._local.depth = depth
                with self._lock:
                    self.times[stage] += ended - started
                    if boundary and depth == 0:
                        self._backend_intervals.append((started, ended))
        return measured

    def wrap(self, owner, name, stage, *, boundary=False):
        """Cronometra uma função real, propagando seu resultado e suas exceções."""
        setattr(owner, name, self.instrument(getattr(owner, name), stage, boundary=boundary))

    def backend_seconds(self, started, ended):
        """Soma a união dos intervalos de backend, sem contar sobreposições entre threads."""
        total = 0.0
        previous_end = started
        for beginning, finish in sorted(self._backend_intervals):
            beginning = max(beginning, started, previous_end)
            finish = min(finish, ended)
            if finish > beginning:
                total += finish - beginning
                previous_end = finish
        return total

    def reset(self):
        """Descarta números da execução anterior sem alterar a sessão observada."""
        self.times.clear()
        self.counts.clear()
        self._backend_intervals.clear()

    def sample(self, scenario, action, *, ui=False):
        """Mede uma ação completa e separa transporte de etapas locais inclusivas."""
        scenario.reset_counts()
        self.reset()
        started = time.perf_counter()
        action()
        ended = time.perf_counter()
        backend = self.backend_seconds(started, ended)
        self.times["backend_total_sem_sobreposicao"] = backend
        self.times["execucao_UI_fora_servicos" if ui else "execucao_fora_servicos"] = max(
            0.0, ended - started - backend,
        )
        return {
            "seconds": ended - started,
            "calls": dict(scenario.calls + self.counts),
            "stages_seconds": {**self.times, **scenario.http_seconds},
        }


def load_fixture(repository: Path):
    """Seleciona o checkout alvo antes de importar qualquer módulo do aplicativo."""
    sys.path.insert(0, str(repository))
    spec = importlib.util.spec_from_file_location(
        "flatter_fixture", SCRIPT_ROOT / "tests" / "fixtures" / "flatter_data.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def install_probe(fixture) -> Probe:
    """Instrumenta os mesmos pontos na versão original e na versão candidata."""
    import web.reports
    import web.service

    probe = Probe()
    probe.wrap(web.service, "gerar_painel", "motor")
    probe.wrap(web.reports, "gerar_painel", "motor")
    probe.wrap(web.service, "normalizar_payload", "normalizacao_inclui_motor")
    probe.wrap(fixture.Scenario, "service_factory", "service_factory", boundary=True)
    probe.wrap(web.service.Service, "context", "contexto_backend", boundary=True)
    probe.wrap(web.service.Service, "load_inventory", "carregamento_backend", boundary=True)
    probe.wrap(web.service.Service, "export_replenishment", "export_reposicao", boundary=True)
    probe.wrap(web.service.Service, "export_monthly_report", "export_relatorio", boundary=True)
    probe.wrap(web.service.Service, "monthly_report", "relatorio", boundary=True)
    if hasattr(web.service.Service, "deferred_export"):
        probe.wrap(web.service.Service, "deferred_export", "preparar_download", boundary=True)
    return probe


def measure_actions(fixture, probe, size, iterations, latency):
    """Mede trabalho solicitado, separado do simples acesso à página.

    A geração usa o serviço real. Os CSVs executam o mesmo callback diferido
    usado pelo botão, quando disponível; na base, executam o exportador imediato.
    Esses tempos não incluem renderização, pintura ou transferência do navegador.
    """
    scenario = fixture.Scenario(size=size, latency_ms=latency)
    service = scenario.service_factory({})
    month = "2026-09-01"

    def generate():
        """Gera efetivamente um fechamento fictício e verifica o período retornado."""
        with service.execution() if hasattr(service, "execution") else nullcontext():
            report = service.monthly_report(month)
            assert report["periodo"]["inicio"] == month
            assert report["mais_vendidos"]

    if hasattr(service, "deferred_export"):
        # O vínculo nasce durante a renderização com o contexto já consultado.
        # Cada invocação abaixo percorre novamente as verificações do callback.
        context = service.context()
        monthly = service.deferred_export("monthly", {"month": month}, context=context)
        replenishment = service.deferred_export("replenishment", {}, context=context)
        mechanism = "Callback diferido real do botão, invocado sem navegador"
    else:
        def monthly():
            """Executa o mesmo exportador imediato utilizado pela interface anterior."""
            return service.export_monthly_report(month)

        replenishment = service.export_replenishment
        mechanism = "Exportador imediato da versão anterior, invocado sem navegador"
    monthly = probe.instrument(monthly, "callback_csv_mensal", boundary=True)
    replenishment = probe.instrument(replenishment, "callback_csv_reposicao", boundary=True)

    def export(callback):
        """Confirma que o exportador produziu bytes, sem registrar conteúdo do CSV."""
        content = callback()
        assert isinstance(content, bytes) and len(content) > 0

    actions = {
        "gerar_relatorio_mensal": generate,
        "exportar_relatorio_csv": lambda: export(monthly),
        "exportar_reposicao_csv": lambda: export(replenishment),
    }
    return {
        "scope": "Ações completas do backend; sem transferência ou renderização no navegador",
        "export_mechanism": mechanism,
        "measurements": {
            name: summarize([probe.sample(scenario, action) for _ in range(iterations)])
            for name, action in actions.items()
        },
    }


def measure_service(fixture, probe, size, iterations, latency):
    """Mede o backend completo; não apresenta esses tempos como renderização UI."""
    scenario = fixture.Scenario(size=size, latency_ms=latency)
    service = scenario.service_factory({})

    def load():
        """Executa contexto e leitura com escopo seguro quando a versão o oferece."""
        with service.execution() if hasattr(service, "execution") else nullcontext():
            service.context()
            result = service.load_inventory("dashboard.view")
            assert len(result["panel"]) == size

    samples = [probe.sample(scenario, load) for _ in range(iterations)]
    return {
        "dashboard_backend": summarize(samples),
        "acoes_backend": measure_actions(fixture, probe, size, iterations, latency),
    }


def measure_ui(fixture, probe, size, iterations, latency):
    """Aciona a aplicação Streamlit real, incluindo seus fragmentos iniciais."""
    from streamlit.testing.v1 import AppTest

    fixture.active_scenario = fixture.Scenario(size=size, latency_ms=latency)
    scenario = fixture.active_scenario
    app = AppTest.from_string(
        "from flatter_fixture import active_scenario\n"
        "from web.ui import run_app\n"
        "run_app(service_factory=active_scenario.service_factory)\n",
        default_timeout=180,
    )

    def execute():
        """Falha se a aplicação exibir exceção ou negar indevidamente o cenário Master."""
        app.run()
        if app.exception:
            raise AssertionError("A interface falhou durante o benchmark: " + str(app.exception[0].message))
        if app.error:
            raise AssertionError("A interface exibiu erro durante o benchmark: " + str(app.error[0].value))

    results = {"primeira_carga": summarize([probe.sample(scenario, execute, ui=True)])}
    for name in ("Painel", "Estoque", "Reposição", "Relatórios"):
        navigation = next(item for item in app.sidebar.radio if item.label == "Navegação")
        navigation.set_value(name)
        samples = [probe.sample(scenario, execute, ui=True) for _ in range(iterations)]
        results[name] = summarize(samples)
    generate_buttons = [item for item in app.button if item.label == "Gerar relatório"]
    if generate_buttons:
        def generate_report():
            """Clica no formulário real, incluindo consulta, cálculo e apresentação."""
            button = next(item for item in app.button if item.label == "Gerar relatório")
            button.click()
            execute()

        results["gerar_relatorio_interface"] = summarize([
            probe.sample(scenario, generate_report, ui=True) for _ in range(iterations)
        ])
    else:
        results["gerar_relatorio_interface"] = {
            "mode": "Geração automática já incluída na entrada em Relatórios nesta versão",
        }
    results["acoes_backend"] = measure_actions(fixture, probe, size, iterations, latency)
    return results


def main():
    """Grava JSON sanitizado e identifica exatamente o ambiente e a simulação."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1200])
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--latency-ms", type=float, default=0)
    parser.add_argument("--mode", choices=["service", "ui"], default="service")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 2:
        parser.error("Use pelo menos duas repetições para calcular dispersão.")
    bootstrap = time.perf_counter()
    fixture = load_fixture(args.repository.resolve())
    probe = install_probe(fixture)
    bootstrap = time.perf_counter() - bootstrap
    versions = {}
    for package in ("pandas", "numpy", "streamlit"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "indisponível"
    result = {
        "mode": args.mode, "python": platform.python_version(), "platform": platform.platform(),
        "versions": versions, "bootstrap_import_seconds": bootstrap,
        "network": "Transporte fictício local; sem conexão com Supabase/Streamlit Cloud",
        "simulated_latency_per_request_ms": args.latency_ms,
        "timing_note": "Etapas são inclusivas; não somar motor com normalização nem relatório com exportação.",
        "ui_timing_note": (
            "execucao_UI_fora_servicos = tempo AppTest menos união das fronteiras de serviços. "
            "Inclui Python da interface, Streamlit, serialização e coordenação do AppTest; "
            "não mede pintura nem rede do navegador. A primeira carga também inclui importação da UI. "
            "backend_total_sem_sobreposicao elimina chamadas aninhadas e intervalos concorrentes; "
            "service_factory mede criação/recuperação do serviço com a mesma fronteira nas duas versões."
        ),
        "navigation_note": (
            "Acesso à página e ação solicitada são medidos separadamente. Adiar cálculo não elimina seu custo. "
            "AppTest não mede pintura do navegador; ações_backend não medem transferência de arquivos."
        ),
        "datasets": {},
    }
    measure = measure_service if args.mode == "service" else measure_ui
    for size in args.sizes:
        result["datasets"][str(size)] = measure(fixture, probe, size, args.iterations, args.latency_ms)
        print(f"Amostra fictícia concluída: {size} SKUs, modo {args.mode}.", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
