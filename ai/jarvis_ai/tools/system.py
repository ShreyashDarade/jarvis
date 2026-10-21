import ctypes
import os
import platform
import shutil
import sys
from typing import Any

from ..domain.ports import Risk


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                ("ext", ctypes.c_ulonglong)]


def _memory_gb() -> tuple[float, float] | None:
    if sys.platform != "win32":
        return None
    status = _MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    gb = 1024 ** 3
    return round(status.total / gb, 1), round(status.avail / gb, 1)


class SystemInfo:
    name = "system_info"
    description = "Read basic facts about this PC: OS, CPU count, memory and free disk space."
    parameters = {"type": "object", "properties": {}}
    risk = Risk.READ

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        info: dict[str, Any] = {"ok": True, "os": platform.platform(), "cpu_count": os.cpu_count()}
        if mem := _memory_gb():
            info["memory_gb_total"], info["memory_gb_free"] = mem
        disk = shutil.disk_usage(os.path.abspath(os.sep))
        info["disk_gb_free"] = round(disk.free / 1024 ** 3, 1)
        return info
