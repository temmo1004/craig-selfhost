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
    fails = {}
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
                env = dict(os.environ)
                env["PATH"] = "/usr/local/bin:" + env.get("PATH", "")  # 讓 cook.sh 找得到 node
                zpath = os.path.join("/tmp", rid + ".zip")
                tracks = []
                try:
                    with open(zpath, "wb") as out:
                        subprocess.run(["/app/cook.sh", rid, "mp3", "zip"],
                                       stdout=out, timeout=3600, check=True, env=env)
                    import zipfile
                    xdir = os.path.join("/tmp", rid + "-x")
                    os.makedirs(xdir, exist_ok=True)
                    with zipfile.ZipFile(zpath) as z:
                        z.extractall(xdir)
                    tracks = sorted(
                        os.path.join(r, fn)
                        for r, _, fns in os.walk(xdir) for fn in fns
                        if fn.endswith(".mp3") and os.path.getsize(os.path.join(r, fn)) > 10000)
                except Exception as e:
                    print("per-track cook fail, 退 mix", str(e)[:80], flush=True)
                # 每人一軌失敗（node 缺等）→ 退回單檔混音（不需 node），確保錄音不遺失
                if not tracks:
                    mixp = os.path.join("/tmp", rid + ".mix.mp3")
                    try:
                        with open(mixp, "wb") as out:
                            subprocess.run(["/app/cook.sh", rid, "mp3", "mix"],
                                           stdout=out, timeout=3600, check=True, env=env)
                        if os.path.getsize(mixp) > 10000:
                            tracks = [mixp]
                            print("mix 後備成功", rid, flush=True)
                    except Exception as e:
                        print("mix cook 也失敗", str(e)[:80], flush=True)
                if not tracks:
                    # 兩種都失敗：這次不標記，留著下輪重試（別讓錄音永久丟失）
                    print("cook 全失敗，保留重試", rid, flush=True)
                    fails[rid] = fails.get(rid, 0) + 1
                    if fails[rid] >= 5:  # 連 5 次都失敗才放棄，免無限迴圈
                        print("放棄", rid, flush=True); mark(rid)
                    time.sleep(30)
                    continue
                if True:
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
                shutil.rmtree(os.path.join("/tmp", rid + "-x"), ignore_errors=True)
                for f in (zpath, os.path.join("/tmp", rid + ".mix.mp3")):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
        except Exception as e:
            print("relay err", e, flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
