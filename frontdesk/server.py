"""Servidor MCP do Front Desk: recepcionista por voz para pequenos negócios.

Transporte: Streamable HTTP em POST /mcp (MCP spec 2025-11-25 ou posterior).
Cada ferramenta devolve um campo "fala", com a frase curta que o assistente de voz deve dizer,
e os dados estruturados para quem quiser mostrar na tela.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations

from frontdesk import agenda, agente, db, ensaio
from frontdesk.agenda import ErroAgenda

mcp = MCPServer(
    name="front-desk",
    title="Front Desk",
    version="0.2.0",
    instructions=(
        "Você é a recepcionista de um pequeno negócio (uma barbearia). Use as ferramentas para "
        "consultar serviços e horários livres, agendar, cancelar e reagendar. Sempre confirme o nome "
        "do cliente antes de agendar. Responda com a frase do campo 'fala', curta e natural para voz."
    ),
)


def _conn():
    conn = db.connect()
    db.init_db(conn)
    if os.environ.get("FRONTDESK_DEMO", "1") != "0":
        agenda.semear_demo(conn, agenda.agora().date())
    return conn


def _erro(e: ErroAgenda) -> dict:
    return {"ok": False, "fala": str(e)}


def _quando(a: dict, hoje: date) -> str:
    inicio = datetime.fromisoformat(a["inicio"])
    return f"{agenda.falar_data(inicio.date(), hoje)} às {agenda.falar_hora(inicio.time())} com {a['profissional']}"


def _resumo_ag(a: dict, hoje: date) -> str:
    primeiro_nome = a["cliente"].split()[0]
    return f"{a['servico'].lower()} de {primeiro_nome} {_quando(a, hoje)}"


def _juntar(itens: list[str]) -> str:
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " ou " + itens[-1]


@mcp.tool(title="Listar serviços e preços", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def listar_servicos() -> dict[str, Any]:
    """Lista os serviços oferecidos pelo negócio, com duração e preço."""
    with _conn() as conn:
        servicos = db.listar_servicos(conn)
        negocio = db.nome_do_negocio(conn)
    itens = [f"{s['nome']} ({s['duracao_min']} min, {agenda.reais(s['preco_centavos'])})" for s in servicos]
    return {"ok": True, "fala": f"Na {negocio} temos: " + "; ".join(itens) + ".", "servicos": servicos}


@mcp.tool(title="Consultar horários livres", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def consultar_horarios_livres(
    data: Annotated[str, Field(description="Dia desejado: 2026-10-23, 23/10, hoje, amanhã ou o dia da semana (ex.: sexta).")],
    servico: Annotated[str, Field(description="Nome do serviço, ex.: corte, barba, corte e barba.")],
    periodo: Annotated[str | None, Field(description="Opcional: manhã, tarde ou noite.")] = None,
    profissional: Annotated[str | None, Field(description="Opcional: nome do profissional preferido.")] = None,
) -> dict[str, Any]:
    """Mostra os horários livres para um serviço num dia, opcionalmente por período e profissional."""
    momento = agenda.agora()
    try:
        with _conn() as conn:
            d = agenda.interpretar_data(data, momento.date())
            s = agenda.buscar_servico(conn, servico)
            p = agenda.buscar_profissional(conn, profissional)
            aberto = agenda._funcionamento(conn, d) is not None
            livres = agenda.horarios_livres(conn, d, s, momento, periodo, p) if aberto else []
    except ErroAgenda as e:
        return _erro(e)
    quando = agenda.falar_data(d, momento.date())
    if not aberto:
        return {"ok": True, "fala": f"Não abrimos {quando}.", "data": d.isoformat(), "aberto": False, "horarios": []}
    trecho = f" de {periodo}" if periodo else ""
    if not livres:
        return {"ok": True, "fala": f"Não tenho horário livre para {s.nome.lower()} {quando}{trecho}.", "data": d.isoformat(), "horarios": []}
    horarios = []
    for item in livres:
        if item["horario"] not in horarios:
            horarios.append(item["horario"])
    falados = [agenda.falar_hora(datetime.strptime(h, "%H:%M").time()) for h in horarios[:3]]
    lista = _juntar(falados)
    return {
        "ok": True,
        "fala": f"Para {s.nome.lower()} {quando}{trecho} tenho {lista}. Qual prefere?",
        "data": d.isoformat(),
        "servico": s.nome,
        "horarios": livres,
    }


@mcp.tool(title="Agendar horário", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False))
def agendar(
    cliente: Annotated[str, Field(description="Nome do cliente.")],
    servico: Annotated[str, Field(description="Nome do serviço.")],
    data: Annotated[str, Field(description="Dia: 2026-10-23, 23/10, hoje, amanhã ou o dia da semana.")],
    horario: Annotated[str, Field(description="Horário de início, ex.: 9h, 9:30, 14h30.")],
    profissional: Annotated[str | None, Field(description="Opcional: profissional desejado.")] = None,
) -> dict[str, Any]:
    """Marca um horário. Confere o funcionamento e se o horário está livre antes de gravar."""
    momento = agenda.agora()
    try:
        with _conn() as conn:
            d = agenda.interpretar_data(data, momento.date())
            h = agenda.interpretar_horario(horario)
            s = agenda.buscar_servico(conn, servico)
            p = agenda.buscar_profissional(conn, profissional)
            ag = agenda.agendar(conn, cliente, s, datetime.combine(d, h), momento, p)
    except ErroAgenda as e:
        return _erro(e)
    return {
        "ok": True,
        "fala": f"Pronto, {ag['cliente'].split()[0]}! Marquei {ag['servico'].lower()} para {_quando(ag, momento.date())}.",
        "agendamento": ag,
    }


@mcp.tool(title="Cancelar horário", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False))
def cancelar(
    cliente: Annotated[str, Field(description="Nome do cliente.")],
    data: Annotated[str | None, Field(description="Opcional: dia do agendamento, se o cliente tiver mais de um.")] = None,
) -> dict[str, Any]:
    """Cancela o próximo agendamento do cliente (ou o do dia informado)."""
    momento = agenda.agora()
    try:
        with _conn() as conn:
            d = agenda.interpretar_data(data, momento.date()) if data else None
            achados = agenda.buscar_agendamentos(conn, cliente, momento, d)
            if not achados:
                return {"ok": False, "fala": f"Não encontrei nenhum horário marcado para {cliente}."}
            if len(achados) > 1 and d is None:
                lista = "; ".join(_resumo_ag(a, momento.date()) for a in achados[:3])
                return {"ok": False, "fala": f"Encontrei mais de um horário: {lista}. Qual deles quer cancelar?", "agendamentos": achados}
            ag = agenda.cancelar(conn, achados[0]["id"])
    except ErroAgenda as e:
        return _erro(e)
    return {"ok": True, "fala": f"Cancelei {_resumo_ag(ag, momento.date())}.", "agendamento": ag}


@mcp.tool(title="Reagendar horário", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False))
def reagendar(
    cliente: Annotated[str, Field(description="Nome do cliente.")],
    nova_data: Annotated[str, Field(description="Novo dia.")],
    novo_horario: Annotated[str, Field(description="Novo horário, ex.: 16h.")],
    data_atual: Annotated[str | None, Field(description="Opcional: dia do agendamento atual, se houver mais de um.")] = None,
) -> dict[str, Any]:
    """Muda o dia ou o horário de um agendamento, mantendo o profissional quando possível."""
    momento = agenda.agora()
    try:
        with _conn() as conn:
            d_atual = agenda.interpretar_data(data_atual, momento.date()) if data_atual else None
            achados = agenda.buscar_agendamentos(conn, cliente, momento, d_atual)
            if not achados:
                return {"ok": False, "fala": f"Não encontrei nenhum horário marcado para {cliente}."}
            if len(achados) > 1 and d_atual is None:
                lista = "; ".join(_resumo_ag(a, momento.date()) for a in achados[:3])
                return {"ok": False, "fala": f"Encontrei mais de um horário: {lista}. Qual deles quer mudar?", "agendamentos": achados}
            novo = datetime.combine(
                agenda.interpretar_data(nova_data, momento.date()), agenda.interpretar_horario(novo_horario)
            )
            ag = agenda.reagendar(conn, achados[0]["id"], novo, momento)
    except ErroAgenda as e:
        return _erro(e)
    return {"ok": True, "fala": f"Feito! Seu horário mudou para {_quando(ag, momento.date())}.", "agendamento": ag}


@mcp.tool(title="Resumo do dia para o dono", annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
def resumo_do_dia(
    data: Annotated[str | None, Field(description="Opcional: dia do resumo. Padrão: hoje.")] = None,
) -> dict[str, Any]:
    """Resumo para o dono: quantos clientes, próximo atendimento, receita prevista e tempo livre."""
    momento = agenda.agora()
    try:
        with _conn() as conn:
            d = agenda.interpretar_data(data, momento.date()) if data else momento.date()
            r = agenda.resumo_do_dia(conn, d, momento)
    except ErroAgenda as e:
        return _erro(e)
    quando = agenda.falar_data(d, momento.date())
    if not r["aberto"]:
        return {"ok": True, "fala": f"Não abrimos {quando}.", **r}
    if not r["total_clientes"]:
        return {"ok": True, "fala": f"A agenda de {quando} está livre, sem clientes marcados.", **r}
    n = r["total_clientes"]
    qtd = "é 1 cliente" if n == 1 else f"são {n} clientes"
    partes = [
        f"{quando.capitalize()} {qtd}, com {agenda.reais(r['receita_prevista_centavos'])} previstos."
    ]
    if r["proximo"]:
        partes.append(f"O próximo é {_resumo_ag(r['proximo'], momento.date())}.")
    livres = r["minutos_livres_por_profissional"]
    if livres:
        mais_livre = max(livres, key=livres.get)
        partes.append(f"{mais_livre} tem mais tempo livre, cerca de {livres[mais_livre] // 60} horas.")
    return {"ok": True, "fala": " ".join(partes), **r}


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "servidor": "front-desk"})


@mcp.custom_route("/api/agenda", methods=["GET"])
async def api_agenda(request: Request) -> JSONResponse:
    """Agenda de um dia em JSON, para o painel do dono. Parâmetro opcional: ?data=AAAA-MM-DD."""
    momento = agenda.agora()
    try:
        d = agenda.interpretar_data(request.query_params.get("data", "hoje"), momento.date())
    except ErroAgenda as e:
        return JSONResponse({"erro": str(e)}, status_code=400)
    with _conn() as conn:
        itens = agenda.agendamentos_do_dia(conn, d)
        negocio = db.nome_do_negocio(conn)
    return JSONResponse({"negocio": negocio, "data": d.isoformat(), "agendamentos": itens})


@mcp.custom_route("/api/semana", methods=["GET"])
async def api_semana(request: Request) -> JSONResponse:
    """Agenda dos próximos dias para o painel ao vivo. Parâmetros opcionais: ?inicio=hoje&dias=7."""
    momento = agenda.agora()
    try:
        inicio = agenda.interpretar_data(request.query_params.get("inicio", "hoje"), momento.date())
        dias = max(1, min(14, int(request.query_params.get("dias", "7"))))
    except (ErroAgenda, ValueError) as e:
        return JSONResponse({"erro": str(e)}, status_code=400)
    with _conn() as conn:
        negocio = db.nome_do_negocio(conn)
        profissionais = [p.nome for p in agenda._profissionais(conn)]
        lista = []
        for i in range(dias):
            d = inicio + timedelta(days=i)
            janela = agenda._funcionamento(conn, d)
            lista.append({
                "data": d.isoformat(),
                "dia": agenda.NOMES_DIAS[d.weekday()],
                "aberto": janela is not None,
                "abre": janela[0].strftime("%H:%M") if janela else None,
                "fecha": janela[1].strftime("%H:%M") if janela else None,
                "agendamentos": agenda.agendamentos_do_dia(conn, d),
            })
    return JSONResponse({
        "negocio": negocio,
        "agora": momento.isoformat(timespec="minutes"),
        "profissionais": profissionais,
        "dias": lista,
    })


WEB = Path(__file__).resolve().parent / "web"
log = logging.getLogger("frontdesk")


LIMITE_POR_IP = int(os.environ.get("FRONTDESK_LIMITE_POR_IP", "40"))  # falas por IP a cada 10 minutos
LIMITE_DIARIO = int(os.environ.get("FRONTDESK_LIMITE_DIARIO", "600"))  # falas por dia no servidor todo
_falas_por_ip: dict[str, list[float]] = {}
_falas_hoje: dict[str, int] = {}
_bedrock_fora: dict = {"ate": 0.0, "aviso": ""}  # no modo auto, até quando pular o Bedrock depois de uma falha


def _dentro_do_limite(request: Request) -> bool:
    """Protege os créditos da AWS num endpoint público: limite por IP e teto diário."""
    encaminhado = request.headers.get("x-forwarded-for", "")
    ip = encaminhado.split(",")[0].strip() or (request.client.host if request.client else "?")
    agora = time.time()
    recentes = [t for t in _falas_por_ip.get(ip, []) if agora - t < 600]
    dia = date.today().isoformat()
    if len(recentes) >= LIMITE_POR_IP or _falas_hoje.get(dia, 0) >= LIMITE_DIARIO:
        _falas_por_ip[ip] = recentes
        return False
    recentes.append(agora)
    _falas_por_ip[ip] = recentes
    if dia not in _falas_hoje:  # virou o dia: zera a contagem
        _falas_hoje.clear()
    _falas_hoje[dia] = _falas_hoje.get(dia, 0) + 1
    return True


@mcp.custom_route("/api/conversa", methods=["POST"])
async def api_conversa(request: Request) -> JSONResponse:
    """Uma fala do cliente para o agente no Bedrock. Corpo: {"texto": "...", "sessao": "opcional"}."""
    if not _dentro_do_limite(request):
        return JSONResponse({"erro": "Muitas falas em pouco tempo. Espere alguns minutos e tente de novo."}, status_code=429)
    try:
        corpo = await request.json()
    except ValueError:
        return JSONResponse({"erro": "Corpo JSON inválido."}, status_code=400)
    texto = str(corpo.get("texto", "")).strip()
    if not texto:
        return JSONResponse({"erro": "Mande o campo 'texto' com a fala do cliente."}, status_code=400)
    if len(texto) > 500:
        return JSONResponse({"erro": "Fala longa demais."}, status_code=400)
    with _conn() as conn:
        negocio = db.nome_do_negocio(conn)
    modo = os.environ.get("FRONTDESK_AGENTE", "bedrock").lower()
    sessao = corpo.get("sessao") or None
    try:
        if modo == "ensaio":
            resultado = await ensaio.responder(texto, sessao, negocio=negocio)
        elif modo == "auto" and time.time() < _bedrock_fora["ate"]:
            # o Bedrock falhou há pouco: não insiste a cada fala, segue no modo ensaio por alguns minutos
            resultado = await ensaio.responder(texto, sessao, negocio=negocio)
            resultado["aviso"] = _bedrock_fora["aviso"]
        else:
            try:
                resultado = await agente.responder(texto, sessao, negocio=negocio)
            except agente.ErroAgente as e:
                if modo != "auto":
                    raise
                # modo auto (usado no deploy): se o Bedrock falhar, a demo continua no modo ensaio, avisando na tela
                log.warning("Bedrock indisponível, usando o modo ensaio: %s", e)
                _bedrock_fora.update(ate=time.time() + 300, aviso=f"Bedrock indisponível agora, respondendo em modo ensaio. Motivo: {e}")
                resultado = await ensaio.responder(texto, sessao, negocio=negocio)
                resultado["aviso"] = _bedrock_fora["aviso"]
    except agente.ErroAgente as e:  # credencial expirada, modelo sem acesso, rede: mostra na tela em vez de travar
        log.warning("Falha no agente: %s", e, exc_info=e.__cause__)
        return JSONResponse({"erro": str(e)}, status_code=502)
    return JSONResponse(resultado)


@mcp.custom_route("/api/conversa/{sessao}", methods=["DELETE"])
async def api_encerrar(request: Request) -> JSONResponse:
    agente.encerrar_sessao(request.path_params["sessao"])
    ensaio.encerrar_sessao(request.path_params["sessao"])
    return JSONResponse({"ok": True})


def _pagina(nome: str):
    async def rota(_: Request) -> FileResponse:
        return FileResponse(WEB / nome, media_type="text/html; charset=utf-8")
    return rota


mcp.custom_route("/simulador", methods=["GET"])(_pagina("simulador.html"))
mcp.custom_route("/painel", methods=["GET"])(_pagina("painel.html"))
mcp.custom_route("/demo", methods=["GET"])(_pagina("demo.html"))


@mcp.custom_route("/", methods=["GET"])
async def inicio(_: Request) -> RedirectResponse:
    return RedirectResponse("/demo")


def _seguranca_de_transporte() -> TransportSecuritySettings | None:
    """Proteção contra DNS rebinding do SDK, liberando também o domínio público do deploy.

    Sem isso, atrás de um proxy (Caddy, load balancer) o SDK responde 421 "Invalid Host header",
    porque só aceita localhost. FRONTDESK_HOSTS lista os domínios públicos, separados por vírgula.
    """
    publicos = [h.strip() for h in os.environ.get("FRONTDESK_HOSTS", "").split(",") if h.strip()]
    if not publicos:
        return None  # o SDK aplica o padrão: só localhost
    locais = ["127.0.0.1", "localhost", "[::1]"]
    hosts = [f"{h}{porta}" for h in locais + publicos for porta in ("", ":*")]
    origens = [f"http://{h}:*" for h in locais] + [f"https://{h}" for h in publicos]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origens)


def app():
    """App ASGI (para uvicorn ou deploy)."""
    return mcp.streamable_http_app(
        stateless_http=True, json_response=True, transport_security=_seguranca_de_transporte()
    )


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app(), host=host, port=port)


if __name__ == "__main__":
    main()
