import pytest


@pytest.fixture(autouse=True)
def reset_jaylog_state():
    """Isola os caches e threads globais entre os testes."""
    from jaylog import logger
    from jaylog.host import identity, metrics_reporter, reporter
    from jaylog.host.collectors import reset_cache
    from jaylog.screenshot import configure_screenshot, reset_desktop_cache

    logger.shutdown()
    logger._settings_registry.clear()
    identity.reset_cache()
    reset_cache()
    configure_screenshot(False)
    reset_desktop_cache()
    reporter._unsupported_warned = False
    metrics_reporter._unsupported_warned = False
    yield
    logger.shutdown()
    logger._settings_registry.clear()
    identity.reset_cache()
    reset_cache()
    configure_screenshot(False)
    reset_desktop_cache()
    reporter._unsupported_warned = False
    metrics_reporter._unsupported_warned = False
