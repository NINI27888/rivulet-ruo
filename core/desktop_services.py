"""Start the portable application's local services under the desktop job."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

from rivulet_services import SERVICE_LIST, _env, port_open

CORE = Path(__file__).resolve().parent
ROOT = CORE.parent
SERVICES = ROOT / "services"


def verify_listeners(children):
    if os.name != "nt":
        return
    command = ("Get-NetTCPConnection -State Listen -ErrorAction Stop | "
               "ForEach-Object { '{0}:{1}' -f $_.LocalPort, $_.OwningProcess }")
    snapshot = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        text=True, timeout=4, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    listeners = set(snapshot.splitlines())
    for port, proc in children:
        if f"{port}:{proc.pid}" not in listeners:
            raise RuntimeError(str(port))


def serve():
    # The launcher only opens this gate after assigning this process to its job.
    if sys.stdin.buffer.readline() != b"start\n":
        return

    commands = [(8088, CORE / "main.py")]
    commands.extend((port, SERVICES / script) for port, script in SERVICE_LIST)
    env = _env()
    env["RIVULET_DESKTOP_MANAGED"] = "1"
    env.setdefault("RIVULET_MEMORY", "http://127.0.0.1:18091")
    children = []
    try:
        for port, script in commands:
            if not script.is_file() or port_open(port):
                raise RuntimeError(str(port))
            proc = subprocess.Popen(
                [sys.executable, str(script)], cwd=script.parent if port == 8088 else SERVICES,
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            children.append((port, proc))
        deadline = time.monotonic() + 22
        while True:
            pending = []
            for port, proc in children:
                if proc.poll() is not None:
                    raise RuntimeError(str(port))
                if not port_open(port):
                    pending.append(port)
            if not pending:
                verify_listeners(children)
                print("READY", flush=True)
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(",".join(map(str, pending)))
            time.sleep(0.2)

        # Retain the process so the launcher can track the supervisor's lifetime.
        sys.stdin.buffer.read()
    except Exception as exc:
        print("FAILED " + str(exc), flush=True)
    finally:
        for _, proc in reversed(children):
            if proc.poll() is None:
                proc.terminate()


if __name__ == "__main__":
    serve()
