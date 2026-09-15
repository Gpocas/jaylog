# jaylog

Biblioteca de logging para Python com rotação de arquivos, saída colorida no console, envio HTTP para um endpoint remoto e registro automático do ambiente de execução (host reporting).

📖 **Documentação completa:** https://gpocas.github.io/jaylog-book

## Instalação

```bash
pip install -U --no-cache-dir jaylog
```

## Uso rápido

> [!IMPORTANT]
> A partir da versão 0.2.2, `configure()` **deve** ser chamado antes de `get_logger()`.
> Chamar `get_logger()` sem configuração prévia lança uma exceção.

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

Para variáveis de ambiente, cenários de uso (console-only, múltiplos loggers, cores), preparação para produção (secrets, host reporting) e referência da API, veja a [documentação guiada](https://gpocas.github.io/jaylog-book).
