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
- **Solução usada:** anotar o retorno como `dict[str, Any]` em vez de `dict`. Com isso o SDK gera `outputSchema` e preenche `structuredContent`; com `structured_output=True` e `-> dict` puro ele recusa a função ("return type dict is not serializable for structured output").
- **Sugestão:** documentar a diferença entre `dict` e `dict[str, Any]` no retorno das ferramentas, ou tratar os dois do mesmo jeito.

## Servidor MCP responde 421 "Invalid Host header" atrás de um proxy
- **Tarefa:** preparar o servidor para rodar atrás do Caddy (HTTPS) num domínio público.
- **Passos:** subir o servidor em `127.0.0.1:8000` com `streamable_http_app()` e mandar um `initialize` com `Host: <dominio-publico>`.
- **Esperado:** `200`, como em `localhost`.
- **Aconteceu:** `421 Invalid Host header`. O SDK liga sozinho a proteção contra DNS rebinding quando o host é `127.0.0.1` e só aceita `localhost`; nada avisa isso na documentação de deploy.
- **Gravidade:** média (o endpoint publicado não responderia ao Alexa+ nem ao MCP Inspector)
- **Solução usada:** passar `TransportSecuritySettings` com o domínio público em `allowed_hosts` (variável `FRONTDESK_HOSTS`), mantendo a proteção ligada. Coberto por teste.
- **Sugestão:** citar a proteção de Host no guia de deploy do SDK e na página de requisitos do endpoint do Alexa+, com um exemplo atrás de proxy.

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

## Credencial do `aws login` não funciona no boto3 sem o extra `crt`
- **Tarefa:** chamar o Amazon Nova pela Converse API (boto3) a partir do servidor do Front Desk, usando o perfil criado com `aws login` na nova experiência da AWS.
- **Passos:** `pip install boto3` (1.40+), `aws login --region us-east-2 --profile juan`, `$env:AWS_PROFILE = "juan"`, chamar `bedrock-runtime.converse`.
- **Esperado:** o boto3 usar a mesma credencial que a AWS CLI já usa.
- **Aconteceu:** `MissingDependencyException: Missing Dependency: Using the login credential provider requires an additional dependency. You will need to pip install "botocore[crt]" before proceeding.` A CLI funciona, mas o SDK não, e nada no fluxo do `aws login` nem no Agent Toolkit avisa que o SDK em Python precisa do extra. No nosso servidor o erro ainda veio embrulhado num `ExceptionGroup` do cliente MCP, o que escondeu a causa na primeira tentativa.
- **Gravidade:** média (bloqueia a primeira chamada ao Bedrock pelo código; a mensagem final é clara)
- **Solução usada:** trocar `boto3` por `boto3[crt]` no `requirements.txt`.
- **Sugestão:** instalar o `awscrt` como dependência padrão do botocore quando o perfil usa `login_session`, ou o `aws login` avisar ao terminar: "para usar esta credencial em SDKs Python, instale botocore[crt]". Vale incluir isso no guia da nova experiência e nas skills do Agent Toolkit.

## Conta nova recebe "Too many tokens per day" na primeira chamada ao Amazon Nova
- **Tarefa:** primeira chamada ao Amazon Nova Lite pela Converse API (boto3), Região us-east-2, perfil de inferência `us.amazon.nova-lite-v1:0`, conta criada pela nova experiência da AWS (plano Free, US$ 100 de crédito).
- **Passos:** `aws login --region us-east-2 --profile juan`; subir o servidor com `AWS_PROFILE=juan`; mandar uma única frase curta ("segunda-feira, meio-dia") pelo simulador.
- **Esperado:** resposta do modelo; o uso total da conta até ali era zero.
- **Aconteceu:** `ThrottlingException: Too many tokens per day, please wait before trying again` já na primeira chamada, com 3 novas tentativas automáticas do SDK, todas iguais. Nenhuma mensagem diz qual cota é, qual o valor aplicado nem como pedir aumento. Confirmado com `aws service-quotas list-service-quotas --service-code bedrock --region us-east-2`: todas as cotas "tokens per day" dos modelos Nova (Nova Lite, Micro, Pro, Premier, Nova 2 Lite, Nova 2 Omni, Nova 2 Pro Preview e os perfis globais) aparecem com valor **0,0** na conta. Pior: no Service Quotas essas cotas aparecem como **não ajustáveis**, com valor padrão da AWS de 5,76 bilhões de tokens por dia (11,52 bilhões no perfil global do Nova 2 Lite) e valor aplicado 0, então nem dá para pedir aumento por lá (`request-service-quota-increase`). Relatos no re:Post descrevem o mesmo em contas novas e a solução lá é abrir chamado no suporte.
- **Gravidade:** bloqueante para o mini desafio AWS Builder (o agente no Bedrock não roda)
- **Solução usada:** o agente passou a tentar outros modelos Nova em sequência quando um está sem cota (Nova Lite, Nova Micro, Nova 2 Lite) e ganhou um "modo ensaio" sem Bedrock para testar o resto da demo; chamado aberto no AWS Support (caso 179236335500169, 18/10) pedindo a cota. O deploy na EC2 confirmou que o papel IAM chega ao Bedrock: o erro é de cota, não de permissão.
- **Sugestão:** contas novas, ainda mais as do plano Free com crédito, deveriam sair com uma cota diária mínima utilizável para os modelos Nova; a mensagem de erro deveria citar o nome da cota no Service Quotas e o link para pedir aumento; e o Playground/console do Bedrock deveria mostrar "sua cota diária é 0" antes de o desenvolvedor escrever código.
