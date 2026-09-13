from pathlib import Path

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jaylog.formatters import _HOST_USERNAME, _HOSTNAME

# Compat: na assinatura anterior de `JaylogSettings.__init__` estes eram os dois
# primeiros parâmetros posicionais. Pode ser removido quando não houver mais
# chamadas posicionais em uso.
_LEGACY_POSITIONAL_ARGS = ('_env_file', '_secrets_dir')


class JaylogSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JAYLOG_",
        env_file=('.env.logging', '.env'),
        env_file_encoding='utf-8',
        secrets_dir='secrets',
        extra="ignore"
    )

    # Tudo que foi recebido no construtor: os campos normais (`app_name=...`) e
    # os argumentos de configuração do pydantic-settings (`_env_file`,
    # `_secrets_dir`, `_case_sensitive`, ... — são 28 e variam por versão).
    # Guardado inteiro para que `reconfigure()` recrie a instância sem perder
    # nada, sem precisar conhecer os nomes um a um.
    _init_values: dict = {}

    def __init__(self, *args, **values) -> None:
        if len(args) > len(_LEGACY_POSITIONAL_ARGS):
            raise TypeError(
                f'{type(self).__name__}() aceita no máximo '
                f'{len(_LEGACY_POSITIONAL_ARGS)} argumentos posicionais'
            )
        for name, value in zip(_LEGACY_POSITIONAL_ARGS, args):
            if name in values:
                raise TypeError(
                    f'{type(self).__name__}() recebeu dois valores para {name!r}'
                )
            values[name] = value

        super().__init__(**values)
        object.__setattr__(self, '_init_values', dict(values))

    def _settings_arg(self, name: str):
        """
        Valor efetivo de um argumento ``_``-prefixado do pydantic-settings:
        o que foi passado no construtor ou, na ausência dele, o que está no
        ``model_config`` (``_env_file`` -> ``env_file``).
        """
        if name in self._init_values:
            return self._init_values[name]
        return self.model_config.get(name.lstrip('_'))

    @property
    def _env_file(self):
        return self._settings_arg('_env_file')

    @property
    def _secrets_dir(self):
        return self._settings_arg('_secrets_dir')

    def reconfigure(self, **overrides) -> "JaylogSettings":
        """
        Recria a instância com os mesmos argumentos do construtor, aplicando
        ``overrides`` por cima.

        Serve para reler a configuração mudando um detalhe sem repetir (nem
        perder) o resto::

            settings = JaylogSettings(app_name='meu-bot', log_level='DEBUG')
            outra = settings.reconfigure(_env_file='producao.env')
            # app_name e log_level preservados, .env trocado

        Vale para qualquer argumento aceito por ``BaseSettings`` — inclusive os
        que esta classe não conhece explicitamente.
        """
        return type(self)(**{**self._init_values, **overrides})

    # App identity
    app_name: str
    log_dir: Path | None = None

    secrets_dir: Path | None = None

    # File handler
    log_level: str = "INFO"
    log_max_bytes: int = 5 * 1024 * 1024  # 5 MB
    log_backup_count: int = 5
    log_retention_days: int = 7

    # HTTP handler
    log_http_endpoint: str | None = None
    log_http_api_key: str | None = None
    log_http_timeout: float = 5.0
    log_http_proxy: str | None = None

    # Console handler — desativar com JAYLOG_LOG_CONSOLE_ENABLED=false
    log_console_enabled: bool = True

    # Cores ANSI no console. `None` (padrão) detecta o suporte do terminal;
    # true/false forçam. Ver JAYLOG_LOG_CONSOLE_COLOR no README.
    log_console_color: bool | None = None

    # Screenshot (log_img field) — desativar com JAYLOG_LOG_SCREENSHOT_ENABLED=false
    log_screenshot_enabled: bool = False

    @field_validator("log_dir", mode="after")
    @classmethod
    def validate_log_dir(cls, v: Path | None) -> Path | None:
        if v is not None and v.exists() and not v.is_dir():
            raise ValueError(f"JAYLOG_LOG_DIR '{v}' exists but is not a directory")
        return v

    @computed_field
    @property
    def log_filename(self) -> Path | None:
        if self.log_dir is None:
            return None
        return Path(f"{self.app_name}_{_HOSTNAME}_{_HOST_USERNAME}.log")

    def reload_secrets(self) -> "JaylogSettings":
        """
        Recarrega a configuração usando o diretório apontado por
        ``JAYLOG_SECRETS_DIR``.

        Necessário porque o diretório de secrets só é conhecido depois que o
        ``.env`` é lido — ou seja, tarde demais para o pydantic-settings usá-lo
        como fonte na primeira instanciação.

        Retorna uma **nova** instância preservando tudo que foi passado
        explicitamente no construtor, para que isto continue funcionando::

            JaylogSettings(app_name='meu-bot').reload_secrets()

        Os valores explícitos continuam tendo prioridade sobre os secrets: um
        ``log_http_api_key`` passado no código não é sobrescrito pelo arquivo.
        """
        if self.secrets_dir is not None:
            if not self.secrets_dir.is_dir():
                raise ValueError(
                    f"JAYLOG_SECRETS_DIR '{self.secrets_dir}' não é um diretório válido"
                )
            return self.reconfigure(_secrets_dir=self.secrets_dir)

        if '_secrets_dir' in self._init_values:
            raise SyntaxError(
                'Não é possivel usar `reload_secrets` caso o valor de _secrets_dir foi sobrescrito'
            )

        # nenhum JAYLOG_SECRETS_DIR definido: relê mantendo o diretório atual,
        # em vez de desligar a leitura de secrets
        return self.reconfigure()
