#!/usr/bin/env python3
"""TurboScribe 雲端版：cookie 從 env TS_COOKIE（= 本機 cookie_header.txt 內容）讀，
不靠 Chrome/yt-dlp。self-contained（內含 levscript 編碼），給 Craig relay 轉稿用。
每軌已是單人 → 預設 diarize=False。"""
import json, os, re, subprocess, tempfile, time

from curl_cffi import requests

BASE = "https://turboscribe.ai"
DASH = f"{BASE}/zh-TW/dashboard"
HX = {"HX-Request": "true", "HX-Current-URL": DASH, "Referer": DASH}
MIME = {".mp3": "audio/mpeg", ".m4a": "audio/x-m4a", ".wav": "audio/wav",
        ".flac": "audio/flac", ".ogg": "audio/ogg", ".mp4": "video/mp4"}


def cookies():
    raw = os.environ.get("TS_COOKIE", "").strip()
    if not raw:
        raise RuntimeError("TS_COOKIE 未設定")
    return dict(p.split("=", 1) for p in raw.split("; ") if "=" in p)


def sess():
    return requests.Session(impersonate="chrome", cookies=cookies())


# ---- levscript /_levscript/json 編碼（要簽章上傳網址用）----
class _Enc:
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


def _lev_encode(obj):
    e = _Enc()
    return json.dumps([e.v(obj), e.tbl], ensure_ascii=False, separators=(",", ":"))


def _lev_decode(raw):
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
                    out[tbl[rest[i][1]] if isinstance(rest[i], list) else rest[i]] = d(rest[i + 1])
                return out
        return x
    return d(expr)


def _lev_call(s, invocations, build_id):
    body = _lev_encode({"invocations": invocations, "build-id": build_id,
                        "process-id": "p", "revision-id": None})
    r = s.post(f"{BASE}/_levscript/json", data=body.encode(),
               headers={"Content-Type": "text/plain", "Referer": DASH}, timeout=120)
    try:
        return r.status_code, _lev_decode(r.text)
    except Exception:
        return r.status_code, {"raw": r.text[:200]}


_BID = {"v": ""}


def build_id(s):
    if _BID["v"]:
        return _BID["v"]
    for _ in range(4):
        h = s.get(DASH, timeout=60).text
        m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", h)
        if m:
            _BID["v"] = m.group(0)
            return _BID["v"]
        time.sleep(2)
    raise RuntimeError("抓不到 build-id（Cloudflare 擋 或 cookie 過期）")


def probe(s):
    """回 True 代表雲端 IP 過得了 Cloudflare 且 cookie 有效。"""
    try:
        return bool(build_id(s))
    except Exception:
        return False


def prep_media(path):
    """轉 48k 單聲道 mp3 縮小上傳；已是小 mp3 就原樣。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3" and os.path.getsize(path) < 8 * 1024 * 1024:
        return path, False
    out = os.path.join(tempfile.gettempdir(),
                       os.path.splitext(os.path.basename(path))[0] + ".t.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vn",
                    "-c:a", "libmp3lame", "-b:a", "48k", "-ac", "1", out],
                   check=True, timeout=3600)
    return out, True


def upload_local(s, path, language="Chinese (Traditional)", diarize=False):
    real, tmp = prep_media(path)
    name = os.path.basename(real)
    size = os.path.getsize(real)
    mime = MIME.get(os.path.splitext(real)[1].lower(), "application/octet-stream")
    code, out = _lev_call(s, [[3, name, None, mime, size, None]], build_id(s))
    res = (out.get("results") or [{}])[0]
    if not res.get("success?"):
        raise RuntimeError(f"要不到上傳網址（{code}）：{str(out)[:150]}")
    blob = open(real, "rb").read()
    handle = None
    for url, h, _ in res["value"][0]:
        hdr = {"Content-Type": mime}
        if "if-none-match" in url:
            hdr["If-None-Match"] = "*"
        try:
            r = s.put(url, data=blob, headers=hdr, timeout=1800)
            if r.status_code in (200, 201, 204):
                handle = h
                break
        except Exception:
            continue
    if tmp:
        try:
            os.remove(real)
        except OSError:
            pass
    if not handle:
        raise RuntimeError("所有候選儲存點都上傳失敗")
    dash = s.get(DASH, timeout=60).text
    form_tok = re.search(r'<form[^>]+hx-post="/_htmx/([^"]+)"', dash).group(1)
    data = {"json:handles": json.dumps([handle]), "language": language,
            "whisper-model": "large-v2", "int:num-speakers": "-1"}
    if diarize:
        data["bool:diarize?"] = "on"
    s.post(f"{BASE}/_htmx/{form_tok}", headers=HX, data=data, timeout=300)
    return os.path.splitext(name)[0]


def listing(s):
    h = s.get(DASH, timeout=60).text
    out, seen = [], set()
    for m in re.finditer(r"/transcript/(\d+)/([A-Za-z0-9\-]+)", h):
        tid = m.group(1)
        if tid in seen:
            continue
        seen.add(tid)
        t = re.search(r'title="([^"]{4,})"', h[m.end():m.end() + 500])
        out.append((tid, m.group(2), t.group(1) if t else ""))
    return out


def fetch_txt(s, tid, slug, purge=False):
    url0 = f"{BASE}/zh-TW/transcript/{tid}/{slug}"
    h = s.get(url0, timeout=90).text
    m = re.search(r'href="(https://turboscribe\.ai/_content/fn/[^"]+\.txt[^"]*)"', h)
    if not m:
        return None
    txt = s.get(m.group(1).replace("&amp;", "&"), timeout=120).text
    if purge and txt:
        _delete(s, tid, slug, h)
    return txt


def _delete(s, tid, slug, page_html=None):
    url0 = f"{BASE}/zh-TW/transcript/{tid}/{slug}"
    h = page_html or s.get(url0, timeout=90).text
    hx = {**HX, "HX-Current-URL": url0, "Referer": url0}
    for full, _tok in dict.fromkeys(re.findall(r'hx-post="(/_htmx/([^"]{20,}))"', h)):
        r = s.post(f"{BASE}{full}", headers=hx, data={}, timeout=60)
        if "刪除" not in r.text:
            continue
        confirm = re.search(r'<form hx-post="(/_htmx/[^"]+)"', r.text)
        if confirm:
            s.post(f"{BASE}{confirm.group(1)}", headers=hx, data={}, timeout=60)
            return True
    return False


def wait_and_fetch(s, stem, purge=True, timeout_min=45):
    slug_hint = stem.lower().replace(" ", "-")[:12]
    for _ in range(timeout_min * 2):
        s2 = sess()
        for tid, slug, title in listing(s2):
            hay = (title or "") + " " + slug
            if stem[:12].lower() in hay.lower() or slug_hint in slug.lower():
                txt = fetch_txt(s2, tid, slug, purge=purge)
                if txt:
                    return txt
        time.sleep(30)
    return None
