#!/usr/bin/env python3
"""TurboScribe 雲端版：cookie 從 env TS_COOKIE（= 本機 cookie_header.txt 內容）讀，
不靠 Chrome/yt-dlp。self-contained（內含 levscript 編碼），給 Craig relay 轉稿用。
每軌已是單人 → 預設 diarize=False。"""
import json, os, re, subprocess, tempfile, time

from curl_cffi import requests
import lev

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
    """轉 48k 單聲道 mp3 縮小上傳；已是小 mp3 就原樣。
    沒 ffmpeg（如 basidemac）或轉檔失敗 → 原檔直傳（TurboScribe 不受 Discord 8MB 限制）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3" and os.path.getsize(path) < 8 * 1024 * 1024:
        return path, False
    out = os.path.join(tempfile.gettempdir(),
                       os.path.splitext(os.path.basename(path))[0] + ".t.mp3")
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vn",
                        "-c:a", "libmp3lame", "-b:a", "48k", "-ac", "1", out],
                       check=True, timeout=3600)
        return out, True
    except (FileNotFoundError, subprocess.SubprocessError):
        return path, False


def upload_local(s, path, language="Chinese (Traditional)", diarize=False):
    real, tmp = prep_media(path)
    name = os.path.basename(real)
    size = os.path.getsize(real)
    mime = MIME.get(os.path.splitext(real)[1].lower(), "application/octet-stream")
    code, out = lev.call(s, [[3, name, None, mime, size, None]], build_id(s))
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
