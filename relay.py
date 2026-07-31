#!/usr/bin/env python3
"""肆方搬運器：偵測 /app/rec 完成的錄音 → cook.sh 轉 mp3 → 上傳 Discord #會議入庫。
判定完成：.ogg.data 檔 90 秒沒再長大。處理過的記在 /app/rec/.relayed。"""
import json, os, subprocess, time, urllib.request

REC = "/app/rec"
WEBHOOK = os.environ.get("INBOX_WEBHOOK_URL", "")
DONE_F = os.path.join(REC, ".relayed")


def done_set():
    try:
        return set(open(DONE_F).read().split())
    except Exception:
        return set()


def mark(rid):
    with open(DONE_F, "a") as f:
        f.write(rid + "\n")


def upload(path, note):
    bnd = "----relay" + str(int(time.time()))
    payload = json.dumps({"content": note[:1900], "username": "會議側錄"})
    blob = open(path, "rb").read()
    body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n{payload}\r\n"
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
            f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: audio/mpeg\r\n\r\n"
            ).encode() + blob + f"\r\n--{bnd}--\r\n".encode()
    urllib.request.urlopen(urllib.request.Request(
        WEBHOOK, body,
        {"Content-Type": f"multipart/form-data; boundary={bnd}"}), timeout=300)


def main():
    sizes = {}
    while True:
        try:
            ids = {fn.split(".")[0] for fn in os.listdir(REC)
                   if fn.endswith(".ogg.data")}
            done = done_set()
            for rid in ids - done:
                p = os.path.join(REC, rid + ".ogg.data")
                sz = os.path.getsize(p)
                prev, ts = sizes.get(rid, (None, 0))
                if sz != prev:
                    sizes[rid] = (sz, time.time())
                    continue
                if time.time() - ts < 90:
                    continue
                # 完成：cook 成 mp3
                print("cooking", rid, flush=True)
                mp3 = os.path.join("/tmp", rid + ".mp3")
                with open(mp3, "wb") as out:
                    subprocess.run(["/app/cook.sh", rid, "mp3", "mix"],
                                   stdout=out, timeout=3600, check=True)
                if os.path.getsize(mp3) < 10000:
                    print("empty cook, skip", rid, flush=True)
                    mark(rid)
                    continue
                if WEBHOOK:
                    mb = os.path.getsize(mp3) / 1e6
                    upload(mp3, f"🎙 **會議側錄完成**（id {rid}，{mb:.1f}MB）\n"
                                f"待處理：轉逐字稿→抽決策/行動項→建卡。")
                    print("uploaded", rid, flush=True)
                mark(rid)
                os.remove(mp3)
        except Exception as e:
            print("relay err", e, flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
