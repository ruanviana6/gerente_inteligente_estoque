"""Executa o scheduler externo; simulação é o comportamento padrão."""
from __future__ import annotations

import argparse
import json
import os
from datetime import date

from web.api import SupabaseAPI
from web.email_delivery import ProvedorResend, RepositorioRPC, executar_relatorios
from web.errors import AppError
from web.reports import mes_anterior
from web.settings import Settings


def main() -> int:
    """Lê somente secrets do executor e imprime contagens, nunca identidades ou tokens."""
    parser = argparse.ArgumentParser(description="Gerar relatórios mensais com entrega controlada.")
    parser.add_argument("--month", type=date.fromisoformat, default=mes_anterior(), help="Mês: AAAA-MM-01")
    parser.add_argument("--real", action="store_true", help="Solicitar envio real, sujeito às duas autorizações.")
    argumentos = parser.parse_args()
    if argumentos.month.day != 1:
        parser.error("O mês deve ser informado pelo primeiro dia.")
    try:
        api = SupabaseAPI(Settings.from_mapping(os.environ))
        fila = RepositorioRPC(api, os.environ.get("REPORT_JOB_SECRET", ""))
        permitir = os.environ.get("REPORT_ALLOW_REAL_EMAIL", "false").strip().lower() == "true"
        provedor = None
        if argumentos.real and permitir:
            provedor = ProvedorResend(os.environ.get("RESEND_API_KEY", ""), os.environ.get("REPORT_EMAIL_FROM", ""))
        resultado = executar_relatorios(
            fila, argumentos.month, simular=not argumentos.real, provedor=provedor,
            permitir_envio_real=permitir, app_url=os.environ.get("REPORT_APP_URL"),
        )
        print(json.dumps(resultado, ensure_ascii=False))
        return 1 if resultado["falhas"] or resultado["incertos"] or resultado["bloqueados"] else 0
    except (AppError, ValueError):
        print("Rotina não executada: confira as configurações protegidas e a disponibilidade do serviço.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
