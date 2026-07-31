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
        {"Content-Type": f"multipart/form-data; boundary={bnd}",
         "User-Agent": "DiscordBot (sifang-relay,1)"}), timeout=300)


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
                # 完成：cook 成每人一軌的 zip（container=zip，軌名含使用者名）
                print("cooking", rid, flush=True)
                zpath = os.path.join("/tmp", rid + ".zip")
                with open(zpath, "wb") as out:
                    subprocess.run(["/app/cook.sh", rid, "mp3", "zip"],
                                   stdout=out, timeout=3600, check=True)
                import zipfile
                xdir = os.path.join("/tmp", rid + "-x")
                os.makedirs(xdir, exist_ok=True)
                with zipfile.ZipFile(zpath) as z:
                    z.extractall(xdir)
                tracks = sorted(
                    os.path.join(r, fn)
                    for r, _, fns in os.walk(xdir) for fn in fns
                    if fn.endswith(".mp3") and os.path.getsize(os.path.join(r, fn)) > 10000)
                if not tracks:
                    print("empty cook, skip", rid, flush=True)
                    mark(rid)
                else:
                    if WEBHOOK:
                        names = "、".join(
                            os.path.basename(t).rsplit(".", 1)[0].split("-", 1)[-1]
                            for t in tracks)
                        for i, t in enumerate(tracks):
                            mb = os.path.getsize(t) / 1e6
                            note = (f"🎙 **會議側錄完成**（id {rid}，{len(tracks)} 軌：{names}）\n"
                                    f"每檔一位講者，檔名即講者。待處理：轉逐字稿（標講者）→抽決策/行動項→建卡。"
                                    if i == 0 else
                                    f"（{rid} 第 {i+1}/{len(tracks)} 軌，{mb:.1f}MB）")
                            upload(t, note)
                        print("uploaded", rid, len(tracks), "tracks", flush=True)
                    mark(rid)
                import shutil
                shutil.rmtree(xdir, ignore_errors=True)
                os.remove(zpath)
        except Exception as e:
            print("relay err", e, flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
