"""Servidor MCP do Front Desk: recepcionista por voz para pequenos negócios.

Transporte: Streamable HTTP em POST /mcp (MCP spec 2025-11-25 ou posterior).
Cada ferramenta devolve um campo "fala", com a frase curta que o assistente de voz deve dizer,
e os dados estruturados para quem quiser mostrar na tela.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Annotated

from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server import MCPServer

from frontdesk import agenda, db
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


@mcp.tool()
def listar_servicos() -> dict:
    """Lista os serviços oferecidos pelo negócio, com duração e preço."""
    with _conn() as conn:
        servicos = db.listar_servicos(conn)
        negocio = db.nome_do_negocio(conn)
    itens = [f"{s['nome']} ({s['duracao_min']} min, {agenda.reais(s['preco_centavos'])})" for s in servicos]
    return {"ok": True, "fala": f"Na {negocio} temos: " + "; ".join(itens) + ".", "servicos": servicos}


@mcp.tool()
def consultar_horarios_livres(
    data: Annotated[str, Field(description="Dia desejado: 2026-10-23, 23/10, hoje, amanhã ou o dia da semana (ex.: sexta).")],
    servico: Annotated[str, Field(description="Nome do serviço, ex.: corte, barba, corte e barba.")],
    periodo: Annotated[str | None, Field(description="Opcional: manhã, tarde ou noite.")] = None,
    profissional: Annotated[str | None, Field(description="Opcional: nome do profissional preferido.")] = None,
) -> dict:
    """Mostra os horários livres para um serviço num dia, opcionalmente por período e profissional."""
    momento = agenda.agora()
    try:
        with _conn() as conn:
            d = agenda.interpretar_data(data, momento.date())
            s = agenda.buscar_servico(conn, servico)
            p = agenda.buscar_profissional(conn, profissional)
            livres = agenda.horarios_livres(conn, d, s, momento, periodo, p)
    except ErroAgenda as e:
        return _erro(e)
    quando = agenda.falar_data(d, momento.date())
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


@mcp.tool()
def agendar(
    cliente: Annotated[str, Field(description="Nome do cliente.")],
    servico: Annotated[str, Field(description="Nome do serviço.")],
    data: Annotated[str, Field(description="Dia: 2026-10-23, 23/10, hoje, amanhã ou o dia da semana.")],
    horario: Annotated[str, Field(description="Horário de início, ex.: 9h, 9:30, 14h30.")],
    profissional: Annotated[str | None, Field(description="Opcional: profissional desejado.")] = None,
) -> dict:
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


@mcp.tool()
def cancelar(
    cliente: Annotated[str, Field(description="Nome do cliente.")],
    data: Annotated[str | None, Field(description="Opcional: dia do agendamento, se o cliente tiver mais de um.")] = None,
) -> dict:
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


@mcp.tool()
def reagendar(
    cliente: Annotated[str, Field(description="Nome do cliente.")],
    nova_data: Annotated[str, Field(description="Novo dia.")],
    novo_horario: Annotated[str, Field(description="Novo horário, ex.: 16h.")],
    data_atual: Annotated[str | None, Field(description="Opcional: dia do agendamento atual, se houver mais de um.")] = None,
) -> dict:
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


@mcp.tool()
def resumo_do_dia(
    data: Annotated[str | None, Field(description="Opcional: dia do resumo. Padrão: hoje.")] = None,
) -> dict:
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


def app():
    """App ASGI (para uvicorn ou deploy)."""
    return mcp.streamable_http_app(stateless_http=True, json_response=True)


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app(), host=host, port=port)


if __name__ == "__main__":
    main()
