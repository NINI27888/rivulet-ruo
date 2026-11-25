# -*- coding: utf-8 -*-
r"""NapCat(无头 Shell 版)的开关 —— 全程不弹窗、不要管理员权限。

    import napcat
    nc = napcat.NapCat(napcat.find_dir())      # 找 NapCat 在哪
    nc.start()                                  # 后台起来(没有黑窗)
    nc.qr()                                     # 二维码图片路径
    nc.state()                                  # "qrcode" / "scanned" / "ok" / "error"
    nc.stop()

它的启动方式就是 `node.exe index.js`,不经过 launcher.bat,
所以没有那句「不是管理员就抬权」—— 0 UAC、0 控制台窗口。
"""
import io
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE                                        # Rivulet services 目录
CREATE_NO_WINDOW = 0x08000000
LOG_DIR = os.path.join(HERE, "data", "logs")
DATA_DIR = os.path.join(HERE, "data")

QR_HINT = "手机 QQ 扫这个码登录"
QR_TIMEOUT = 90
QUICK_TIMEOUT = 25
MAX_QR_BYTES = 500_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def log_file():
    return os.path.join(LOG_DIR, "napcat.log")


def pid_file():
    return os.path.join(DATA_DIR, "napcat_pid.txt")


def find_dir(setting_path=""):
    """NapCat 在哪个文件夹:先是设置里写的,再是包里的 NapCat\\。"""
    cands = []
    if setting_path:
        p = setting_path.strip().strip('"')
        if p:
            if os.path.isdir(p):
                cands.append(p)
            else:
                cands.append(os.path.dirname(p))
    for name in ("NapCat", "napcat", "NapCat.Shell"):
        cands.append(os.path.join(ROOT, name))
    for d in cands:
        if d and os.path.isfile(os.path.join(d, "index.js")) and \
                os.path.isfile(os.path.join(d, "node.exe")):
            return d
    for d in cands:
        if d and os.path.isfile(os.path.join(d, "launcher.bat")):
            return d
    return ""


def is_headless(d):
    return bool(d) and os.path.isfile(os.path.join(d, "node.exe")) and \
        os.path.isfile(os.path.join(d, "index.js"))


def uin_from_config(d):
    """登录过的话,config\\onebot11_<uin>.json 会留下 QQ 号。"""
    cfg = os.path.join(d, "napcat", "config")
    best = ""
    best_mt = 0
    try:
        for name in os.listdir(cfg):
            m = re.match(r"^onebot11_(\d+)\.json$", name)
            if not m:
                continue
            mt = os.path.getmtime(os.path.join(cfg, name))
            if mt >= best_mt:
                best_mt = mt
                best = m.group(1)
    except Exception:
        pass
    return best


