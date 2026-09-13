# jaylog

Biblioteca de logging para Python com rotação de arquivos e envio HTTP para um endpoint remoto.

## Instalação

```bash
pip install -U --no-cache-dir jaylog
```

## Variáveis de ambiente

As variáveis usam o prefixo `JAYLOG_`. Podem ser definidas no ambiente do sistema ou em um arquivo `.env` / `.env.logging` na raiz do projeto.

| Variável                        | obrigatório? | Padrão    | Descrição                                                               |
| ------------------------------- | ------------ | --------- | ----------------------------------------------------------------------- |
| `JAYLOG_APP_NAME`               | SIM          | `null`    | Nome do serviço/bot (usado no nome do arquivo de log)                   |
| `JAYLOG_LOG_DIR`                | NÃO          | `null`    | Caminho do diretório onde os arquivos de log serão salvos. Se omitido, o handler de arquivo é desativado |
| `JAYLOG_LOG_LEVEL`              | NÃO          | `INFO`    | Nível mínimo de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)   |
| `JAYLOG_LOG_MAX_BYTES`          | NÃO          | `5242880` | Tamanho máximo do arquivo de log antes de rotacionar (bytes)            |
| `JAYLOG_LOG_BACKUP_COUNT`       | NÃO          | `5`       | Quantidade de arquivos de backup mantidos após rotação                  |
| `JAYLOG_LOG_RETENTION_DAYS`     | NÃO          | `7`       | Dias para manter arquivos de log antigos                                |
| `JAYLOG_LOG_CONSOLE_ENABLED`    | NÃO          | `true`    | Habilita a saída de log no console (`true`/`false`)                     |
| `JAYLOG_LOG_CONSOLE_COLOR`      | NÃO          | `null`    | Força (`true`) ou desliga (`false`) as cores no console. Se omitido, detecta automaticamente o suporte do terminal |
| `JAYLOG_LOG_HTTP_TIMEOUT`       | NÃO          | `5.0`     | Timeout em segundos para o envio HTTP                                   |
| `JAYLOG_LOG_HTTP_ENDPOINT`      | NÃO          | `null`    | URL do endpoint que receberá os logs                                    |
| `JAYLOG_LOG_HTTP_API_KEY`       | NÃO          | `null`    | Chave de autenticação enviada no header `x-api-key`                     |
| `JAYLOG_LOG_HTTP_PROXY`         | NÃO          | `null`    | URL do proxy para o envio HTTP (ex: `http:\\user:password@server:port`) |
| `JAYLOG_LOG_SCREENSHOT_ENABLED` | NÃO          | `false`   | Captura screenshot no momento do log (`true`/`false`, apenas Windows)   |


## Como usar?

> [!IMPORTANT]
> A partir da versão 0.2.2, `configure()` **deve** ser chamado antes de `get_logger()`.
> Chamar `get_logger()` sem configuração prévia lança uma exceção.

Existem alguns cenários diferentes onde a utilização desse lib pode mudar, abaixo estão os cenários mapeados e como realizar configuração para cada um.

## Arquivo único

__*.env.logging*__
```env
JAYLOG_APP_NAME=meu-bot
JAYLOG_LOG_DIR=C:\logs
```
__*main.py*__
```python
from jaylog import JaylogSettings, configure, get_logger

configure(JaylogSettings())

logger = get_logger()

logger.info("Arquivo Único")
```

## Apenas console (sem arquivo de log)

Basta omitir `JAYLOG_LOG_DIR`. O handler de console fica ativo por padrão.

__*.env.logging*__
```env
JAYLOG_APP_NAME=meu-bot
```
__*main.py*__
```python
from jaylog import JaylogSettings, configure, get_logger

configure(JaylogSettings())

logger = get_logger()

logger.info("Saída apenas no console")
```

## Múltiplos Arquivos

__*.env.logging*__
```env
JAYLOG_APP_NAME=meu-bot
JAYLOG_LOG_DIR=C:\logs
```

__*main.py*__
```python
from jaylog import JaylogSettings, get_logger, configure
from parse import parse_csv

configure(JaylogSettings())

logger = get_logger()

logger.info("Múltiplos Arquivos - main.py")
parse_csv()
```


__*parse.py*__
```python
from jaylog import get_logger

logger = get_logger()

def parse_csv():
    logger.info("Múltiplos Arquivos - parse.py")
```

## Múltiplos loggers nomeados

