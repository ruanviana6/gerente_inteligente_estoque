"""Compara detalhes, arquivos e consulta de resumo usando apenas dados fictícios."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from analytics.commercial import pseudonimizar_cliente  # noqa: E402
from analytics.commercial_pipeline import preparar_publicacao  # noqa: E402


def gerar_cenario(linhas=100_000):
    """Cria mil clientes e 30 cidades fictícios preservando chaves de pedido/linha."""
    source, store = "30000000-0000-4000-8000-000000000011", "DEMO"
    refs = {i: pseudonimizar_cliente(source, store, str(i), b"segredo-exclusivo-ficticio-32-bytes") for i in range(1000)}
    return [{"source_id": source, "store_id": store, "document_id": f"DEMO-{i//2:06}", "line_id": str(i % 2),
             "customer_ref": refs[(i//2) % 1000], "city_code": str(2300000 + ((i//2) % 30)),
             "city_name": f"Cidade fictícia {(i//2) % 30}", "state": "CE",
             "sale_date": (date(2026, 1, 4) + timedelta(days=(i//2) % 56)).isoformat(),
             "sku": f"DEMO-{i % 2}", "quantity": (i//2) % 7 + 1, "returned_quantity": 0, "status": "completed"}
            for i in range(linhas)]


def _medir(funcao, repeticoes=3):
    """Retorna a mediana e o maior tempo observado, sem chamar rede."""
    tempos, valor = [], None
    for _ in range(repeticoes):
        inicio = time.perf_counter()
        valor = funcao()
        tempos.append((time.perf_counter()-inicio)*1000)
    return valor, {"p50_ms": round(statistics.median(tempos), 3), "p95_observado_ms": round(max(tempos), 3),
                   "repeticoes": repeticoes}


def executar(linhas=100_000):
    """Confere restauração exata e mede CPU/bytes de arquivos e resultados prontos."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    dados = gerar_cenario(linhas)
    quadro = pd.DataFrame(dados)
    csv = quadro.to_csv(index=False).encode()
    compactado, gzip_tempo = _medir(lambda: gzip.compress(csv, compresslevel=6, mtime=0))
    restaurado, gzip_leitura = _medir(lambda: pd.read_csv(io.BytesIO(gzip.decompress(compactado)), dtype=str,
                                                       keep_default_na=False))
    assert quadro.astype(str).to_dict("records") == restaurado.to_dict("records")
    tabela = pa.Table.from_pandas(quadro, preserve_index=False)
    def gravar_parquet():
        """Grava um arquivo em memória, sem bucket nem mudanças de retenção."""
        buffer = pa.BufferOutputStream()
        pq.write_table(tabela, buffer, compression="zstd", compression_level=3)
        return buffer.getvalue().to_pybytes()
    parquet, parquet_tempo = _medir(gravar_parquet)
    volta, parquet_leitura = _medir(lambda: pq.read_table(pa.BufferReader(parquet)).to_pylist())
    assert volta == dados
    parametros = dict(source=dados[0]["source_id"], store="DEMO", coverage_start=date(2026, 1, 4),
                      coverage_end=date(2026, 2, 28), city_basis="delivery")
    resultado, preparo = _medir(lambda: preparar_publicacao(dados, date(2026, 2, 1), date(2026, 2, 28), **parametros))
    # Simula exclusivamente a desserialização de uma página pronta; não mede
    # autenticação, rede, SQL remoto nem latência real do Streamlit Cloud.
    pagina = json.dumps(resultado["items"][:10], separators=(",", ":")).encode()
    _, leitura = _medir(lambda: json.loads(pagina), repeticoes=30)
    bruto = json.dumps(dados, separators=(",", ":")).encode()
    return {"cenario": {"linhas": linhas, "clientes": len(set(quadro.customer_ref)), "cidades": len(set(quadro.city_code))},
            "json_detalhado_bytes": len(bruto), "csv_bytes": len(csv), "csv_gzip_bytes": len(compactado),
            "parquet_zstd_bytes": len(parquet), "csv_sha256": hashlib.sha256(csv).hexdigest(),
            "restauracao_exata": True, "csv_gzip_escrita": gzip_tempo, "csv_gzip_leitura": gzip_leitura,
            "parquet_zstd_escrita": parquet_tempo, "parquet_zstd_leitura": parquet_leitura,
            "calculo_completo_antes": preparo, "pagina_pronta_depois": leitura, "pagina_bytes": len(pagina),
            "limites": "CPU local e dados sintéticos. p95 observado é o máximo das repetições, não SLA. "
                        "O resumo não substitui o detalhe nem permite reconstruir pedidos sozinho. Nenhum arquivo foi apagado."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--linhas", type=int, default=100_000)
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.linhas <= 100_000:
        parser.error("Use de 2 a 100.000 linhas fictícias.")
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(executar(args.linhas), indent=2, ensure_ascii=False))
    print("Benchmark fictício e restauração concluídos.")
