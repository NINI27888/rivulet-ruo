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
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE                                        # Rivulet services 目录
CREATE_NO_WINDOW = 0x08000000
LOG_DIR = os.path.join(HERE, "data", "logs")
DATA_DIR = os.path.join(HERE, "data")

QR_HINT = "手机 QQ 扫这个码登录"
QR_TIMEOUT = 90
QUICK_TIMEOUT = 25


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


def loopback_webui(d):
    """WebUI 只绑 127.0.0.1。

    NapCat 的 webui.json 默认 host 是 `::`(所有网卡),Windows 防火墙一看有程序
    要在公网上听端口,就弹一个「是否允许 Node.js 访问网络」—— 傻瓜式部署不能有这个。
    只绑回环地址系统就不会问,也不会从外面连进来。
    """
    import json
    p = os.path.join(d, "napcat", "config", "webui.json")
    data = {}
    if os.path.isfile(p):
        try:
            data = json.loads(io.open(p, encoding="utf-8-sig", errors="replace").read() or "{}")
        except Exception:
            data = {}
    if str(data.get("host", "")).strip() in ("127.0.0.1", "localhost"):
        return p
    data["host"] = "127.0.0.1"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
    return p


def write_onebot(d, uin, port=3001, name="大肥鱼"):
    """给这个 QQ 号配一个 OneBot WebSocket 服务端,让机器人能连上来。"""
    cfg = os.path.join(d, "napcat", "config")
    os.makedirs(cfg, exist_ok=True)
    p = os.path.join(cfg, "onebot11_%s.json" % uin)
    data = {}
    if os.path.isfile(p):
        try:
            import json
            data = json.loads(io.open(p, encoding="utf-8-sig", errors="replace").read() or "{}")
        except Exception:
            data = {}
    net = data.setdefault("network", {})
    for k in ("httpServers", "httpSseServers", "httpClients", "websocketClients",
              "plugins"):
        net.setdefault(k, [])
    servers = [s for s in net.get("websocketServers", [])
               if not (isinstance(s, dict) and int(s.get("port") or 0) == int(port))]
    servers.append({
        "name": name, "enable": True, "host": "127.0.0.1", "port": int(port),
        "messagePostFormat": "array", "reportSelfMessage": False, "token": "",
        "enableForcePushEvent": True, "debug": False, "heartInterval": 30000,
    })
    net["websocketServers"] = servers
    data.setdefault("musicSignUrl", "")
    data.setdefault("enableLocalFile2Url", False)
    data.setdefault("parseMultMsg", False)
    import json
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
    return p


class NapCat(object):
    def __init__(self, d, logp=""):
        self.d = d or ""
        self._logp = logp or log_file()
        self._proc = None
        self._fh = None

    # ---------- 路径
    def qr(self):
        return os.path.join(self.d, "napcat", "cache", "qrcode.png")

    def log(self):
        return self._logp

    # ---------- 进程
    def running(self):
        if self._proc is not None and self._proc.poll() is None:
            return True
        return bool(self.pid_alive())

    def pid_alive(self):
        try:
            pid = int(io.open(pid_file(), encoding="utf-8").read().strip())
        except Exception:
            return 0
        if pid <= 0:
            return 0
        try:
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                 capture_output=True, text=True, errors="replace",
                                 creationflags=CREATE_NO_WINDOW).stdout or ""
        except Exception:
            return pid
        return pid if str(pid) in out else 0

    def start(self, uin="", fresh_log=True):
        """后台把 NapCat 起起来。返回 True/False,不抛异常。"""
        if not is_headless(self.d):
            raise RuntimeError("这不是无头版 NapCat(缺 node.exe 或 index.js):%s" % self.d)
        if self.running():
            return True
        os.makedirs(os.path.dirname(self._logp) or ".", exist_ok=True)
        os.makedirs(DATA_DIR, exist_ok=True)
        mode = "w" if fresh_log else "a"
        self._fh = io.open(self._logp, mode, encoding="utf-8", errors="replace")
        cmd = [os.path.join(self.d, "node.exe"), os.path.join(self.d, "index.js")]
        if uin:
            cmd += ["-q", str(uin)]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        self._proc = subprocess.Popen(
            cmd, cwd=self.d, stdin=subprocess.DEVNULL, stdout=self._fh,
            stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW, env=env)
        try:
            with io.open(pid_file(), "w", encoding="utf-8") as f:
                f.write(str(self._proc.pid))
        except Exception:
            pass
        return True

    def stop(self, wait=3):
        pids = []
        if self._proc is not None and self._proc.poll() is None:
            pids.append(self._proc.pid)
        else:
            p = self.pid_alive()
            if p:
                pids.append(p)
        for pid in pids:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, creationflags=CREATE_NO_WINDOW)
            except Exception:
                pass
        t0 = time.time()
        while time.time() - t0 < wait:
            if not self.pid_alive():
                break
            time.sleep(0.2)
        try:
            if self._fh is not None:
                self._fh.close()
        except Exception:
            pass
        self._fh = None
        self._proc = None
        try:
            os.remove(pid_file())
        except Exception:
            pass

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
                return ("error", "这个号已经在别处登着了(NapCat 不能同时开两份)")
            if "[Core] [Login] Login Error" in line or "登录失败:" in line:
                return ("error", line.strip()[-130:])
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
