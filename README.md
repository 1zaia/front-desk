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

Também há `GET /api/agenda?data=AAAA-MM-DD`, com a agenda do dia em JSON, e `GET /api/semana`, com os próximos 7 dias para o painel do dono.

## Simulador de voz e painel do dono
As ferramentas de desenvolvedor da Alexa+ estão em preview fechado, então a demo usa um simulador no navegador:

- `/simulador`: tela de assistente de voz. O cliente fala (Web Speech API, pt-BR) ou digita; a fala vai para `POST /api/conversa`.
- `POST /api/conversa`: o agente no **Amazon Bedrock** (Amazon Nova, Converse API com tool use) lê as ferramentas do servidor MCP (`tools/list`), chama as que precisar (`tools/call`) e devolve a resposta falada e a lista de chamadas com argumentos e latência.
- `/painel`: agenda da semana por profissional, atualizando sozinha a cada 1,5 s; marcações novas, remarcações e cancelamentos piscam na tela.
- `/demo`: simulador e painel lado a lado, o enquadramento do vídeo.

O agente usa as credenciais padrão da AWS. Quem entra com `aws login` precisa do extra `crt` do botocore (já está no `requirements.txt` como `boto3[crt]`). Variáveis:
- `AWS_PROFILE` e `AWS_REGION` (padrão `us-east-2`).
- `FRONTDESK_MODELO`: modelos ou perfis de inferência do Bedrock, separados por vírgula, em ordem de preferência (padrão: Nova Lite, Nova Micro e Nova 2 Lite). Se um estiver sem cota ou sem acesso, o agente passa para o próximo.
- `FRONTDESK_AGENTE`: `bedrock` (padrão), `ensaio` (agente simples de regras, sem o modelo, para testar a demo; usa as mesmas ferramentas MCP e a tela mostra "modo ensaio") ou `auto` (Bedrock e, se ele falhar, modo ensaio com aviso na tela; é o que o deploy usa).
- `FRONTDESK_HOSTS`: domínios públicos aceitos pelo servidor MCP quando ele roda atrás de um proxy (ex.: `front-desk.exemplo.com`). Sem isso, o SDK só aceita `localhost` e responde 421.
- `FRONTDESK_LIMITE_POR_IP` e `FRONTDESK_LIMITE_DIARIO`: limite de falas por IP a cada 10 minutos (padrão 40) e por dia (padrão 600), para proteger os créditos da AWS.
- `FRONTDESK_MCP_URL`: servidor MCP que o agente usa (padrão: o próprio servidor).

## Status
- [x] Servidor MCP em `POST /mcp` (Streamable HTTP, MCP spec 2025-11-25)
- [x] Agenda em SQLite com dados de uma barbearia fictícia
- [x] As seis ferramentas de agenda, com testes
- [x] Simulador de voz com agente no Amazon Bedrock
- [x] Painel do dono ao vivo
- [x] Template de deploy na AWS (EC2 + HTTPS + papel IAM para o Nova)
- [ ] Deploy publicado

## Rodar localmente
Requer Python 3.10 ou superior.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m frontdesk.server
```

O servidor sobe em `http://127.0.0.1:8000/mcp` e a verificação de saúde em `http://127.0.0.1:8000/health`.
Abra `http://127.0.0.1:8000/demo` no Chrome para ver o simulador e o painel (para o agente, rode antes `aws login` e defina `AWS_PROFILE`).

Variáveis opcionais:
- `FRONTDESK_DB`: caminho do banco SQLite (padrão: `frontdesk.db` na raiz).
- `FRONTDESK_DEMO=0`: não cria os agendamentos de exemplo.
- `FRONTDESK_AGORA=2026-10-23T08:00`: fixa o relógio, útil para gravar a demo.

## Publicar na AWS
Uma instância EC2 com HTTPS (Caddy) e papel IAM só para o Amazon Nova, criada por CloudFormation. Passo a passo, custos e limpeza em [`deploy/README.md`](deploy/README.md).

## Testar
```bash
python -m pytest -q
```

Para inspecionar com o MCP Inspector, conecte em `http://127.0.0.1:8000/mcp` usando o transporte Streamable HTTP.

## Licença
MIT
