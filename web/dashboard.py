"""Prepara resumos compactos somente em processos explícitos, nunca na navegação."""

import json
from datetime import date, timedelta

import pandas as pd

from motor import somar_valores_financeiros
from reposicao.apresentacao import projetar_painel


def preparar_resumo(dados, regras):
    """Reutiliza o motor auditado e os ciclos; não inventa demanda ou embalagem."""
    painel, vendas = dados['panel'], dados['sales']
    meta = dados['metadata']
    referencia = date.fromisoformat(meta['snapshot_date'])
    configuracao = regras.get('configuration', {})
    primary = int(configuracao.get('cycle_primary', 14))
    alternative = int(configuracao.get('cycle_alternative', 21))
    ciclos, proxima, desconhecidos = [], [], 0
    for ciclo in (primary, alternative):
        projecao = projetar_painel(painel, regras, referencia, ciclo)
        conhecidos = projecao['caixas'].notna()
        desconhecidos = max(desconhecidos, int((~conhecidos).sum()))
        caixas = int(projecao.loc[conhecidos, 'caixas'].sum()) if conhecidos.all() and len(projecao) else None
        ciclos.append({'days': ciclo, 'boxes': caixas})
        if ciclo == primary:
            proxima = [r['solicitar_ate'] for r in projecao.to_dict('records')
                       if r.get('solicitar_ate') and r.get('unidades') and not pd.isna(r['unidades'])]
    fim = date.fromisoformat(meta['period_end'])
    inicio = date.fromisoformat(meta['period_start'])
    datas = pd.to_datetime(vendas['data']) if not vendas.empty else pd.Series([], dtype='datetime64[ns]')
    diaria = (vendas.assign(data=datas.dt.normalize()).groupby('data')['quantidade'].sum()
              if not vendas.empty else pd.Series(index=pd.DatetimeIndex([]), dtype='float64'))
    serie = [{'date': (fim-timedelta(days=i)).isoformat(), 'units': int(diaria.get(pd.Timestamp(fim-timedelta(days=i)), 0))}
             for i in range(29, -1, -1)] if inicio <= fim-timedelta(days=29) else []
    atual = sum(r['units'] for r in serie) if serie else None
    anterior = int(diaria.loc[(diaria.index >= pd.Timestamp(fim-timedelta(days=59))) &
                             (diaria.index <= pd.Timestamp(fim-timedelta(days=30)))].sum()) if inicio <= fim-timedelta(days=59) else None
    variacao = ((atual-anterior)/anterior*100) if atual is not None and anterior else None
    estoque = {
        'products': len(painel), 'stockouts': int(painel['status'].eq('Ruptura').sum()),
        'risk': int(painel['risco_prazo'].sum()), 'review': int(painel['requer_revisao'].sum()),
        'excess': int(painel['status'].isin(['Parado', 'Excesso']).sum()),
        'suggested': int(painel['quantidade_sugerida'].gt(0).sum()),
    }
    prioridades = [{'kind': kind, 'count': estoque[campo], 'priority': prioridade}
                   for kind, campo, prioridade in [('risk', 'risk', 0), ('stockout', 'stockouts', 0),
                      ('production', 'suggested', 1), ('excess', 'excess', 3), ('quality', 'review', 4)] if estoque[campo]]
    resultado = {'schema': 1, 'mode': 'demo' if 'demo' in str(meta.get('source', '')).lower() else 'unclassified',
        'inventory': estoque, 'replenishment': {'boxes': ciclos[0]['boxes'], 'unknown_packaging': desconhecidos,
            'primary': primary, 'alternative': alternative, 'reference_boxes': int(configuracao.get('reference_boxes', 80)),
            'next_request': min(proxima) if proxima else None, 'cycles': ciclos},
        'sales': {'current_30': atual, 'previous_30': anterior, 'change_pct': variacao, 'daily': serie},
        'priorities': prioridades[:5], 'finance': {'capital': float(somar_valores_financeiros(painel['capital_estoque'])) if 'capital_estoque' in painel and painel['capital_estoque'].notna().any() else None, 'partial': bool(painel['capital_estoque'].isna().any()) if 'capital_estoque' in painel else True}}
    # Limite pequeno e JSON estrito protegem a cota e recusam valores não finitos.
    codificado = json.dumps(resultado, allow_nan=False).encode()
    if len(codificado) > 32768:
        raise ValueError('O resumo excedeu o limite de 32 KiB.')
    return resultado
