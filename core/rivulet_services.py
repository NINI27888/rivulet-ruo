# -*- coding: utf-8 -*-
"""把 Rivulet 若 的本地功能服务挂到「朋友版」core 上。

朋友版的 exe 只拉 core(FastAPI,8088)。我们的功能服务是独立的小 HTTP 服务:
    18095 工作区 / 18096 QQ / 18097 B站 / 18098 人格 / 18099 心情 / 18100 一键部署
这里在 core 启动时把它们拉起来(端口已开就跳过),并用无窗口方式跑。

用法:在 core/main.py 里 `import rivulet_services; rivulet_services.ensure()`。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(BASE)
SERVICES = os.path.join(PKG, "services")

# 端口 -> 脚本
SERVICE_LIST = [
    (18091, "memory/api.py"),
    (int(os.environ.get("RIVULET_WORKSPACE_PORT") or 18095), "workspace/api.py"),
    (18099, "mood_service.py"),
    (18098, "persona_service.py"),
    (18100, "setup_service.py"),
    (18096, "qq_service.py"),
    (18097, "bili_service.py"),
]

# 我们的服务用到的第三方库(websockets / httpx / PIL),放在这个目录里
LIBS = os.environ.get("RIVULET_LIBS") or os.path.join(PKG, "runtime", "Lib", "site-packages")


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(0.25)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _env() -> dict:
    env = dict(os.environ)
    pp = env.get("PYTHONPATH") or ""
    if os.path.isdir(LIBS) and LIBS not in pp:
        env["PYTHONPATH"] = LIBS + (os.pathsep + pp if pp else "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def ensure() -> int:
    """把没起来的服务拉起来,返回本次启动的个数。"""
    if os.environ.get("RIVULET_DESKTOP_MANAGED") == "1":
        return 0
    if not os.path.isdir(SERVICES):
        return 0
    started = 0
    for port, script in SERVICE_LIST:
        if port_open(port):
            continue
        path = os.path.join(SERVICES, script)
        if not os.path.exists(path):
            continue
        try:
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = 0x0800_0000  # CREATE_NO_WINDOW
            subprocess.Popen(
                [sys.executable, path],
                cwd=SERVICES,
                env=_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                **kwargs,
            )
            started += 1
        except Exception:
            pass
    if started:
        time.sleep(1.2)
    return started
