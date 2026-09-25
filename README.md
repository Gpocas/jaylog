# jaylog

Biblioteca de logging para Python com rotação de arquivos, saída colorida no console, envio HTTP para um endpoint remoto e registro automático do ambiente de execução (host reporting).

📖 **Documentação completa:** https://gpocas.github.io/jaylog-book

## Instalação

```bash
pip install -U --no-cache-dir jaylog
```

## Uso rápido

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

logger.info("Olá, jaylog!")
```

## Métricas de recursos

Com o endpoint HTTP configurado, o jaylog envia a cada minuto o uso de CPU, memória e disco da máquina e do processo (somando os filhos, como Chrome e Excel abertos pelo bot) para `POST /logs/host-metrics`, e os limites da máquina (CPUs, RAM total, disco total) junto com o registro de ambiente. Não exige configuração nova:

| Variável | Padrão | |
|---|---|---|
| `JAYLOG_HOST_METRICS_ENABLED` | `true` | Depende de `JAYLOG_HOST_REPORT_ENABLED` |
| `JAYLOG_HOST_METRICS_INTERVAL` | `60` | Segundos, mínimo 10 |
| `JAYLOG_HOST_METRICS_HTTP_ENDPOINT` | derivado | `/logs/add` → `/logs/host-metrics` |

Para variáveis de ambiente, cenários de uso (console-only, múltiplos loggers, cores), preparação para produção (secrets, host reporting) e referência da API, veja a [documentação guiada](https://gpocas.github.io/jaylog-book).
