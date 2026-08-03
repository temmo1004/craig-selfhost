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
    # 強制重做：env CRAIG_REDO=rid1,rid2 → 開機時把它們從 .relayed 移除，重新處理
    redo = [r.strip() for r in os.environ.get("CRAIG_REDO", "").split(",") if r.strip()]
    if redo:
        done = done_set()
        keep = done - set(redo)
        try:
            with open(DONE_F, "w") as f:
                f.write("\n".join(sorted(keep)) + ("\n" if keep else ""))
            print("CRAIG_REDO 解除標記，將重跑:", redo, flush=True)
        except Exception as e:
            print("redo unmark fail", e, flush=True)
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
                # 完成：cook 成單檔混音（TurboScribe 會做語者分離，不必 per-track；
                # 也不撞 Discord 8MB 上限——切成時間段分次上傳）
                print("cooking", rid, flush=True)
                env = dict(os.environ)
                env["PATH"] = "/usr/local/bin:" + env.get("PATH", "")
                mixp = os.path.join("/tmp", rid + ".mix.mp3")
                try:
                    with open(mixp, "wb") as out:
                        subprocess.run(["/app/cook.sh", rid, "mp3", "mix"],
                                       stdout=out, timeout=3600, check=True, env=env)
                except Exception as e:
                    print("mix cook 失敗", str(e)[:80], flush=True)
                if not (os.path.exists(mixp) and os.path.getsize(mixp) > 10000):
                    print("cook 失敗，保留重試", rid, flush=True)
                    fails[rid] = fails.get(rid, 0) + 1
                    if fails[rid] >= 5:
                        print("放棄", rid, flush=True); mark(rid)
                    time.sleep(30)
                    continue
                # 重壓 32kbps 單聲道再切 20 分鐘一段（每段約 4.6MB，穩過 8MB）
                small = os.path.join("/tmp", rid + ".s.mp3")
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mixp,
                                "-ac", "1", "-b:a", "32k", small], check=True, timeout=1800, env=env)
                segpat = os.path.join("/tmp", rid + "-%03d.mp3")
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", small,
                                "-f", "segment", "-segment_time", "1200", "-c", "copy", segpat],
                               check=True, timeout=1800, env=env)
                tracks = sorted(os.path.join("/tmp", f) for f in os.listdir("/tmp")
                                if f.startswith(rid + "-") and f.endswith(".mp3"))
                if not tracks:  # 沒切出段（很短）就傳壓縮後單檔
                    tracks = [small]
                if True:
                    if WEBHOOK:
                        for i, t in enumerate(tracks):
                            mb = os.path.getsize(t) / 1e6
                            note = (f"🎙 **會議側錄完成**（id {rid}，共 {len(tracks)} 段・單檔混音）\n"
                                    f"待處理：轉逐字稿（TurboScribe 標講者）→抽決策/行動項→建卡。"
                                    if i == 0 else f"（{rid} 第 {i+1}/{len(tracks)} 段，{mb:.1f}MB）")
                            upload(t, note)
                        print("uploaded", rid, len(tracks), "tracks", flush=True)
                    mark(rid)
                # 清暫存
                for f in tracks + [mixp, os.path.join("/tmp", rid + ".s.mp3")]:
                    try:
                        os.remove(f)
                    except OSError:
                        pass
        except Exception as e:
            print("relay err", e, flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
