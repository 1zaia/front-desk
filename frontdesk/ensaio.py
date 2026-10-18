"""Modo ensaio: um agente simples, sem Bedrock, para testar a demo quando o modelo não está disponível.

Entende frases de agendamento em português com regras (serviço, dia, período, horário, nome) e
chama as mesmas ferramentas do servidor MCP, pelo mesmo caminho do agente de verdade
(tools/list e tools/call). Ativado com FRONTDESK_AGENTE=ensaio. A tela mostra "modo ensaio".
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from typing import Any

from mcp import Client

from frontdesk import agente

NOME_MODO = "modo ensaio (sem Bedrock)"
DIAS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
NAO_NOMES = {
    "corte", "barba", "cabelo", "pigmentacao", "dia", "hoje", "amanha", "manha", "tarde", "noite",
    "horario", "sexta", "segunda", "terca", "quarta", "quinta", "sabado", "domingo", "sim", "nao",
    "rafael", "bruno", "o", "a", "um", "uma", "de", "do", "da", "as", "pra", "para",
}

_estados: dict[str, dict] = {}


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w/:+]", " ", t).split())


def _servico(t: str) -> str | None:
    if re.search(r"corte (e|com|\+) barba|cabelo e barba|barba e (corte|cabelo)", t):
        return "corte e barba"
    if "pigment" in t:
        return "pigmentação"
    if "barba" in t and re.search(r"\b(corte|cortar|cabelo)\b", t):
        return "corte e barba"
    if "barba" in t:
        return "barba"
    if re.search(r"\b(corte|cortar|cabelo)\b", t):
        return "corte"
    return None


def _data(t: str) -> str | None:
    if "depois de amanha" in t:
        return "depois de amanhã"
    if re.search(r"\bamanha\b", t):
        return "amanhã"
    if re.search(r"\bhoje\b", t):
        return "hoje"
    m = re.search(r"\b(\d{1,2}/\d{1,2})\b", t)
    if m:
        return m.group(1)
    for d in DIAS:
        if re.search(rf"\b{d}\b", t):
            return d
    return None


def _periodo(t: str) -> str | None:
    for p, falado in (("manha", "manhã"), ("tarde", "tarde"), ("noite", "noite")):
        if re.search(rf"\b(de|a|na|pela|essa|esta|nessa|nesta) {p}\b", t) or t == p:
            return falado
    return None


def _horario(t: str) -> str | None:
    if re.search(r"\bmeio dia\b", t):
        return "12h30" if "meio dia e meia" in t else "12h"
    m = re.search(r"\b(\d{1,2}) e meia\b", t)
    if m:
        return f"{int(m.group(1))}h30"
    m = re.search(r"\b(\d{1,2})\s*(?:h|:|horas?\b)\s*(\d{2})?", t)
    if m and not re.search(rf"\b{m.group(1)}/", t):
        return f"{int(m.group(1))}h{m.group(2) or ''}"
    m = re.search(r"\b(?:as|para as|pras|a partir das)\s+(\d{1,2})\b(?!/)", t)
    if m:
        return f"{int(m.group(1))}h"
    return None


def _nome(original: str, t: str, esperando_nome: bool) -> str | None:
    m = re.search(r"(?:meu nome e|me chamo|aqui e o|aqui e a|sou o|sou a|no nome de|em nome de|nome do|nome da)\s+([a-z]+(?:\s+[a-z]+)?)", t)
    candidato = m.group(1) if m else None
    if not candidato and esperando_nome:
        palavras = [p for p in t.replace("e o ", "").replace("sou ", "").split() if p not in NAO_NOMES]
        if 1 <= len(palavras) <= 3:
            candidato = " ".join(palavras[:2])
    if not candidato:
        return None
    partes = [p for p in candidato.split() if p not in NAO_NOMES]
    if not partes:
        return None
    # recupera a grafia com acento da fala original
    originais = {_norm(w): w for w in re.findall(r"[\wÀ-ÿ]+", original)}
    return " ".join(originais.get(p, p).capitalize() for p in partes)


def _profissional(t: str) -> str | None:
    for nome in ("rafael", "bruno"):
        if re.search(rf"\b{nome}\b", t):
            return nome.capitalize()
    return None


def _intencao(t: str) -> str:
    if re.search(r"\bcancel", t) or "desmarc" in t:
        return "cancelar"
    if re.search(r"remarc|reagend|mudar|trocar|passa pra|passar pra|passa para|adiar", t):
        return "reagendar"
    if re.search(r"resumo|minha agenda|como (esta|ta) (a|minha) agenda|quantos clientes|agenda de hoje", t):
        return "resumo"
    if re.search(r"quanto custa|quanto e|preco|valor|quais servicos|que servicos|o que voces fazem|tabela", t):
        return "servicos"
    return "agendar"


async def responder(texto: str, sessao_id: str | None = None, servidor: Any = None, negocio: str = "") -> dict:
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("Fala vazia.")
    sid = sessao_id or uuid.uuid4().hex
    est = _estados.setdefault(sid, {})
    t = _norm(texto)
    alvo = servidor if servidor is not None else agente._url_mcp_padrao()
    opcoes = {"mode": "legacy"} if isinstance(alvo, str) else {}
    chamadas: list[dict] = []
    inicio = time.perf_counter()

    esperando_nome = est.get("pendente") == "nome"
    novos = {
        "servico": _servico(t), "data": _data(t), "periodo": _periodo(t), "horario": _horario(t),
        "nome": _nome(texto, t, esperando_nome), "profissional": _profissional(t),
    }
    intencao = _intencao(t)
    if intencao == "agendar" and est.get("intencao") in ("cancelar", "reagendar") and not novos["servico"]:
        intencao = est["intencao"]
    for k, v in novos.items():
        if v:
            est[k] = v
    est["intencao"] = intencao
    est.pop("pendente", None)

    try:
        async with Client(alvo, **opcoes) as mcp:
            t0 = time.perf_counter()
            await mcp.list_tools()
            ms_tools_list = round((time.perf_counter() - t0) * 1000)

            async def usar(nome: str, args: dict) -> dict:
                args = {k: v for k, v in args.items() if v}
                t1 = time.perf_counter()
                r = await mcp.call_tool(nome, args)
                bruto = "".join(getattr(c, "text", "") for c in r.content if getattr(c, "type", "") == "text")
                try:
                    dados = json.loads(bruto)
                except json.JSONDecodeError:
                    dados = {"ok": not r.is_error, "fala": bruto}
                chamadas.append({"ferramenta": nome, "argumentos": args, "ms": round((time.perf_counter() - t1) * 1000),
                                 "ok": bool(dados.get("ok")), "fala": dados.get("fala", "")})
                return dados

            resposta = await _decidir(est, intencao, t, usar)
    except BaseException as e:
        raiz = agente._raiz(e)
        raise agente.ErroAgente(agente.explicar_erro(raiz)) from raiz

    return {
        "sessao": sid,
        "resposta": resposta,
        "chamadas": chamadas,
        "modelo": NOME_MODO,
        "ms_modelo": 0,
        "ms_tools_list": ms_tools_list,
        "ms_total": round((time.perf_counter() - inicio) * 1000),
    }


async def _decidir(est: dict, intencao: str, t: str, usar) -> str:
    if intencao == "servicos":
        return (await usar("listar_servicos", {}))["fala"]

    if intencao == "resumo":
        return (await usar("resumo_do_dia", {"data": est.get("data")}))["fala"]

    if intencao == "cancelar":
        if not est.get("nome"):
            est["pendente"] = "nome"
            return "Claro. Em nome de quem está o horário?"
        r = await usar("cancelar", {"cliente": est["nome"], "data": est.get("data") if _data(t) else None})
        if r.get("ok"):
            _limpar(est)
        return r["fala"]

    if intencao == "reagendar":
        if not est.get("nome"):
            est["pendente"] = "nome"
            return "Claro. Em nome de quem está o horário?"
        if not est.get("horario"):
            return "Para que dia e horário você quer mudar?"
        r = await usar("reagendar", {"cliente": est["nome"], "nova_data": est.get("data") or "hoje",
                                     "novo_horario": est["horario"]})
        if r.get("ok"):
            _limpar(est)
        else:
            est.pop("horario", None)
        return r["fala"]

    # agendar
    if re.fullmatch(r"(oi|ola|bom dia|boa tarde|boa noite|e ai)( tudo bem)?", t):
        return "Olá! Quer marcar um horário? É só dizer o serviço e o dia."
    if not est.get("servico"):
        return "Claro! Qual serviço você quer: corte, barba, corte e barba ou pigmentação?"
    if not est.get("data"):
        return f"Para qual dia você quer o {est['servico']}?"
    if not est.get("horario"):
        r = await usar("consultar_horarios_livres", {"data": est["data"], "servico": est["servico"],
                                                     "periodo": est.get("periodo"), "profissional": est.get("profissional")})
        return r["fala"]
    if not est.get("nome"):
        est["pendente"] = "nome"
        return "Perfeito. Qual é o seu nome?"
    r = await usar("agendar", {"cliente": est["nome"], "servico": est["servico"], "data": est["data"],
                               "horario": est["horario"], "profissional": est.get("profissional")})
    if r.get("ok"):
        _limpar(est)
    else:
        est.pop("horario", None)
    return r["fala"]


def _limpar(est: dict) -> None:
    nome = est.get("nome")
    est.clear()
    if nome:
        est["nome"] = nome


def encerrar_sessao(sessao_id: str) -> None:
    _estados.pop(sessao_id, None)
