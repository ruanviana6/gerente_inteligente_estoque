"""Executa uma janela de sincronização autorizada fora da navegação Streamlit."""

from __future__ import annotations

import argparse
import json
import os

from integracoes.gestaoclick import parse_json_seguro
from sincronizacao.automatic import executar_sincronizacao, validar_configuracao
from web.api import SupabaseAPI


def main(argv: list[str] | None = None) -> int:
    """Valida por padrão; --run libera um único job aprovado e sem escrita no ERP."""
    parser = argparse.ArgumentParser(description="Sincronização GestãoClick somente leitura e em lotes.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Validar configuração sem rede nem reserva de lote.")
    mode.add_argument("--run", action="store_true", help="Executar uma janela autorizada pelo Master.")
    args = parser.parse_args(argv)
    try:
        raw = os.environ.get("SYNC_JOB_CONFIG_JSON", "")
        values = parse_json_seguro(raw)
        configuration = validar_configuracao(values)
        if not args.run:
            result = {"status": "configuration_valid", "network_accessed": False}
        else:
            result = executar_sincronizacao(SupabaseAPI(configuration.settings), configuration)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["status"] == "failed" else 0
    except Exception:
        print(json.dumps({"status": "invalid_configuration", "error_code": "SYNC_CONFIGURATION_INVALID"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
