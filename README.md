# jaylog

Biblioteca de logging para Python com rotação de arquivos e envio HTTP para um endpoint remoto.

## Instalação

```bash
pip install -U --no-cache-dir git+https://github.com/Gpocas/jaylog.git
```

## Variáveis de ambiente

As variáveis usam o prefixo `JAYLOG_`. Podem ser definidas no ambiente do sistema ou em um arquivo `.env` / `.env.logging` na raiz do projeto.

| Variável                        | obrigatorio? | Padrão    | Descrição                                                               |
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


## Uso Simples

__*.env.logging*__
```env
JAYLOG_APP_NAME=meu-bot
JAYLOG_LOG_DIR=C:\logs
```
__*main.py*__
```python
from jaylog import JaylogSettings, get_logger

logger = get_logger(JaylogSettings())

logger.info("Mensagem de log")
logger.error("Erro ao processar")
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

logger.info("Mensagem de log")
logger.error("Erro ao processar")
```


## Preparando para produção


> [!IMPORTANT]
> **HTTP_ENDPOINT** e **HTTP_API_KEY** (opcionais) 📢
>
> - A configuração **HTTP_ENDPOINT** e **HTTP_API_KEY** não precisa ser feita em ambiente local ou de desenvolvimento
> - Se apenas uma das duas variaveis **HTTP_ENDPOINT** ou **HTTP_API_KEYS** for definida, o envio HTTP é ignorado.
> - Caso a aplicação execute em um ambiente que usa um proxy ntlm, defina `JAYLOG_LOG_HTTP_PROXY`


__*prodution.env*__
```env
JAYLOG_APP_NAME=mlleu-bot
JAYLOG_LOG_DIR=C:\logs
JAYLOG_LOG_HTTP_ENDPOINT=https://meu-backend.com/logs/add
JAYLOG_LOG_HTTP_API_KEY=minha-chave
JAYLOG_LOG_HTTP_PROXY=http://username:password@proxy.com:8080
```

__*main.py*__
```python
from jaylog import JaylogSettings, get_logger


settings = JaylogSettings(_env_file='prodution.env', _secrets_dir='/caminho/secrets/')
logger = get_logger(settings)

logger.info("Mensagem de log")
logger.error("Erro ao processar")
```


