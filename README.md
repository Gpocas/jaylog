# jaylog

Biblioteca de logging para Python com rotação de arquivos e envio HTTP para um endpoint remoto.

## Instalação

```bash
pip install -U --no-cache-dir git+https://github.com/Gpocas/jaylog.git
```

## Variáveis de ambiente

As variáveis usam o prefixo `JAYLOG_`. Podem ser definidas no ambiente do sistema ou em um arquivo `.env` / `.env.logging` na raiz do projeto.

### Obrigatórias

| Variável | Descrição |
|---|---|
| `JAYLOG_LOG_DIR` | Caminho do diretório onde os arquivos de log serão salvos |

### HTTP (opcionais, mas obrigatórias em par)

| Variável | Descrição |
|---|---|
| `JAYLOG_LOG_HTTP_ENDPOINT` | URL do endpoint que receberá os logs |
| `JAYLOG_LOG_HTTP_API_KEY` | Chave de autenticação enviada no header `x-api-key` |

> Se apenas uma das duas for definida, o envio HTTP é ignorado.

### Opcionais

| Variável | Padrão | Descrição |
|---|---|---|
| `JAYLOG_APP_NAME` | `app` | Nome do serviço/bot (usado no nome do arquivo de log) |
| `JAYLOG_LOG_LEVEL` | `INFO` | Nível mínimo de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `JAYLOG_LOG_MAX_BYTES` | `5242880` | Tamanho máximo do arquivo de log antes de rotacionar (bytes) |
| `JAYLOG_LOG_BACKUP_COUNT` | `5` | Quantidade de arquivos de backup mantidos após rotação |
| `JAYLOG_LOG_RETENTION_DAYS` | `7` | Dias para manter arquivos de log antigos |
| `JAYLOG_LOG_HTTP_TIMEOUT` | `5.0` | Timeout em segundos para o envio HTTP |
| `JAYLOG_LOG_HTTP_PROXY` | — | URL do proxy para o envio HTTP (ex: `http://proxy.empresa.com:8080`) |
| `JAYLOG_LOG_SCREENSHOT_ENABLED` | `false` | Captura screenshot no momento do log (`true`/`false`, apenas Windows) |

### Proxy do sistema (opcional)

Se o ambiente exigir proxy para acesso externo, defina as variáveis padrão do sistema — o `requests` as lê automaticamente:

```bash
HTTP_PROXY=http://proxy.empresa.com:8080
HTTPS_PROXY=http://proxy.empresa.com:8080
```

## Uso

```python
from jaylog import JaylogSettings, get_logger

logger = get_logger(JaylogSettings())

logger.info("Mensagem de log")
logger.error("Erro ao processar", exc_info=True)
```

## Exemplo de .env

```env
JAYLOG_APP_NAME=meu-bot
JAYLOG_LOG_DIR=C:\logs
JAYLOG_LOG_HTTP_ENDPOINT=https://meu-backend.com/logs/add
JAYLOG_LOG_HTTP_API_KEY=minha-chave
JAYLOG_LOG_HTTP_PROXY=http://proxy.empresa.com:8080
```
