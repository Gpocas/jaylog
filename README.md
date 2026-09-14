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
| `JAYLOG_LOG_HTTP_VERIFY`        | NÃO          | `false`   | `true` ou caminho para o bundle de CA usado para validar TLS            |
| `JAYLOG_LOG_SCREENSHOT_ENABLED` | NÃO          | `false`   | Captura screenshot no momento do log (`true`/`false`, apenas Windows)   |
| `JAYLOG_HOST_REPORT_ENABLED`    | NÃO          | `true`    | Envia uma fotografia do ambiente no startup                             |
| `JAYLOG_HOST_HTTP_ENDPOINT`     | NÃO          | derivado  | Override para o endpoint de host; por padrão `/logs/add` vira `/logs/host` |
| `JAYLOG_HOST_REPORT_TIMEOUT`    | NÃO          | `2 × HTTP_TIMEOUT` | Timeout do POST de ambiente                                     |
| `JAYLOG_HOST_GIT_ENABLED`       | NÃO          | `true`    | Coleta metadados do repositório Git                                     |
| `JAYLOG_HOST_GIT_DIRTY_ENABLED` | NÃO          | `true`    | Coleta se há arquivos alterados                                         |
| `JAYLOG_HOST_GIT_REMOTE_ENABLED`| NÃO          | `true`    | Coleta a URL remota sem credenciais                                     |
| `JAYLOG_HOST_GIT_TIMEOUT`       | NÃO          | `3.0`     | Timeout, em segundos, de cada chamada ao Git                            |
| `JAYLOG_HOST_GIT_DIR`           | NÃO          | `null`    | Diretório inicial para localizar o repositório                          |


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

settings_order = JaylogSettings(app_name="ORDER-PROCESSOR")
settings_billing = JaylogSettings(app_name="BILLING")

configure([settings_order, settings_billing])

logger = get_logger("ORDER-PROCESSOR")  # ou get_logger() — retorna o primeiro registrado
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

configure(JaylogSettings(_env_file="development.env"))
logger = get_logger()

logger.info("Alterando Caminho padrão do .env")
```


## Preparando para produção

## Registro do ambiente

Na versão 0.3, `configure()` inicia em segundo plano um único `POST` JSON para
o endpoint de host por serviço. O corpo dos logs continua compatível com a
linha 0.2.x; apenas os headers `x-jaylog-protocol: 2` e `x-jaylog-run-id` são
adicionados. O `run_id` permite ao backend ligar os logs à execução que os
produziu.

O endpoint é derivado automaticamente trocando o último segmento de
`JAYLOG_LOG_HTTP_ENDPOINT`: `https://api.example/logs/add` vira
`https://api.example/logs/host`. Use `JAYLOG_HOST_HTTP_ENDPOINT` somente quando
o backend publicar a rota em outro endereço.

O registro inclui sistema operacional, modo de execução, Python, virtualenv,
Git e diretórios de execução. A URL remota do Git tem sempre a credencial
embutida removida antes de sair da máquina. Falhas de coleta ou de rede não
interrompem a aplicação: o envio tenta novamente com backoff. Se o backend
aceitar um log mas devolver `x-jaylog-host-required: 1`, o jaylog reenvia o
registro de host com debounce de 30 segundos; o log já foi aceito e não é
reenviado.

`JAYLOG_LOG_HTTP_VERIFY=false` continua sendo o padrão desta versão para não
interromper instalações atrás de proxies corporativos. Para validar TLS, use
`JAYLOG_LOG_HTTP_VERIFY=true` ou informe o caminho do CA bundle corporativo.
O padrão passará a `true` na 0.4.0.

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

configure(JaylogSettings(_env_file="prodution.env", _secrets_dir="/foo/bar/secrets/"))
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
configure(JaylogSettings(_env_file="prodution.env").reload_secrets())
logger = get_logger()

logger.info("Mensagem de log")
```

`reload_secrets()` devolve uma **nova** instância preservando tudo que foi passado explicitamente no construtor, então dá para combinar configuração em código com secrets em disco:

```python
settings = JaylogSettings(app_name="meu-bot", log_level="DEBUG").reload_secrets()
configure(settings)  # app_name e log_level mantidos; endpoint/api_key vêm dos secrets
```

> [!NOTE]
> Valores passados no construtor têm prioridade sobre os secrets: um `log_http_api_key='...'` definido em código **não** é sobrescrito pelo arquivo em `secrets/`.

## Reconfigurando sem repetir argumentos

`reconfigure()` recria a configuração com os mesmos argumentos do construtor, aplicando apenas os overrides informados. Vale tanto para campos quanto para os argumentos de configuração do pydantic-settings (`_env_file`, `_secrets_dir`, `_case_sensitive`, `_env_prefix`, ...):

```python
base = JaylogSettings(app_name="meu-bot", log_level="DEBUG")

homolog = base.reconfigure(_env_file="homolog.env")
prod = base.reconfigure(_env_file="producao.env", log_level="WARNING")
# app_name preservado nos dois; só o que foi informado muda
```

É sobre esse mecanismo que o `reload_secrets()` é construído — ele é só um `reconfigure(_secrets_dir=...)` com validação.
