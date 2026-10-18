"""Agente do Front Desk: Amazon Nova no Bedrock conversando com o cliente e usando o servidor MCP.

Fluxo de cada fala do cliente:
1. lê as ferramentas do servidor MCP (tools/list) e as converte para o formato da Converse API;
2. manda a conversa para o modelo no Bedrock;
3. enquanto o modelo pedir ferramentas, executa cada uma no servidor MCP (tools/call) e devolve o resultado;
4. responde com a frase final do modelo e a lista de chamadas feitas (nome, argumentos, latência).

Configuração por variáveis de ambiente:
- FRONTDESK_MODELO: um ou mais modelos/perfis de inferência separados por vírgula, em ordem de preferência
  (padrão: us.amazon.nova-lite-v1:0, us.amazon.nova-micro-v1:0, us.amazon.nova-2-lite-v1:0). Se um modelo
  estiver sem cota ou sem acesso, o agente passa para o próximo.
- FRONTDESK_MCP_URL: endereço do servidor MCP (padrão: o próprio servidor, http://127.0.0.1:PORT/mcp).
- AWS_REGION / AWS_PROFILE: Região e perfil da AWS (padrão: us-east-2 e as credenciais padrão do boto3).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import anyio

from mcp import Client

from frontdesk import agenda

try:  # Python 3.11+
    BaseExceptionGroup
except NameError:  # Python 3.10: backport instalado junto com o anyio
    from exceptiongroup import BaseExceptionGroup

MODELOS_PADRAO = "us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0,us.amazon.nova-2-lite-v1:0"
PAUSA_MODELO_S = 3600  # depois de um "sem cota", pula o modelo por 1 hora
MAX_RODADAS = 6  # limite de idas e voltas modelo -> ferramenta por fala
MAX_TURNOS_HISTORICO = 20  # mensagens guardadas por sessão
MAX_SESSOES = 200

INSTRUCOES = """Você é a recepcionista virtual da {negocio}, uma barbearia, atendendo por voz em português do Brasil.
Hoje é {hoje} e agora são {hora}.

Regras:
- Use sempre as ferramentas para consultar serviços, horários, agendar, cancelar, reagendar e ver o resumo do dia. Nunca invente horários, preços ou agendamentos.
- Cada ferramenta devolve um campo "fala". Baseie sua resposta nele, sem acrescentar informação que não veio da ferramenta.
- Antes de agendar, você precisa do nome do cliente, do serviço, do dia e do horário. Se faltar algo, pergunte só o que falta.
- Passe datas e horários para as ferramentas como o cliente falou (ex.: "sexta", "amanhã", "9h").
- Responda curto, no máximo duas frases, em tom simpático e natural para ser falado em voz alta. Sem listas, sem markdown, sem emojis.
- Se o cliente for o dono perguntando da agenda, use resumo_do_dia."""


@dataclass
class Sessao:
    mensagens: list[dict] = field(default_factory=list)
    criada_em: float = field(default_factory=time.time)


_sessoes: dict[str, Sessao] = {}


def _sessao(sessao_id: str | None) -> tuple[str, Sessao]:
    sid = sessao_id or uuid.uuid4().hex
    if sid not in _sessoes:
        if len(_sessoes) >= MAX_SESSOES:
            mais_antiga = min(_sessoes, key=lambda k: _sessoes[k].criada_em)
            _sessoes.pop(mais_antiga, None)
        _sessoes[sid] = Sessao()
    return sid, _sessoes[sid]


class ErroAgente(RuntimeError):
    """Falha do agente com mensagem pronta para mostrar na tela."""


def _raiz(e: BaseException) -> BaseException:
    """O cliente MCP roda num TaskGroup, que embrulha o erro real num ExceptionGroup."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


def explicar_erro(e: BaseException) -> str:
    e = _raiz(e)
    nome = type(e).__name__
    codigo = _codigo(e)
    login = "Rode `aws login --region us-east-2 --profile juan` e suba o servidor com AWS_PROFILE=juan."
    if nome in ("NoCredentialsError", "TokenRetrievalError", "UnauthorizedSSOTokenError") or codigo in (
        "UnrecognizedClientException", "ExpiredTokenException", "InvalidSignatureException"
    ):
        return f"Sem credencial válida da AWS ({codigo or nome}). {login}"
    if nome == "MissingDependencyException":
        return ('Falta uma dependência da AWS para ler a credencial do `aws login`. '
                'Rode `pip install -r requirements.txt` (ou `pip install "boto3[crt]"`) e suba o servidor de novo.')
    if nome == "ProfileNotFound":
        return f"Perfil da AWS não encontrado ({e}). Confira a variável AWS_PROFILE."
    if codigo == "ThrottlingException" and "per day" in str(e).lower():
        return ("A conta da AWS está sem cota diária no Bedrock para os modelos Nova testados "
                f"({', '.join(modelos())}). Abra um chamado no AWS Support pedindo o aumento da cota, "
                "ou rode o servidor com FRONTDESK_AGENTE=ensaio para testar sem o Bedrock.")
    if codigo == "AccessDeniedException":
        return f"O Bedrock negou o acesso ao modelo {modelo()}: {e}"
    if codigo in ("ValidationException", "ResourceNotFoundException"):
        return f"O Bedrock recusou o pedido ({codigo}) para o modelo {modelo()}: {e}"
    return f"{nome}: {e}"


