"""Regras puras compartilhadas por contrato com a preparação Python da automação."""

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

CONTRATO = json.loads(Path(__file__).with_name('contrato_v2.json').read_text())
MODALIDADES_FRETE = {int(k): v for k, v in CONTRATO['modalidades_frete'].items()}
TIPOS_CONTRIBUINTE = {int(k): v for k, v in CONTRATO['tipos_contribuinte'].items()}
ESTADOS = {
    'awaiting_approval': 'Aguardando aprovação', 'confirmed': 'Confirmada',
    'processing': 'Cadastro em processamento', 'authorized': 'Resultado legado (DEMO)',
    'registered': 'Cadastrada (DEMO), emissão manual no ERP',
    'rejected': 'Cadastro recusado (DEMO)', 'needs_reconciliation': 'Necessita conferência',
    'blocked': 'Bloqueada',
}


@dataclass(frozen=True)
class EscolhasFiscais:
    """Preserva códigos internos sem inferir a condição fiscal do destinatário."""

    modalidade_frete: int
    tipo_contribuinte: int

    def __post_init__(self):
        """Recusa ausência, booleanos, floats e códigos desconhecidos."""
        if type(self.modalidade_frete) is not int or self.modalidade_frete not in MODALIDADES_FRETE:
            raise ValueError('Selecione uma modalidade de frete válida, sem valor automático.')
        if type(self.tipo_contribuinte) is not int or self.tipo_contribuinte not in TIPOS_CONTRIBUINTE:
            raise ValueError('Selecione o tipo de contribuinte validado do destinatário.')

    def registro(self):
        """Retorna somente os dois códigos necessários à prévia imutável."""
        return {'modalidade_frete': self.modalidade_frete, 'tipo_contribuinte': self.tipo_contribuinte}


def normalizar_uf(valor):
    """Reconhece exclusivamente CE/Ceará/Ceara após normalização Unicode."""
    if not isinstance(valor, str):
        return None
    texto = ''.join(c for c in unicodedata.normalize('NFKD', valor.strip()) if not unicodedata.combining(c)).upper()
    return 'CE' if texto in {'CE', 'CEARA'} else None


def elegibilidade(pedido, autorizacao=False):
    """Explica a elegibilidade observada sem substituir a validação SQL."""
    if not isinstance(pedido, dict) or normalizar_uf(pedido.get('uf')) != 'CE':
        return False, 'Destino fora do Ceará ou não informado.'
    if pedido.get('nota_existente') or pedido.get('solicitacao_existente'):
        return False, 'Já existe nota ou solicitação para este pedido.'
    if pedido.get('cenario') == 'incompleto':
        return False, 'Cadastro ou informação fiscal incompleta: revisão obrigatória.'
    status = str(pedido.get('situacao', '')).strip().casefold()
    if status in {'em separação', 'concretizada'}:
        return True, 'Elegível apenas para a demonstração; emissão real está bloqueada.'
    if status == 'em andamento':
        return bool(autorizacao), 'Exige aprovação explícita de usuário com fiscal.approve_pending.'
    return False, 'Situação do pedido não elegível.'
