#!/usr/bin/env python3
"""會議逐字稿 worker（跑在 basidemac，住宅 IP 過得了 Cloudflare）。

盯 #會議入庫 → 每個「會議側錄完成」批次：把同人分段音檔接回全長 → TurboScribe 轉稿
（每軌單人，關語者分離）→ 合成標名逐字稿貼回 #會議入庫 → TurboScribe 帳號 purge。
狀態存 processed.json（已處理的 header 訊息 id）。launchd 常駐、每 60s 掃一次。

env：DISCORD_BOT_TOKEN、INBOX_CHANNEL_ID、TS_COOKIE
"""
import json, os, re, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ts_cloud as ts

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHAN = os.environ["INBOX_CHANNEL_ID"]
BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "processed.json")
WORK = os.path.join(BASE, "work")
CDN_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
NAME_MAP = {"bath_helloword": "巴斯", "shon0981186963": "Shon",
            "jinnit00": "Joe", "hows8062": "HOWS", "aes1004": "aes1004"}


def dapi(path):
    r = urllib.request.Request(f"https://discord.com/api/v10{path}",
                               headers={"Authorization": f"Bot {TOKEN}",
                                        "User-Agent": "DiscordBot (meeting-tx,1)"})
    return json.load(urllib.request.urlopen(r, timeout=30))


def download(url):
    r = urllib.request.Request(url, headers={"User-Agent": CDN_UA})
    return urllib.request.urlopen(r, timeout=180).read()


def person_key(fn):
    """1-bath_helloword_0.mp3-000.mp3 或 4-aes1004_0.mp3.c.mp3 → (講者名, 全軌鍵, 段序)"""
    stem = re.sub(r"\.c\.mp3$", "", fn)
    m = re.match(r"(.+?\.mp3)(?:-(\d+)\.mp3)?$", stem)
    track = m.group(1)                       # 1-bath_helloword_0.mp3
    seg = int(m.group(2)) if m.group(2) else 0
    acct = re.sub(r"_\d+$", "", track.rsplit(".", 1)[0].split("-", 1)[-1])
    return NAME_MAP.get(acct, acct), track, seg


def load_state():
    try:
        return set(json.load(open(STATE)))
    except Exception:
        return set()


def save_state(s):
    json.dump(sorted(s), open(STATE, "w"))


def post_transcript(note, fname, blob):
    bnd = "----tx" + str(int(time.time()))
    payload = json.dumps({"content": note[:1900]})
    body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n{payload}\r\n"
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
            f"filename=\"{fname}\"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            ).encode() + blob + f"\r\n--{bnd}--\r\n".encode()
    urllib.request.urlopen(urllib.request.Request(
        f"https://discord.com/api/v10/channels/{CHAN}/messages", body,
        {"Content-Type": f"multipart/form-data; boundary={bnd}",
         "Authorization": f"Bot {TOKEN}", "User-Agent": "DiscordBot (meeting-tx,1)"}), timeout=300)


def post_note(text):
    body = json.dumps({"content": text[:1900]}).encode()
    urllib.request.urlopen(urllib.request.Request(
        f"https://discord.com/api/v10/channels/{CHAN}/messages", body,
        {"Content-Type": "application/json",
         "Authorization": f"Bot {TOKEN}", "User-Agent": "DiscordBot (meeting-tx,1)"}), timeout=60)


def process_meeting(rid, msgs):
    """msgs：屬於這場會議、含音檔附件的訊息（時間升冪）。轉稿並貼回。"""
    # 收集附件 → 依講者分組，段序排好
    tracks = {}   # track鍵 → {"spk":名, "segs":{seg序: url}}
    for m in msgs:
        for a in m.get("attachments", []):
            fn = a["filename"]
            if not fn.endswith(".mp3"):
                continue
            spk, track, seg = person_key(fn)
            t = tracks.setdefault(track, {"spk": spk, "segs": {}})
            t["segs"][seg] = a["url"]
    if not tracks:
        return False
    os.makedirs(WORK, exist_ok=True)
    parts = []
    for track, info in sorted(tracks.items()):
        spk = info["spk"]
        full = os.path.join(WORK, track)
        # 同人各段二進位接回全長（-c copy 切出來的，接回=原檔）
        with open(full, "wb") as out:
            for seg in sorted(info["segs"]):
                out.write(download(info["segs"][seg]))
        try:
            s = ts.sess()
            stem = ts.upload_local(s, full, diarize=False)
            txt = ts.wait_and_fetch(s, stem, purge=True, timeout_min=50)
            if txt and txt.strip():
                parts.append((spk, txt.strip()))
                print("轉稿完成", spk, len(txt), "字", flush=True)
            else:
                print("轉稿逾時/空", spk, flush=True)
        except Exception as e:
            print("轉稿單軌失敗", spk, str(e)[:100], flush=True)
        try:
            os.remove(full)
        except OSError:
            pass
    if not parts:
        return False   # 失敗不標完成，交給 scan 重試/放棄
    header = (f"📝 **會議逐字稿**（id {rid}，每人一軌，共 {len(parts)} 位講者）\n"
              f"每段開頭是講者名。抽決策/行動項→建卡交給分析。")
    blob = ""
    for spk, txt in parts:
        blob += f"\n===== {spk} =====\n{txt}\n"
    post_transcript(header, f"{rid}-逐字稿.txt", blob.encode("utf-8"))
    print("逐字稿已貼回", rid, len(parts), "位", flush=True)
    return True


_FAILS = {}


def scan():
    done = load_state()
    msgs = dapi(f"/channels/{CHAN}/messages?limit=100")
    msgs.sort(key=lambda m: m["id"])          # 時間升冪
    # 找「會議側錄完成」header（帶 id RID）
    headers = [(i, m) for i, m in enumerate(msgs)
               if "會議側錄完成" in (m.get("content") or "") and "id " in (m.get("content") or "")]
    for hi, (idx, hm) in enumerate(headers):
        if hm["id"] in done:
            continue
        rmatch = re.search(r"id\s+(\w+)", hm["content"])
        if not rmatch:
            continue
        rid = rmatch.group(1)
        # 這場的訊息：從 header 到下一個 header（或結尾）
        end = headers[hi + 1][0] if hi + 1 < len(headers) else len(msgs)
        batch = msgs[idx:end]
        print("處理會議", rid, "訊息", len(batch), flush=True)
        try:
            if process_meeting(rid, batch):
                done.add(hm["id"]); save_state(done); _FAILS.pop(hm["id"], None)
            else:
                _FAILS[hm["id"]] = _FAILS.get(hm["id"], 0) + 1
                if _FAILS[hm["id"]] >= 3:      # 3 次都失敗才放棄，避免無限重試/洗版
                    done.add(hm["id"]); save_state(done)
                    post_note(f"⚠️ 會議 {rid}：轉稿連 3 次失敗，放棄（見 basidemac worker log）。")
        except Exception as e:
            print("會議處理失敗", rid, str(e)[:120], flush=True)


def main():
    print("meeting_tx worker 啟動；掃描間隔 60s", flush=True)
    # 啟動探針：確認住宅 IP 過得了 Cloudflare
    try:
        print("TS 探針:", "PASS ✅" if ts.probe(ts.sess()) else "FAIL ❌（cookie 過期？）", flush=True)
    except Exception as e:
        print("TS 探針例外", str(e)[:100], flush=True)
    while True:
        try:
            scan()
        except Exception as e:
            print("scan err", str(e)[:120], flush=True)
        time.sleep(60)


if __name__ == "__main__":
    main()
