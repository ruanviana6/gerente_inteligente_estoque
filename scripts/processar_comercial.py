"""Prepara resultados de arquivo minimizado, sem consultas ao GestãoClick."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from analytics.commercial_pipeline import preparar_publicacao  # noqa: E402


def executar(args):
    """Valida uma cópia autorizada e publica apenas com opção explícita do Master."""
    if args.entrada.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Arquivo excede 64 MiB.")
    linhas = json.loads(args.entrada.read_text(encoding="utf-8"))
    preparado = preparar_publicacao(linhas, args.inicio, args.fim, source=args.fonte, store=args.loja,
        coverage_start=args.cobertura_inicio, coverage_end=args.cobertura_fim, city_basis=args.cidade)
    if not args.publicar:
        print(json.dumps({"simulacao": True, "insights": len(preparado["items"]), "hash": preparado["content_hash"]}))
        return
    if not args.contrato_validado or not args.dados_confirmados:
        raise ValueError("A publicação exige contrato, finalidade e abrangência conferidos.")
    from web.api import SupabaseAPI
    from web.auth import AuthService
    from web.service import Service
    from web.settings import Settings

    # A chave comum e o JWT individual são suficientes. Nunca se usa service_role
    # para publicar analytics ou contornar as políticas de autorização.
    auth = AuthService(SupabaseAPI(Settings.from_mapping(os.environ)))
    try:
        contexto = auth.login(os.environ.get("COMMERCIAL_MASTER_EMAIL", ""), os.environ.get("COMMERCIAL_MASTER_PASSWORD", ""))
        if contexto is None or contexto.profile.role != "Master":
            raise ValueError("É necessário um Master ativo com primeiro acesso concluído.")
        resultado = Service(auth).publish_commercial_batch(preparado)
        print(json.dumps({"publicado": True, "reutilizado": resultado.get("reused", False)}))
    finally:
        auth.logout(scope="local")


def main():
    """Não revela linhas, credenciais, identidades ou erros internos no terminal."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entrada", type=Path, required=True)
    parser.add_argument("--fonte", required=True)
    parser.add_argument("--loja", required=True)
    parser.add_argument("--inicio", type=date.fromisoformat, required=True)
    parser.add_argument("--fim", type=date.fromisoformat, required=True)
    parser.add_argument("--cobertura-inicio", type=date.fromisoformat, required=True)
    parser.add_argument("--cobertura-fim", type=date.fromisoformat, required=True)
    parser.add_argument("--cidade", choices=["delivery", "registration", "billing", "unknown"], required=True)
    parser.add_argument("--publicar", action="store_true")
    parser.add_argument("--contrato-validado", action="store_true")
    parser.add_argument("--dados-confirmados", action="store_true")
    try:
        executar(parser.parse_args())
    except Exception:
        print("Processamento recusado. Confira arquivo, escopo, cobertura e autorização. Nenhum detalhe sensível foi registrado.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
