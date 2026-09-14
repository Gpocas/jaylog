"""
Captura de tela anexada a logs de ERROR/EXCEPTION.

Extraído de ``formatters.py``. Duas mudanças de comportamento:

* a captura deixa de acontecer no caminho compartilhado de formatação (ver
  ``context.py``) — só o handler HTTP pede screenshot, porque só ele tem para
  onde mandar. Um deploy console-only ou file-only passa a **nunca** capturar
  tela; antes capturava, e jogava fora;
* em sessão 0 do Windows (serviço iniciado pelo SCM) não existe desktop:
  ``ImageGrab.grab()`` devolve uma imagem preta ou levanta. A captura se
  autodesliga ali, usando o ``session_id()`` que a detecção de execução já
  precisa ler.
"""

import io
import sys

_screenshot_enabled: bool = True
_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
_desktop_available: bool | None = None
_session0_warned = False


def configure_screenshot(enabled: bool) -> None:
    global _screenshot_enabled
    _screenshot_enabled = enabled


def _has_desktop() -> bool:
    """``False`` apenas quando temos certeza de estar na sessão 0 do Windows."""
    global _desktop_available, _session0_warned
    if _desktop_available is None:
        from jaylog.host import win32

        _desktop_available = win32.session_id() != 0
        if not _desktop_available and not _session0_warned:
            _session0_warned = True
            print(
                "[jaylog] screenshot: sessão 0 (serviço do Windows) não tem desktop; "
                "captura desativada",
                file=sys.stderr,
            )
    return _desktop_available


def reset_desktop_cache() -> None:
    """Existe para os testes."""
    global _desktop_available, _session0_warned
    _desktop_available = None
    _session0_warned = False


def capture_screenshot() -> bytes | None:
    """
    JPEG da tela, comprimido até caber em ``_MAX_BYTES``.

    Baixa a qualidade primeiro (85 -> 30) e só então reduz a escala: perder
    nitidez atrapalha menos a leitura de uma mensagem de erro na tela do que
    perder resolução.
    """
    if not _screenshot_enabled:
        return None
    if not _has_desktop():
        return None
    try:
        from PIL import ImageGrab

        img = ImageGrab.grab()

        quality = 85
        scale = 1.0
        buf = io.BytesIO()

        while True:
            buf.seek(0)
            buf.truncate()

            current = img
            if scale < 1.0:
                current = img.resize((int(img.width * scale), int(img.height * scale)))

            current.save(buf, format="JPEG", quality=quality, optimize=True)

            if buf.tell() <= _MAX_BYTES:
                break

            if quality > 30:
                quality -= 10
            elif scale > 0.5:
                scale -= 0.1
            else:
                break

        buf.seek(0)
        return buf.read()
    except Exception as exc:
        print(f"[jaylog] screenshot: {exc}", file=sys.stderr)
        return None


__all__ = ["configure_screenshot", "capture_screenshot", "reset_desktop_cache"]
