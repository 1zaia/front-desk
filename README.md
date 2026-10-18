# Front Desk

Recepcionista por voz para pequenos negócios, construída como servidor MCP para a trilha **Alexa+** do *Build, Ship, Shape: Amazon Developer Hackathon*.

O cliente fala "quero cortar o cabelo sexta de manhã", o servidor confere a agenda, marca o horário e o painel do dono atualiza na hora.

## Status
- [x] Servidor MCP em `POST /mcp` (Streamable HTTP, MCP spec 2025-11-25)
- [x] Agenda em SQLite com dados de uma barbearia fictícia
- [x] Ferramenta `listar_servicos`
- [ ] `consultar_horarios_livres`, `agendar`, `cancelar`, `reagendar`, `resumo_do_dia`
- [ ] Simulador de voz com agente no Amazon Bedrock
- [ ] Painel do dono ao vivo

## Rodar localmente
Requer Python 3.10 ou superior.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m frontdesk.server
```

O servidor sobe em `http://127.0.0.1:8000/mcp` e a verificação de saúde em `http://127.0.0.1:8000/health`.

## Testar
```bash
python -m pytest -q
```

Para inspecionar com o MCP Inspector, conecte em `http://127.0.0.1:8000/mcp` usando o transporte Streamable HTTP.

## Licença
MIT
