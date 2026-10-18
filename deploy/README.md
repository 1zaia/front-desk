# Publicar o Front Desk na AWS

Uma instância **EC2** (`t3.micro`, Amazon Linux 2023) roda o servidor do Front Desk atrás do **Caddy**, que cuida do HTTPS com certificado Let's Encrypt num domínio `<ip>.sslip.io`. A instância tem um **papel IAM** que só permite chamar os modelos **Amazon Nova** no Bedrock, então não há chave de acesso em lugar nenhum. Tudo é criado por um template **CloudFormation** (`front-desk.yaml`).

```
Navegador ──HTTPS──> Caddy (443) ──> Front Desk (127.0.0.1:8000)
                                       ├── /mcp        servidor MCP (Streamable HTTP)
                                       ├── /demo       simulador + painel
                                       └── agente ──> Amazon Bedrock (Amazon Nova, papel da instância)
```

Por que EC2: no plano Free da nova experiência da AWS, o App Runner não está disponível; o servidor guarda a agenda em SQLite e precisa de **uma única instância** para que o agente, o MCP e o painel vejam os mesmos dados. A EC2 resolve isso de forma simples e barata.

## Publicar
Pré-requisitos: código na `main` do GitHub (a instância clona o repositório) e `aws login --region us-east-2 --profile juan` feito.

No Windows, dê dois cliques em `deploy\publicar.cmd`, ou rode:

```powershell
aws cloudformation deploy --template-file deploy/front-desk.yaml --stack-name front-desk --capabilities CAPABILITY_IAM --region us-east-2 --profile juan
aws cloudformation describe-stacks --stack-name front-desk --region us-east-2 --profile juan --query "Stacks[0].Outputs" --output table
```

A saída mostra `DemoUrl`, `McpUrl` e `PainelUrl`. O HTTPS fica pronto de 3 a 5 minutos depois que a pilha termina (a instância instala tudo e pede o certificado).

Parâmetros opcionais (acrescente `--parameter-overrides Nome=valor`):
- `AgentMode`: `auto` (padrão: Bedrock e, se falhar, modo ensaio com aviso), `bedrock` ou `ensaio`.
- `Branch`: branch do GitHub a publicar (padrão `main`).
- `InstanceType`: `t3.micro` (padrão) ou `t3.small`.

## Atualizar depois de um merge
A instância clona o código só na criação. Para pegar a `main` nova, recrie a instância:

```powershell
aws cloudformation delete-stack --stack-name front-desk --region us-east-2 --profile juan
aws cloudformation wait stack-delete-complete --stack-name front-desk --region us-east-2 --profile juan
deploy\publicar.cmd
```

(O IP elástico e o domínio mudam ao recriar.)

## Testar o endpoint MCP publicado
```bash
npx @modelcontextprotocol/inspector --cli https://<ip>.sslip.io/mcp --transport http --method tools/list
```

## Problemas
- **Página não abre logo depois do deploy:** espere 5 minutos. Para ver o log da instalação, abra a instância no console da EC2 > Conectar > Session Manager e rode `sudo tail -n 80 /var/log/cloud-init-output.log`, `systemctl status front-desk caddy` e `journalctl -u front-desk -n 50`.
- **Simulador mostra "modo ensaio" com aviso de cota:** o Bedrock da conta está sem cota diária para o Nova (ver `FRICTION-LOG.md`). A demo continua funcionando no modo ensaio até a cota sair.

## Custos e limpeza
- `t3.micro` + IP público + 8 GB de disco: cerca de US$ 12 por mês, cobertos pelos créditos do plano Free. Chamadas ao Nova Lite custam frações de centavo por conversa.
- O servidor limita as falas por IP e por dia (`FRONTDESK_LIMITE_POR_IP`, `FRONTDESK_LIMITE_DIARIO`) para proteger os créditos.
- Manter no ar até o fim do julgamento (20/11/2026). Depois, apagar tudo com `aws cloudformation delete-stack --stack-name front-desk --region us-east-2 --profile juan`.
