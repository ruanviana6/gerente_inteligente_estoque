"""Solicitações fiscais via JWT e executor isolado; sem credenciais do GestãoClick."""

import re
from uuid import UUID

from fiscal.regras import EscolhasFiscais
from web.errors import AuthorizationError, ServiceUnavailable


def _identificador(valor):
    """Exige UUID canônico antes de enviar um identificador ao backend."""
    try:
        return str(UUID(str(valor)))
    except (TypeError, ValueError, AttributeError):
        raise ValueError('Identificador fiscal inválido.') from None


class FiscalService:
    """Revalida acesso antes e depois de cada consulta ou solicitação."""

    def __init__(self, auth):
        """Usa exclusivamente a instância Auth da sessão atual."""
        self.auth = auth

    def _validar_contexto(self, original, atual):
        """Não entrega uma resposta caso a identidade ou as permissões tenham mudado."""
        if original.profile != atual.profile or original.permissions != atual.permissions:
            raise AuthorizationError('O acesso fiscal mudou. Atualize a página.')

    def _rpc(self, nome, dados, permissao='fiscal.view'):
        """Centraliza autorização renovada e mantém a identidade durante a operação."""
        with self.auth.operation():
            original = self.auth.require(permissao)
            if permissao in {'fiscal.create_request', 'fiscal.approve_pending'}:
                self.auth.require('fiscal.view')
            resposta = self.auth.rpc(nome, dados, permissao)
            self._validar_contexto(original, self.auth.require(permissao))
            if not isinstance(resposta, (dict, list)):
                raise ServiceUnavailable('Resposta fiscal indisponível. Consulte o histórico.')
            return resposta

    def consultar(self, numero):
        """Consulta somente o catálogo DEMO explícito; não acessa o ERP."""
        if not isinstance(numero, str) or not re.fullmatch(r'[0-9]{1,20}', numero):
            raise ValueError('Informe um número de pedido válido.')
        return self._rpc('fiscal_order', {'p_number': numero})

    def preparar(self, numero, frete, contribuinte):
        """Registra escolhas manuais numa prévia identificada e imutável."""
        escolhas = EscolhasFiscais(frete, contribuinte)
        if not isinstance(numero, str) or not re.fullmatch(r'[0-9]{1,20}', numero):
            raise ValueError('Informe um número de pedido válido.')
        return self._rpc('fiscal_preview', {'p_number': numero, 'p_choices': escolhas.registro()}, 'fiscal.create_request')

    def previa(self, identificador):
        """Recupera a prévia do próprio operador, sem guardar payload em SessionState."""
        return self._rpc('fiscal_preview_get', {'p_preview': _identificador(identificador)}, 'fiscal.create_request')

    def confirmar(self, identificador, confirmado, aprovar=False):
        """A confirmação não aceita identidade, organização ou aprovador do navegador."""
        if type(confirmado) is not bool or not confirmado or type(aprovar) is not bool:
            raise ValueError('A confirmação explícita da simulação é obrigatória.')
        return self._rpc('fiscal_confirm', {'p_preview': _identificador(identificador), 'p_confirm': confirmado,
                                          'p_approve_pending': aprovar}, 'fiscal.create_request')

    def listar(self, pagina=0):
        """Lê no máximo 25 solicitações; o banco decide quem pode ver cada registro."""
        if type(pagina) is not int or not 0 <= pagina <= 200:
            raise ValueError('Página fiscal inválida.')
        return self._rpc('fiscal_list', {'p_offset': pagina * 25})

    def aprovar(self, identificador, confirmado):
        """Vincula a aprovação de Em andamento à identidade Auth atual."""
        if confirmado is not True:
            raise ValueError('Confirme a aprovação do pedido Em andamento.')
        return self._rpc('fiscal_approve', {'p_request': _identificador(identificador), 'p_confirm': True}, 'fiscal.approve_pending')

    def auditar(self, pagina=0):
        """Oferece ao auditor uma página de metadados, sem ampliar a navegação fiscal."""
        if type(pagina) is not int or not 0 <= pagina <= 200:
            raise ValueError('Página fiscal inválida.')
        return self._rpc('fiscal_audit_list', {'p_offset': pagina * 25}, 'fiscal.audit')

    def historico(self, identificador):
        """Consulta transições imutáveis somente com permissão fiscal de auditoria."""
        return self._rpc('fiscal_history', {'p_request': _identificador(identificador)}, 'fiscal.audit')

    def estado(self):
        """Consulta se a demonstração está habilitada, preservando o histórico em OFF."""
        return self._rpc('fiscal_status', {})

    def configurar(self, habilitado):
        """Permite ao Master desligar a DEMO; não existe opção de ligar emissão real."""
        if type(habilitado) is not bool:
            raise ValueError('Configuração fiscal inválida.')
        return self._rpc('fiscal_configure', {'p_demo_enabled': habilitado}, 'fiscal.manage')

    def configuracao(self):
        """Expõe apenas o modo bloqueado, sem tokens ou configuração secreta."""
        return self._rpc('fiscal_settings', {}, 'fiscal.manage')

    def executar(self, identificador, reconciliar=False):
        """Aciona o executor apenas após uma ação explícita, sem chamar o ERP."""
        if type(reconciliar) is not bool:
            raise ValueError('Ação fiscal inválida.')
        with self.auth.operation():
            original = self.auth.require('fiscal.create_request')
            self.auth.require('fiscal.view')
            resposta = self.auth.api.fiscal_demo(_identificador(identificador), reconciliar,
                                                self.auth.session.access_token)
            self._validar_contexto(original, self.auth.require('fiscal.create_request'))
            return resposta
