# Friction log

## MCP Python SDK 2.x renomeou FastMCP sem aviso nos tutoriais
- **Tarefa:** criar o esqueleto do servidor MCP em Python.
- **Passos:** `pip install mcp` e `from mcp.server.fastmcp import FastMCP`, como na maioria dos tutoriais.
- **Esperado:** importar o FastMCP.
- **Aconteceu:** `ModuleNotFoundError`. Na versão 2.x a classe virou `mcp.server.MCPServer`, e opções como `stateless_http` e `json_response` saíram do construtor para `streamable_http_app()`.
- **Gravidade:** média
- **Solução usada:** ler o código do pacote instalado e migrar para `MCPServer`.
- **Sugestão:** os recursos do hackathon poderiam fixar uma versão do SDK e trazer um exemplo mínimo atualizado de servidor Streamable HTTP.

## Retorno em dict não vira structuredContent
- **Tarefa:** devolver a resposta da ferramenta em formato estruturado.
- **Esperado:** `structuredContent` preenchido ao retornar um `dict`.
- **Aconteceu:** veio só como texto; `structuredContent` ficou vazio.
- **Gravidade:** baixa
- **Solução usada:** ler o texto da resposta no teste; tipar o retorno quando precisarmos de saída estruturada.
- **Sugestão:** documentar quando o SDK gera saída estruturada.

## `aws login` não abriu o navegador e expirou em silêncio
- **Tarefa:** autenticar a AWS CLI no Windows 11 com `aws login` (Agent Toolkit for AWS, passo 3).
- **Passos:** rodar `aws login --region us-east-2 --profile juan` no PowerShell.
- **Esperado:** abrir a página de login no navegador padrão.
- **Aconteceu:** a CLI disse que tentaria abrir o navegador, mas nenhuma aba abriu. Depois de 10 minutos saiu com código 255, sem mensagem explicando que o tempo tinha acabado.
- **Gravidade:** média
- **Solução usada:** capturar o link impresso pela CLI e abri-lo manualmente no navegador; a página "Choose AWS sessions" reaproveitou a sessão do console.
- **Sugestão:** avisar quando o navegador não abrir, mostrar uma contagem do tempo restante e explicar o código 255 como tempo esgotado.

## `aws agent-toolkit list-available-skills` quebra com erro de codificação no Windows
- **Tarefa:** verificar o Agent Toolkit (passo 6 do setup).
- **Passos:** capturar a saída do comando no PowerShell 5.1 (`$x = aws agent-toolkit list-available-skills ...`).
- **Esperado:** JSON com o catálogo de skills.
- **Aconteceu:** `aws: [ERROR]: 'charmap' codec can't encode character '\u2192'`, código 255. A configuração em si tinha funcionado.
- **Gravidade:** média (parece falha de instalação, mas não é)
- **Solução usada:** definir `PYTHONUTF8=1` e `PYTHONIOENCODING=utf-8` antes do comando; retornou 114 skills.
- **Sugestão:** a CLI forçar UTF-8 na saída ou trocar caracteres não ASCII (como a seta) nas descrições.
