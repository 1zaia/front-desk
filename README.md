# Front Desk

Recepcionista por voz para pequenos negócios, construída como servidor MCP para a trilha **Alexa+** do *Build, Ship, Shape: Amazon Developer Hackathon*.

O cliente fala "quero cortar o cabelo sexta de manhã", o servidor confere a agenda, marca o horário e o painel do dono atualiza na hora.

## Ferramentas MCP
| Ferramenta | O que faz | Exemplo de fala |
|---|---|---|
| `listar_servicos` | Serviços, duração e preço | "Quanto custa o corte?" |
| `consultar_horarios_livres` | Horários livres por dia, período e profissional | "Tem horário sexta de manhã?" |
| `agendar` | Marca o horário, checando funcionamento e conflitos | "Marca um corte sexta às 9h para o João" |
| `cancelar` | Cancela o próximo horário do cliente | "Cancela o meu horário" |
| `reagendar` | Muda dia ou hora, mantendo o profissional quando possível | "Passa para as 16h" |
| `resumo_do_dia` | Resumo para o dono: clientes, receita prevista, próximo atendimento | "Como está minha agenda hoje?" |

Toda resposta traz um campo `fala`, com a frase curta que o assistente de voz deve dizer, e os dados estruturados.
O servidor entende datas como "hoje", "amanhã", "sexta" e "23/10", e horários como "9h", "9h30" e "14:00".

Também há `GET /api/agenda?data=AAAA-MM-DD`, com a agenda do dia em JSON para o painel do dono.

## Status
- [x] Servidor MCP em `POST /mcp` (Streamable HTTP, MCP spec 2025-11-25)
- [x] Agenda em SQLite com dados de uma barbearia fictícia
- [x] As seis ferramentas de agenda, com testes
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

Variáveis opcionais:
- `FRONTDESK_DB`: caminho do banco SQLite (padrão: `frontdesk.db` na raiz).
- `FRONTDESK_DEMO=0`: não cria os agendamentos de exemplo.
- `FRONTDESK_AGORA=2026-10-23T08:00`: fixa o relógio, útil para gravar a demo.

## Testar
```bash
python -m pytest -q
```

Para inspecionar com o MCP Inspector, conecte em `http://127.0.0.1:8000/mcp` usando o transporte Streamable HTTP.

## Licença
MIT
