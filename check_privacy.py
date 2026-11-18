# -*- coding: utf-8 -*-
"""提交前脱敏自检:扫描发布目录,发现私人数据/凭证/绝对路径即报错。

用法: python check_privacy.py [目录]   (默认脚本所在目录)
退出码 0 = 干净, 1 = 有问题需人工确认。
不输出任何匹配到的实际内容,只报文件名与规则名。
"""
import os, re, sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {".git", "__pycache__", "node_modules", "target"}

# 不该出现在仓库里的运行时数据 / 凭证文件
BAD_FILES = re.compile(
    r"(^|/)(session\.bin|persona\.json|persona\.txt|home\.json|mood\.json|"
    r"qq\.json|bili\.json|qq_whitelist\.json|.*\.jsonl|.*\.db|.*\.db-wal|.*\.db-shm|"
    r".*bili_cookie.*|.*cookie.*\.json|config\.json|\.env.*)$", re.I)

# 源码里不该出现的:绝对路径、疑似真实密钥
# 规则字面量拆开拼接,避免自检脚本自己命中自己
_D = "D:" + "\\" + "deepseek"
_C = "C:" + "\\" + "Users" + "\\"
BAD_CONTENT = [
    ("绝对路径_D盘", re.compile(re.escape(_D) + "|(?:D:/)deepseek")),
    ("绝对路径_C盘用户", re.compile(re.escape(_C) + "|(?:C:/)Users/")),
    ("OpenAI风格真实密钥", re.compile("sk-" + "[A-Za-z0-9]{20,}")),
    ("硬编码长密钥串", re.compile(r'"(api_key|token|secret|password)"\s*:\s*"[A-Za-z0-9_\-]{24,}"', re.I)),
]

TEXT_EXT = {".py", ".js", ".rs", ".json", ".md", ".html", ".css", ".toml", ".sql", ".txt", ".yml", ".yaml"}


def main():
    problems = []
    scanned = 0
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            rel = os.path.relpath(os.path.join(base, fn), ROOT).replace("\\", "/")
            if BAD_FILES.search("/" + rel):
                problems.append((rel, "运行时数据/凭证文件不应入库"))
                continue
            if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                continue
            scanned += 1
            try:
                txt = open(os.path.join(base, fn), encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for label, pat in BAD_CONTENT:
                if pat.search(txt):
                    problems.append((rel, label))

    print(f"扫描文本文件 {scanned} 个")
    if problems:
        print(f"发现 {len(problems)} 处需人工确认(仅列文件名与规则,不显示内容):")
        for rel, why in problems:
            print(f"  - {rel}  [{why}]")
        return 1
    print("脱敏自检通过:未发现私人数据文件、绝对路径或硬编码密钥。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