def encerrar_sessao(sessao_id: str) -> None:
    _sessoes.pop(sessao_id, None)


# ------------------------------------------------------------- MCP -> Bedrock

def _simplificar_schema(schema: Any) -> Any:
    """Deixa o JSON Schema do MCP no subconjunto aceito pela Converse API.

    O SDK gera campos opcionais como anyOf [string, null] com default null; o Bedrock
    aceita melhor um tipo simples, e o campo continua opcional por não estar em "required".
    """
    if isinstance(schema, list):
        return [_simplificar_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    s = {k: v for k, v in schema.items() if k not in ("title", "default")}
    opcoes = s.get("anyOf")
    if isinstance(opcoes, list):
        nao_nulas = [o for o in opcoes if not (isinstance(o, dict) and o.get("type") == "null")]
        if len(nao_nulas) == 1:
            s.pop("anyOf")
            s = {**_simplificar_schema(nao_nulas[0]), **s}
    return {k: _simplificar_schema(v) for k, v in s.items()}


def ferramentas_para_bedrock(ferramentas) -> list[dict]:
    specs = []
    for t in ferramentas:
        schema = _simplificar_schema(t.input_schema or {"type": "object", "properties": {}})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        specs.append({"toolSpec": {
            "name": t.name,
            "description": (t.description or t.name).strip(),
            "inputSchema": {"json": schema},
        }})
    return specs


def _texto_do_resultado(resultado) -> str:
    return "".join(getattr(c, "text", "") for c in resultado.content if getattr(c, "type", "") == "text")


# ------------------------------------------------------------------ Bedrock

_cliente_bedrock = None


def _bedrock():
    global _cliente_bedrock
    if _cliente_bedrock is None:
        import boto3
        from botocore.config import Config

        sessao = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None)
        _cliente_bedrock = sessao.client(
            "bedrock-runtime",
            region_name=os.environ.get("AWS_REGION", "us-east-2"),
            config=Config(retries={"max_attempts": 2, "mode": "standard"}, read_timeout=60),
        )
    return _cliente_bedrock


def modelos() -> list[str]:
    return [m.strip() for m in os.environ.get("FRONTDESK_MODELO", MODELOS_PADRAO).split(",") if m.strip()]


_pausados: dict[str, float] = {}


def modelo() -> str:
    """Modelo preferido no momento (o primeiro que não está pausado)."""
    agora = time.time()
    for m in modelos():
        if _pausados.get(m, 0) <= agora:
            return m
    return modelos()[0]


def _codigo(e: BaseException) -> str:
    resposta = getattr(e, "response", None)
    return resposta.get("Error", {}).get("Code", "") if isinstance(resposta, dict) else ""


def _troca_de_modelo_resolve(e: BaseException) -> bool:
    """Erros que dependem do modelo (cota, acesso, modelo inexistente na Região): vale tentar outro."""
    codigo, texto = _codigo(e), str(e).lower()
    if codigo == "ThrottlingException":
        return "per day" in texto or "tokens" in texto
    if codigo in ("AccessDeniedException", "ResourceNotFoundException"):
        return True
    return codigo == "ValidationException" and ("model" in texto or "inference profile" in texto)


def _converse(**kwargs) -> dict:
    return _bedrock().converse(**kwargs)


def _chamar_modelo(converse, **kwargs) -> tuple[dict, str]:
    """Chama o primeiro modelo disponível; se ele estiver sem cota ou sem acesso, tenta o próximo."""
    agora = time.time()
    candidatos = [m for m in modelos() if _pausados.get(m, 0) <= agora] or modelos()
    ultimo_erro: BaseException | None = None
    for m in candidatos:
        try:
            return converse(modelId=m, **kwargs), m
        except Exception as e:
            if not _troca_de_modelo_resolve(e):
                raise
            _pausados[m] = time.time() + PAUSA_MODELO_S
            ultimo_erro = e
    raise ultimo_erro  # type: ignore[misc]


def _instrucoes(negocio: str) -> str:
    momento = agenda.agora()
    dia = agenda.NOMES_DIAS[momento.weekday()]
    return INSTRUCOES.format(
        negocio=negocio,
        hoje=f"{dia}, {momento:%d/%m/%Y}",
        hora=agenda.falar_hora(momento.time()),
    )


def _url_mcp_padrao() -> str:
    return os.environ.get("FRONTDESK_MCP_URL") or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}/mcp"


