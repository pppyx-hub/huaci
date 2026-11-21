# -*- coding: utf-8 -*-
"""多线程分段下载器（GitHub 单连接限速，用 Range 并发绕过）"""
import os
import sys
import threading
import time
import urllib.request

URL = sys.argv[1]
OUT = sys.argv[2]
CHUNK = 2 * 1024 * 1024          # 每段 2MB —— 实测这个大小超时率最低
THREADS = 16

req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=30) as r:
    SIZE = int(r.headers.get("Content-Length", 0))
print("文件大小: %.1f MB" % (SIZE / 1048576))

n = max(1, (SIZE + CHUNK - 1) // CHUNK)
print("分成 %d 段，每段 %.1f MB" % (n, CHUNK / 1048576))

bufs = [None] * n
ok = [False] * n
done_bytes = [0]
lock = threading.Lock()


def grab(i):
    s = i * CHUNK
    e = min(s + CHUNK, SIZE) - 1
    need = e - s + 1
    for attempt in range(8):
        try:
            rq = urllib.request.Request(URL, headers={
                "Range": "bytes=%d-%d" % (s, e), "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(rq, timeout=60) as r:
                data = r.read()
            if len(data) >= need * 0.98:
                bufs[i] = data
                ok[i] = True
                with lock:
                    done_bytes[0] += len(data)
                    pct = done_bytes[0] * 100 // SIZE
                    print("  段 %2d/%d 完成  累计 %d%%" % (i + 1, n, pct), flush=True)
                return
        except Exception as ex:
            if attempt == 7:
                print("  段 %d 失败: %s" % (i, str(ex)[:40]), flush=True)
        time.sleep(1 + attempt * 0.7)


ts = [threading.Thread(target=grab, args=(i,), daemon=True) for i in range(n)]
t0 = time.time()
for t in ts:
    t.start()
for t in ts:
    t.join()
dt = time.time() - t0

bad = [i for i in range(n) if not ok[i]]
if bad:
    print("失败段:", bad)
    sys.exit(1)

with open(OUT, "wb") as f:
    for b in bufs:
        f.write(b)
real = os.path.getsize(OUT)
print("完成: %d 字节 (期望 %d)  耗时 %.1fs  速度 %.1f MB/s"
      % (real, SIZE, dt, SIZE / 1048576 / dt if dt else 0))
if real != SIZE:
    print("长度不符！")
    sys.exit(1)
