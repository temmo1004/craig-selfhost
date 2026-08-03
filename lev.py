#!/usr/bin/env python3
"""TurboScribe 的 /_levscript/json：壓縮 JSON＋字串表 的私有 RPC 編解碼。

線路格式（側錄真實上傳得出）：
  body = [ <expr>, [字串表] ]，Content-Type: text/plain
  編碼規則：字串 → [9, 表索引]；陣列 → [1, ...元素]；
            映射 → [7, 鍵索引, 值, 鍵索引, 值, ...]（鍵一律是字串，寫成表索引）
            數字/布林/null → 原樣
  頂層映射：{"invocations":[[fnId, ...args]], "build-id":…, "process-id":…, "revision-id":…}
  已知 fnId：2＝頁面瀏覽追蹤、3＝要求簽章上傳網址
"""
import json, re, secrets


class Enc:
    def __init__(self):
        self.tbl, self.idx = [], {}

    def s(self, x):
        if x not in self.idx:
            self.idx[x] = len(self.tbl)
            self.tbl.append(x)
        return self.idx[x]

    def v(self, x):
        if isinstance(x, str):
            return [9, self.s(x)]
        if isinstance(x, bool) or x is None or isinstance(x, (int, float)):
            return x
        if isinstance(x, list):
            return [1] + [self.v(i) for i in x]
        if isinstance(x, dict):
            out = [7]
            for k, val in x.items():
                out += [self.s(k), self.v(val)]
            return out
        raise TypeError(type(x))


def encode(obj):
    e = Enc()
    body = e.v(obj)
    return json.dumps([body, e.tbl], ensure_ascii=False, separators=(",", ":"))


def decode(raw):
    """把回應的壓縮結構還原成一般 Python 物件。"""
    data = json.loads(raw) if isinstance(raw, str) else raw
    expr, tbl = data[0], data[1]

    def d(x):
        if isinstance(x, list) and x:
            h = x[0]
            if h == 9:
                return tbl[x[1]]
            if h == 1:
                return [d(i) for i in x[1:]]
            if h == 7:
                out, rest = {}, x[1:]
                for i in range(0, len(rest) - 1, 2):
                    k = rest[i]
                    out[tbl[k] if isinstance(k, int) and k < len(tbl) else k] = d(rest[i + 1])
                return out
            if h == 6:          # 集合/序列容器
                return [d(i) for i in x[1:]]
            if h == 0:          # 單值包裝
                return d(x[1]) if len(x) > 1 else None
            if h == 4:          # 關鍵字（字串表索引）
                return tbl[x[1]] if isinstance(x[1], int) and x[1] < len(tbl) else x[1]
            return [d(i) for i in x]
        return x
    return d(expr)


def call(sess, invocations, build_id, revision_id="", process_id=None):
    body = encode({"invocations": invocations,
                   "build-id": build_id,
                   "process-id": process_id or secrets.token_hex(16)[:31],
                   "revision-id": revision_id})
    r = sess.post("https://turboscribe.ai/_levscript/json", data=body.encode(),
                  headers={"Content-Type": "text/plain",
                           "Referer": "https://turboscribe.ai/zh-TW/dashboard"},
                  timeout=120)
    return r.status_code, decode(r.text)


def page_ids(html):
    """從頁面抓 build-id（資產路徑上的 uuid）與 revision-id（若有）。"""
    b = re.search(r"/_content/versioned/([0-9a-f\-]{36})/", html)
    rev = re.search(r'"([0-9a-f]{40})"', html)
    return (b.group(1) if b else ""), (rev.group(1) if rev else "")
