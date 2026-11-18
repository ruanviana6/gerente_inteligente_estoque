"""Diagnóstico autorizado de NF-e por GET. Nunca cadastra, emite ou persiste nota."""

from __future__ import annotations

import hmac
from typing import Any
from urllib.parse import urlencode

from integracoes.account_setup import (
    ContaGestaoClick,
    _configuracao,
    _vinculo_configuracao,
    validar_conta,
)
from integracoes.diagnostico import _stamp
from integracoes.gestaoclick import (
    ContratoInvalido,
    GestaoClickError,
    TransporteConsulta,
    TransporteHTTPS,
    parse_json_seguro,
)
from sincronizacao.service import RPCAuth
from web.errors import AuthorizationError


def consultar_recurso_nfe(
    auth: RPCAuth,
    carregar_configuracao,
    conta: ContaGestaoClick,
    loja_id: str,
    *,
    autorizado: bool,
    transporte: TransporteConsulta | None = None,
) -> dict[str, Any]:
    """Executa uma única chamada de leitura e descarta integralmente o conteúdo.

    GET /notas_fiscais_produtos é documentado publicamente, mas um 200 não
    comprova que a credencial tenha acesso a todas as notas da loja nem que
    o cadastro por POST esteja habilitado. Esse teste não ativa operações fiscais.
    """
    with auth.operation():
        stamp = _stamp(auth)
        if autorizado is not True:
            raise AuthorizationError("Confirme explicitamente a consulta fiscal somente leitura.")
        validar_conta(auth, conta)
        if (not isinstance(loja_id, str) or loja_id not in {loja["id"] for loja in conta.lojas}):
            raise AuthorizationError("A loja fiscal não pertence ao catálogo autorizado.")
        config = _configuracao(auth, carregar_configuracao, stamp, True)
        if not hmac.compare_digest(conta._config_stamp, _vinculo_configuracao(config)):
            raise AuthorizationError("A configuração de acesso mudou. Consulte as lojas novamente.")
        base = config.validar_conexao()

        def revalidar() -> None:
            validar_conta(auth, conta)
            atual = _configuracao(auth, carregar_configuracao, stamp, True)
            if atual != config:
                raise AuthorizationError("A configuração fiscal mudou durante a consulta.")

        url = base + "/notas_fiscais_produtos?" + urlencode({
            "loja_id": loja_id, "pagina": 1, "limite": 1,
        })
        revalidar()
        try:
            resposta = (transporte or TransporteHTTPS()).get(url, {
                "access-token": config.access_token,
                "secret-access-token": config.secret_access_token,
                "Accept": "application/json",
            }, min(config.timeout_seconds, 15))
        except Exception:
            revalidar()
            raise GestaoClickError("Consulta fiscal indisponível. Nenhuma nota foi alterada.") from None
        revalidar()
        if resposta.status in (401, 403):
            raise GestaoClickError("A consulta de NF-e foi recusada por autenticação ou permissão.")
        if resposta.status == 429:
            raise GestaoClickError("Limite de requisições da API atingido.")
        if type(resposta.status) is not int or resposta.status != 200:
            raise GestaoClickError("O recurso fiscal não respondeu com sucesso. Nenhuma nota foi alterada.")
        payload = parse_json_seguro(resposta.body)
        rows = payload.get("data")
        if (type(payload.get("code")) is not int or payload["code"] != 200 or
                payload.get("status") != "success" or not isinstance(rows, list) or
                len(rows) > 1 or any(not isinstance(row, dict) for row in rows)):
            raise ContratoInvalido("Formato do retorno de NF-e divergente; leitura não comprovada.")
        # Não retorna nem guarda linha, chave, nota, destinatário, valor ou metadados brutos.
        return {
            "get_autorizado": True,
            "recurso": "notas_fiscais_produtos",
            "loja_solicitada": loja_id,
            "itens_amostra": len(rows),
            "loja_efetiva_comprovada": False,
            "cobertura_comprovada": False,
            "escrita_habilitada": False,
            "resultado": "GET_RESPONDEU_SEM_HOMOLOGAR_CADASTRO",
        }
