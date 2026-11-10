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

## Certificado TLS (HTTPS)

`JAYLOG_LOG_HTTP_VERIFY` controla a verificação do certificado em todos os envios HTTP (logs, host, métricas, heartbeat e agendas):

| Valor | Efeito |
|---|---|
| `true` (`1`, `yes`, `on`) | Verifica com o bundle padrão do `requests` (`certifi`, ou `REQUESTS_CA_BUNDLE` se definida) |
| `/caminho/corp-ca.pem` | Verifica com esse CA bundle — use para proxy corporativo com inspeção TLS. O caminho precisa existir: senão `JaylogSettings()` falha na inicialização |
| `false` (`0`, `no`, `off`) | Não verifica (inseguro); o jaylog avisa no stderr |

> **0.3.x:** o padrão ainda é `false` por compatibilidade e passa a ser `true` na **0.4.0**. Para antecipar, defina `JAYLOG_LOG_HTTP_VERIFY=true` ou o caminho do bundle corporativo.