def _aparar_historico(mensagens: list[dict]) -> list[dict]:
    """Mantém as últimas mensagens, começando sempre numa fala de texto do usuário."""
    if len(mensagens) <= MAX_TURNOS_HISTORICO:
        return mensagens
    corte = mensagens[-MAX_TURNOS_HISTORICO:]
    while corte and not (corte[0]["role"] == "user" and any("text" in c for c in corte[0]["content"])):
        corte = corte[1:]
    return corte


async def responder(
    texto: str,
    sessao_id: str | None = None,
    servidor: Any = None,
    negocio: str = "Barbearia Navalha",
    converse=None,
) -> dict:
    """Processa uma fala do cliente e devolve a resposta do agente com as chamadas de ferramenta.

    `servidor` é a URL do servidor MCP (padrão: FRONTDESK_MCP_URL) ou um MCPServer em memória (testes).
    `converse` permite trocar a chamada ao Bedrock (testes).
    """
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("Fala vazia.")
    converse = converse or _converse
    alvo = servidor if servidor is not None else _url_mcp_padrao()
    opcoes = {"mode": "legacy"} if isinstance(alvo, str) else {}
    sid, sessao = _sessao(sessao_id)
    mensagens = _aparar_historico(sessao.mensagens + [{"role": "user", "content": [{"text": texto}]}])

    chamadas: list[dict] = []
    modelos_usados: list[str] = []
    inicio_total = time.perf_counter()

    try:
        resposta_final, ms_modelo, ms_tools_list = await _rodadas(
            alvo, opcoes, mensagens, chamadas, negocio, converse, modelos_usados)
    except BaseException as e:
        raiz = _raiz(e)
        if isinstance(raiz, (KeyboardInterrupt, SystemExit, anyio.get_cancelled_exc_class())):
            raise
        raise ErroAgente(explicar_erro(raiz)) from raiz

    if not resposta_final:
        resposta_final = chamadas[-1]["fala"] if chamadas else "Desculpe, não entendi. Pode repetir?"
    sessao.mensagens = mensagens
    return {
        "sessao": sid,
        "resposta": resposta_final,
        "chamadas": chamadas,
        "modelo": modelos_usados[-1] if modelos_usados else modelo(),
        "ms_modelo": ms_modelo,
        "ms_tools_list": ms_tools_list,
        "ms_total": round((time.perf_counter() - inicio_total) * 1000),
    }


async def _rodadas(alvo, opcoes, mensagens, chamadas, negocio, converse, modelos_usados) -> tuple[str, int, int]:
    ms_modelo = 0
    resposta_final = ""
    async with Client(alvo, **opcoes) as mcp_cliente:
        t0 = time.perf_counter()
        lista = await mcp_cliente.list_tools()
        ms_tools_list = round((time.perf_counter() - t0) * 1000)
        ferramentas = ferramentas_para_bedrock(lista.tools)

        for _ in range(MAX_RODADAS):
            t0 = time.perf_counter()
            saida, usado = await anyio.to_thread.run_sync(lambda: _chamar_modelo(
                converse,
                system=[{"text": _instrucoes(negocio)}],
                messages=mensagens,
                toolConfig={"tools": ferramentas},
                inferenceConfig={"maxTokens": 400, "temperature": 0.2},
            ))
            ms_modelo += round((time.perf_counter() - t0) * 1000)
            modelos_usados.append(usado)
            mensagem = saida["output"]["message"]
            mensagens.append(mensagem)

            pedidos = [c["toolUse"] for c in mensagem["content"] if "toolUse" in c]
            if saida.get("stopReason") != "tool_use" or not pedidos:
                resposta_final = " ".join(c["text"] for c in mensagem["content"] if "text" in c).strip()
                break

            resultados = []
            for pedido in pedidos:
                argumentos = pedido.get("input") or {}
                t0 = time.perf_counter()
                try:
                    r = await mcp_cliente.call_tool(pedido["name"], argumentos)
                    bruto = _texto_do_resultado(r)
                    try:
                        dados = json.loads(bruto)
                    except json.JSONDecodeError:
                        dados = {"ok": not r.is_error, "fala": bruto}
                    erro = bool(r.is_error)
                except Exception as e:  # falha de rede ou ferramenta desconhecida: o modelo precisa saber
                    dados, erro = {"ok": False, "fala": f"Erro ao chamar a ferramenta: {e}"}, True
                ms = round((time.perf_counter() - t0) * 1000)
                chamadas.append({
                    "ferramenta": pedido["name"],
                    "argumentos": argumentos,
                    "ms": ms,
                    "ok": bool(dados.get("ok", not erro)),
                    "fala": dados.get("fala", ""),
                })
                resultados.append({"toolResult": {
                    "toolUseId": pedido["toolUseId"],
                    "content": [{"json": dados}],
                    **({"status": "error"} if erro else {}),
                }})
            mensagens.append({"role": "user", "content": resultados})
        else:
            resposta_final = "Desculpe, me enrolei aqui. Pode repetir, por favor?"
    return resposta_final, ms_modelo, ms_tools_list
