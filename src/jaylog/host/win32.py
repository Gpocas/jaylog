"""
Bindings ``ctypes`` do Win32 usados pela detecção de modo de execução.

Duas funções públicas, de propósito: ``session_id()`` e ``process_table()``.
Toda a lógica de classificação vive em ``detect_execution.py`` e mocka essas
duas — assim o teste da classificação roda em Linux sem um grama de Windows.

``ctypes`` é importado **dentro** das funções: o PyInstaller não precisa de
``--hidden-import`` e a importação do jaylog em ambientes exóticos não custa
nada. Fora do Windows as duas degradam para ``None``/``{}`` sem levantar.
"""

import os
import sys

from jaylog.host._safe import safe

MAX_PATH = 260
TH32CS_SNAPPROCESS = 0x00000002

#: Profundidade máxima da subida na cadeia de pais. Corta ciclos causados por
#: reuso de PID (o pai já morreu e o PID foi reciclado num descendente).
MAX_ANCESTRY_DEPTH = 6


def is_windows() -> bool:
    return sys.platform == "win32"


@safe(default=None)
def session_id(pid: int | None = None) -> int | None:
    """
    Sessão do Terminal Services do processo. ``0`` é a sessão isolada onde os
    serviços do Windows rodam (e onde não existe desktop — ver ``screenshot``).

    Devolve ``None`` fora do Windows ou quando a chamada falha: ``None``
    significa "não sabemos", que é diferente de ``0``.
    """
    if not is_windows():
        return None

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

    out = wintypes.DWORD()
    target = os.getpid() if pid is None else pid
    if not kernel32.ProcessIdToSessionId(wintypes.DWORD(target), ctypes.byref(out)):
        return None
    return int(out.value)


@safe(default={})
def process_table() -> dict[int, tuple[int, str]]:
    """
    Snapshot de todos os processos: ``{pid: (ppid, nome_do_executavel)}``.

    ``CreateToolhelp32Snapshot`` devolve PPID **e** nome numa chamada só, sem
    exigir um handle ``PROCESS_QUERY_INFORMATION`` para cada processo — é por
    isso que ele substitui ``NtQueryInformationProcess`` aqui: aquele daria só
    o PPID, e o nome do pai é justamente a evidência que classifica a execução.

    Devolve ``{}`` fora do Windows ou em qualquer falha.
    """
    if not is_windows():
        return {}

    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        # Sem `_pack_`: o alinhamento natural do ctypes é o mesmo do compilador
        # da Microsoft. Forçar pack=1 aqui quebraria o offset de
        # th32ParentProcessID em 64 bits (há padding antes do ULONG_PTR).
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),  # ULONG_PTR
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * MAX_PATH),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    invalid = ctypes.c_void_p(-1).value
    if not snapshot or snapshot == invalid:
        return {}

    table: dict[int, tuple[int, str]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            table[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                str(entry.szExeFile),
            )
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    return table


def ancestry(pid: int, table: dict[int, tuple[int, str]]) -> list[str]:
    """
    Nomes dos processos ancestrais de ``pid``, do pai para cima.

    Para em ``MAX_ANCESTRY_DEPTH``, ao sair da tabela, ou ao reencontrar um PID
    já visitado — reuso de PID pode formar ciclo e o loop não pode depender de
    a árvore estar bem formada.
    """
    chain: list[str] = []
    seen = {pid}
    current = pid
    for _ in range(MAX_ANCESTRY_DEPTH):
        item = table.get(current)
        if item is None:
            break
        ppid, _name = item
        parent = table.get(ppid)
        if parent is None or ppid in seen or ppid == 0:
            break
        seen.add(ppid)
        chain.append(parent[1])
        current = ppid
    return chain


__all__ = ["is_windows", "session_id", "process_table", "ancestry", "MAX_ANCESTRY_DEPTH"]
