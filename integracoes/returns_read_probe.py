"""Diagnóstico de devoluções por GET; sem persistência, conciliação ou escrita."""

from __future__ import annotations

import hmac
from datetime import date
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
from integracoes.native_preview import _hoje
from sincronizacao.service import RPCAuth
from web.errors import AuthorizationError


def consultar_recurso_devolucoes(
    auth: RPCAuth,
    carregar_configuracao,
    conta: ContaGestaoClick,
    loja_id: str,
    inicio: date,
    fim: date,
    *,
    autorizado: bool,
    transporte: TransporteConsulta | None = None,
) -> dict[str, Any]:
    """Uma página de diagnóstico; não presume esquema dos itens nem cobertura."""
    with auth.operation():
        stamp = _stamp(auth)
        if autorizado is not True:
            raise AuthorizationError("Confirme explicitamente a consulta de devoluções somente leitura.")
        validar_conta(auth, conta)
        if (not isinstance(loja_id, str) or loja_id not in {loja["id"] for loja in conta.lojas}):
            raise AuthorizationError("A loja não pertence ao catálogo autorizado.")
        if (type(inicio) is not date or type(fim) is not date or
                inicio > fim or (fim - inicio).days >= 7 or fim > _hoje()):
            raise ContratoInvalido("Escolha até sete dias inclusivos, sem datas futuras.")
        config = _configuracao(auth, carregar_configuracao, stamp, True)
        if not hmac.compare_digest(conta._config_stamp, _vinculo_configuracao(config)):
            raise AuthorizationError("A configuração de acesso mudou. Consulte as lojas novamente.")
        base = config.validar_conexao()

        def revalidar() -> None:
            validar_conta(auth, conta)
            atual = _configuracao(auth, carregar_configuracao, stamp, True)
            if atual != config:
                raise AuthorizationError("A configuração mudou durante a consulta.")

        url = base + "/devolucoes?" + urlencode({
            "loja_id": loja_id, "pagina": 1, "limite": 1,
            "data_inicio": inicio.isoformat(), "data_fim": fim.isoformat(),
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
            raise GestaoClickError("Consulta de devoluções indisponível. Nenhum registro foi alterado.") from None
        revalidar()
        if resposta.status in (401, 403):
            raise GestaoClickError("A consulta de devoluções foi recusada por autenticação ou permissão.")
        if resposta.status == 429:
            raise GestaoClickError("Limite de requisições da API atingido.")
        if type(resposta.status) is not int or resposta.status != 200:
            raise GestaoClickError("O recurso de devoluções não respondeu com sucesso. Nenhum registro foi alterado.")
        payload = parse_json_seguro(resposta.body)
        rows = payload.get("data")
        if (type(payload.get("code")) is not int or payload["code"] != 200 or
                payload.get("status") != "success" or not isinstance(rows, list) or
                len(rows) > 1 or any(not isinstance(row, dict) for row in rows)):
            raise ContratoInvalido("Formato do retorno de devoluções divergente; leitura não comprovada.")
        # Não devolve valores nem chaves arbitrárias da resposta. Só tipos de
        # candidatos conhecidos; presença não comprova significado semântico.
        campos = ("id", "loja_id", "pedido_id", "venda_id", "data",
                  "situacao_id", "produtos", "tipo")
        tipos = {str: "texto", int: "inteiro", list: "lista", dict: "objeto",
                 bool: "booleano", float: "decimal", type(None): "nulo"}
        linha = rows[0] if rows else {}
        formato = {campo: tipos.get(type(linha[campo]), "outro")
                   if campo in linha else "ausente" for campo in campos}
        valor = linha.get("loja_id")
        loja_confere = None
        if type(valor) is int and valor > 0:
            loja_confere = hmac.compare_digest(str(valor), loja_id)
        elif isinstance(valor, str) and valor.isascii() and valor.isdecimal():
            loja_confere = hmac.compare_digest(valor, loja_id)
        return {
            "recurso": "devolucoes",
            "itens_amostra": len(rows),
            "loja_no_registro_confere": loja_confere,
            "tipos_campos_candidatos": formato,
            "cobertura_comprovada": False,
            "apta_reposicao": False,
        }
