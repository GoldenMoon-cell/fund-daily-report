# check_privacy.py
import re, pathlib

FILES = [".gitignore", "app.py", "app.spec", "ocr_hold.py",
         "snapshot_view.py", "README.md", "requirements.txt", "使用说明.txt"]

PRIVACY = [
    (r"oapi\.dingtalk|dingtalk|钉钉", "钉钉"),
    (r"access_token|webhook|secret", "token/webhook"),
    (r"password|passwd|api[_-]?key|authorization|bearer", "密码/密钥"),
    (r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", "IP地址"),
    (r"C:[\\/]+Users|D:[\\/]|E:[\\/]|/home/", "本地路径"),
    (r"1[3-9]\d{9}", "手机号"),
    (r"[\w.+-]+@[\w-]+\.(com|cn|net|org)", "邮箱"),
    (r"发我", "私人用语"),
]
STYLE = [
    (r"TODO|FIXME|XXX|HACK", "未完成标记"),
    (r"先这样|临时|暂时|回头|再说|坑死|垃圾|牛逼|傻|无语", "口语化"),
]

def read(p):
    b = p.read_bytes()
    for enc in ("utf-8", "gbk"):
        try: return b.decode(enc)
        except UnicodeDecodeError: pass
    return b.decode("utf-8", errors="ignore")

n = 0
for name in FILES:
    p = pathlib.Path(name)
    if not p.exists():
        print(f"[跳过] {name}"); continue
    for i, line in enumerate(read(p).splitlines(), 1):
        for pat, label in PRIVACY + STYLE:
            if re.search(pat, line, re.I):
                n += 1
                print(f"[{label}] {name}:{i} {line.strip()[:70]}")
print("----")
print(f"可疑点合计: {n}  (0 = 放心上传)")