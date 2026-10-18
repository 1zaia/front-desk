"""Servidor MCP do Front Desk: recepcionista por voz para pequenos negócios.

Transporte: Streamable HTTP em POST /mcp (MCP spec 2025-11-25 ou posterior).
"""
from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server import MCPServer

from frontdesk import db

mcp = MCPServer(
    name="front-desk",
    title="Front Desk",
    version="0.1.0",
    instructions=(
        "Recepcionista de um pequeno negócio. Use as ferramentas para consultar serviços, "
        "horários livres e fazer agendamentos. Responda em frases curtas, próprias para voz."
    ),
)


def _conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


def _reais(centavos: int) -> str:
    return f"R$ {centavos / 100:.2f}".replace(".", ",")


@mcp.tool()
def listar_servicos() -> dict:
    """Lista os serviços oferecidos pelo negócio, com duração e preço."""
    with _conn() as conn:
        servicos = db.listar_servicos(conn)
        negocio = db.nome_do_negocio(conn)
    itens = [f"{s['nome']} ({s['duracao_min']} min, {_reais(s['preco_centavos'])})" for s in servicos]
    return {
        "fala": f"Na {negocio} temos: " + "; ".join(itens) + ".",
        "servicos": servicos,
    }


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "servidor": "front-desk"})


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