Quando o projeto possui serviços distintos, passe uma lista para `configure()`. Cada entrada usa seu próprio `app_name` e grava em arquivos separados. O campo `[service]` é exibido automaticamente no formato do log quando há mais de um logger registrado.

__*.env.logging*__
```env
JAYLOG_LOG_DIR=C:\logs
```

__*main.py*__
```python
from jaylog import JaylogSettings, configure, get_logger

settings_order   = JaylogSettings(app_name="ORDER-PROCESSOR")
settings_billing = JaylogSettings(app_name="BILLING")

configure([settings_order, settings_billing])

logger         = get_logger("ORDER-PROCESSOR")  # ou get_logger() — retorna o primeiro registrado
billing_logger = get_logger("BILLING")

logger.info("Pedido recebido")
billing_logger.info("Fatura emitida")
```



## Cores no console

As cores são ligadas automaticamente quando o terminal suporta ANSI. A detecção cobre:

- **Windows**: o modo *virtual terminal* do console é habilitado em tempo de execução, o que faz as cores funcionarem também no `cmd.exe`/PowerShell rodando no console legado (`conhost`) do Windows 10 — antes só saía colorido no Windows Terminal.
- **Saída redirecionada** (`python main.py > saida.txt`, pipes, serviços sem console): as cores são desligadas, para o arquivo não ficar com lixo do tipo `←[32m`.
- **Consoles antigos** que não suportam ANSI de jeito nenhum: o log sai em texto limpo, com o mesmo alinhamento.
- As convenções `NO_COLOR` e `FORCE_COLOR` são respeitadas.

Para forçar um comportamento, use `JAYLOG_LOG_CONSOLE_COLOR`:

__*.env.logging*__
```env
JAYLOG_APP_NAME=meu-bot
JAYLOG_LOG_CONSOLE_COLOR=false
```

Ou direto no código:

```python
configure(JaylogSettings(app_name="meu-bot", log_console_color=False))
```

> [!TIP]
> `JAYLOG_LOG_CONSOLE_COLOR=true` força as cores mesmo com a saída redirecionada — útil quando o log é consumido por uma ferramenta que entende ANSI (ex: `... | less -R`).


## Alterando Caminho padrão do .env

__*development.env*__
```env
JAYLOG_APP_NAME=meu-bot
JAYLOG_LOG_DIR=C:\logs
```

__*main.py*__
```python
from jaylog import JaylogSettings, configure, get_logger

configure(JaylogSettings(_env_file='development.env'))
logger = get_logger()

logger.info("Alterando Caminho padrão do .env")
```


## Preparando para produção

> [!IMPORTANT]
> **HTTP_ENDPOINT** e **HTTP_API_KEY** (opcionais) 📢
>
> - A configuração **HTTP_ENDPOINT** e **HTTP_API_KEY** não precisa ser feita em ambiente local ou de desenvolvimento
> - Se apenas uma das duas variáveis **HTTP_ENDPOINT** ou **HTTP_API_KEYS** for definida, o envio HTTP é ignorado.
> - Caso a aplicação execute em um ambiente que usa um proxy ntlm, defina `JAYLOG_LOG_HTTP_PROXY`


> [!TIP]
> Em produção é recomendado usar um diretório específico para suas secrets, para que você possa reutilizar entre diferentes aplicações.

### Cenário 1 (secrets definido hardcode no código):

__*prodution.env*__
```env
JAYLOG_APP_NAME=meu-bot
JAYLOG_LOG_DIR=C:\logs
```
```bash
$ pwd
/foo/bar/secrets

$ ls -la
JAYLOG_LOG_HTTP_ENDPOIN
JAYLOG_LOG_HTTP_API_KEY
JAYLOG_LOG_HTTP_PROXY
```

__*main.py*__
```python
from jaylog import JaylogSettings, configure, get_logger

configure(JaylogSettings(
    _env_file='prodution.env',
    _secrets_dir='/foo/bar/secrets/'
))
logger = get_logger()

logger.info("Mensagem de log")
```

### Cenário 2 (secrets definido no .env):

__*prodution.env*__
```env
JAYLOG_APP_NAME=meu-bot
JAYLOG_LOG_DIR=C:\logs
JAYLOG_SECRETS_DIR=/foo/bar/secrets
```

__*main.py*__
```python
from jaylog import JaylogSettings, configure, get_logger

# nesse caso é necessário usar a função de classe `reload_secrets`
# pois o diretorio dos secrets foi passado via variável de ambiente
configure(JaylogSettings(_env_file='prodution.env').reload_secrets())
logger = get_logger()

logger.info("Mensagem de log")
