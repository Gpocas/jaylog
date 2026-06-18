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
| `JAYLOG_LOG_DIR`                | SIM          | `null`    | Caminho do diretório onde os arquivos de log serão salvos               |
| `JAYLOG_LOG_LEVEL`              | NÃO          | `INFO`    | Nível mínimo de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)   |
| `JAYLOG_LOG_MAX_BYTES`          | NÃO          | `5242880` | Tamanho máximo do arquivo de log antes de rotacionar (bytes)            |
| `JAYLOG_LOG_BACKUP_COUNT`       | NÃO          | `5`       | Quantidade de arquivos de backup mantidos após rotação                  |
| `JAYLOG_LOG_RETENTION_DAYS`     | NÃO          | `7`       | Dias para manter arquivos de log antigos                                |
| `JAYLOG_LOG_HTTP_TIMEOUT`       | NÃO          | `5.0`     | Timeout em segundos para o envio HTTP                                   |
| `JAYLOG_LOG_HTTP_ENDPOINT`      | NÃO          | `null`    | URL do endpoint que receberá os logs                                    |
| `JAYLOG_LOG_HTTP_API_KEY`       | NÃO          | `null`    | Chave de autenticação enviada no header `x-api-key`                     |
| `JAYLOG_LOG_HTTP_PROXY`         | NÃO          | `null`    | URL do proxy para o envio HTTP (ex: `http:\\user:password@server:port`) |
| `JAYLOG_LOG_SCREENSHOT_ENABLED` | NÃO          | `false`   | Captura screenshot no momento do log (`true`/`false`, apenas Windows)   |


## Como usar?

Existem alguns cenários diferentes onde a utilização desse lib pode mudar, abaixo estão os cenários mapeados e como realizar configuração para cada um.

## Arquivo único

__*.env.logging*__
```env
JAYLOG_APP_NAME=meu-bot
JAYLOG_LOG_DIR=C:\logs
```
__*main.py*__
```python
from jaylog import JaylogSettings, get_logger

logger = get_logger(JaylogSettings())

logger.info("Arquivo Único")
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

jaylog_settings = JaylogSettings()
configure(jaylog_settings)

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



## Alterando Caminho padrão do .env

__*development.env*__
```env
JAYLOG_APP_NAME=meu-bot
JAYLOG_LOG_DIR=C:\logs
```

__*main.py*__
```python
from jaylog import JaylogSettings, get_logger

settings = JaylogSettings(_env_file='development.env')
logger = get_logger(settings)

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
from jaylog import JaylogSettings, get_logger

settings = JaylogSettings(
    _env_file='prodution.env',
    _secrets_dir='/foo/bar/secrets/'
)
logger = get_logger(settings)

logger.info("Mensagem de log")
```

### Cenário 2 (secrets definido no .env):

__*prodution.env*__
```env
JAYLOG_APP_NAME=meu-bot
JAYLOG_LOG_DIR=C:\logs
JAYLOG_SECRETS_DIRS=/foo/bar/secrets
```

__*main.py*__
```python
from jaylog import JaylogSettings, get_logger

# nesse caso é necessário usar a função de classe `reload_secrets`
# pois o diretorio dos secrets foi passado via variável de ambiente
settings = JaylogSettings(_env_file='prodution.env').reload_secrets() 

logger = get_logger(settings)

logger.info("Mensagem de log")