def _read_config(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("NapCat 配置格式不正确")
    return data


def _write_config(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            json.dump(data, temp, ensure_ascii=False, indent=2)
            temp.close()
            temp_path.replace(path)
        finally:
            temp.close()
            temp_path.unlink(missing_ok=True)


def loopback_webui(d, force_qr=False):
    """限制内置 WebUI 的监听地址。手动重扫时停用其自动登录账号。"""
    path = Path(d, "napcat", "config", "webui.json")
    data = _read_config(path)
    updated = dict(data)
    updated["host"] = "127.0.0.1"
    if force_qr:
        updated["autoLoginAccount"] = ""
    if updated != data:
        _write_config(path, updated)


def ensure_onebot(d, host, port, token):
    """给内置 NapCat 准备新账号模板，并补齐当前安装已有账号的本地连接。"""
    config = Path(d, "napcat", "config")
    files = [config / "onebot11.json"]
    files.extend(path for path in config.glob("onebot11_*.json")
                 if re.fullmatch(r"onebot11_\d+\.json", path.name))
    for path in files:
        _ensure_onebot_file(path, host, port, token)


def _ensure_onebot_file(path, host, port, token):
    data = _read_config(path)
    network = data.setdefault("network", {})
    servers = network.setdefault("websocketServers", [])
    if any(str(server.get("port")) == str(port) for server in servers):
        return
    servers.append({
        "name": "Rivulet 若", "enable": True, "host": host, "port": port,
        "messagePostFormat": "array", "reportSelfMessage": False, "token": token,
        "enableForcePushEvent": True, "debug": False, "heartInterval": 30_000,
    })
    _write_config(path, data)


class NapCat(object):
    def __init__(self, d, logp=""):
        self.d = d or ""
        self._logp = logp or log_file()
        self._proc = None
        self._fh = None
        self._started_at = None

    # ---------- 路径
    def qr(self):
        return os.path.join(self.d, "napcat", "cache", "qrcode.png")

    def qr_bytes(self):
        try:
            with open(self.qr(), "rb") as fp:
                image = fp.read(MAX_QR_BYTES + 1)
        except OSError:
            return b""
        if len(image) <= MAX_QR_BYTES and image.startswith(PNG_SIGNATURE) and image.endswith(PNG_END):
            return image
        return b""

    def qr_timed_out(self):
        return self._started_at is not None and time.monotonic() - self._started_at >= QR_TIMEOUT

    def log(self):
        return self._logp

    # ---------- 进程
    def running(self):
        if self._proc is not None:
            return self._proc.poll() is None
        return bool(self.pid_alive())

    def pid_alive(self):
        try:
            pid = int(io.open(pid_file(), encoding="utf-8").read().strip())
        except Exception:
            return 0
        if pid <= 0:
            return 0
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').ExecutablePath"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=4, creationflags=CREATE_NO_WINDOW)
            actual_path = result.stdout.strip()
            if result.returncode == 0 and actual_path and os.path.samefile(actual_path, Path(self.d, "node.exe")):
                return pid
        except (OSError, subprocess.TimeoutExpired):
            pass
        return 0

    def start(self, uin="", fresh_log=True, *, force_qr=False):
        """后台启动 NapCat，二维码只属于当前启动。"""
        if not is_headless(self.d):
            raise RuntimeError("这不是无头版 NapCat(缺 node.exe 或 index.js):%s" % self.d)
        if self.running():
            return True
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        Path(self.qr()).unlink(missing_ok=True)
        os.makedirs(os.path.dirname(self._logp) or ".", exist_ok=True)
        os.makedirs(DATA_DIR, exist_ok=True)
        mode = "w" if fresh_log else "a"
        self._fh = io.open(self._logp, mode, encoding="utf-8", errors="replace")
        cmd = [os.path.join(self.d, "node.exe"), os.path.join(self.d, "index.js")]
        if uin and not force_qr:
            cmd += ["-q", str(uin)]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        if force_qr:
            for key in ("NAPCAT_QUICK_ACCOUNT", "NAPCAT_QUICK_PASSWORD", "NAPCAT_QUICK_PASSWORD_MD5"):
                env.pop(key, None)
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=self.d, stdin=subprocess.DEVNULL, stdout=self._fh,
                stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW, env=env)
        except OSError:
            self._fh.close()
            self._fh = None
            raise
        self._started_at = time.monotonic()
        try:
            with io.open(pid_file(), "w", encoding="utf-8") as f:
                f.write(str(self._proc.pid))
        except Exception:
            pass
        return True

    def stop(self, wait=3):
        if self._proc is not None:
            pid = self._proc.pid if self._proc.poll() is None else 0
        else:
            pid = self.pid_alive()
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=4, creationflags=CREATE_NO_WINDOW)
            deadline = time.monotonic() + wait
            while self.running():
                if time.monotonic() >= deadline:
                    raise RuntimeError("旧的 QQ 登录组件仍在运行，请稍后重试。")
                time.sleep(0.2)
        if self._fh is not None:
            self._fh.close()
        self._fh = None
        self._proc = None
        self._started_at = None
        Path(pid_file()).unlink(missing_ok=True)

    # ---------- 日志
    def tail(self, n=260):
        try:
            lines = io.open(self._logp, encoding="utf-8", errors="replace").read().split("\n")
        except Exception:
            return ""
        return "\n".join(lines[-n:])

    def state(self):
        """从日志尾巴上判断走到哪一步了。

        qrcode  等他扫码
        scanned 手机扫了,等确认
        ok      登上了
        error   出错了,附带原因
        none    还什么都没出来
        """
        txt = self.tail(300)
        if not txt:
            return ("none", "")
        lines = [x for x in txt.split("\n") if x.strip()]
        for line in reversed(lines[-80:]):
            if "当前账号(" in line and "已登录" in line:
                return ("error", "此 QQ 账号已在另一个 NapCat 实例登录。先退出旧实例，再重试。")
            if "[Core] [Login] Login Error" in line or "登录失败:" in line:
                if "安全风险" in line:
                    return ("error", "QQ 提示账号存在安全风险，请在手机 QQ 完成验证后重试。")
                if "二维码已过期" in line or re.search(r"ErrType:\s*1\s+ErrCode:\s*3\b", line):
                    return ("error", "二维码已过期，正在等待刷新。也可以点「重新生成二维码」。")
                return ("error", "QQ 登录失败，请查看手机 QQ 上的提示后重试。")
            if "已通知主进程登录成功" in line or "Worker进程已登录成功" in line:
                return ("ok", "登录成功")
            if "二维码已被扫描" in line:
                return ("scanned", "扫到了,手机上点一下「确认登录」")
            if "二维码已保存到" in line:
                return ("qrcode", QR_HINT)
        return ("none", "")

    def wait(self, want=("qrcode",), timeout=QR_TIMEOUT, on_state=None, stop=None):
        """等日志走到 want 里的某一步。stop 是个回调,返回 True 就放弃。"""
        end = time.time() + timeout
        last = None
        while time.time() < end:
            if stop is not None and stop():
                return ("stopped", "停下了")
            st = self.state()
            if st[0] != last:
                last = st[0]
                if on_state is not None:
                    try:
                        on_state(st[0], st[1])
                    except Exception:
                        pass
            if st[0] in want:
                return st
            if st[0] == "error":
                return st
            time.sleep(0.5)
        return self.state()
