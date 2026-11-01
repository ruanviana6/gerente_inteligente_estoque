"""Erros seguros para apresentação, sem corpos HTTP, tokens ou detalhes internos."""


class AppError(Exception):
    """Representa uma falha esperada cujo texto pode ser mostrado ao usuário."""


class ConfigurationError(AppError):
    """Indica configuração ausente ou incompatível com o projeto autorizado."""


class AuthenticationError(AppError):
    """Solicita nova autenticação sem revelar existência de uma conta."""


class AuthorizationError(AppError):
    """Nega uma operação quando o perfil ou a permissão vigente não permite."""


class ServiceUnavailable(AppError):
    """Informa indisponibilidade externa sem expor resposta ou credenciais."""


class RateLimited(AppError):
    """Sinaliza bloqueio temporário de tentativas de acesso."""
