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
                # 完成：cook 成每人一軌（Craig 原生 per-user），檔名即講者
                print("cooking", rid, flush=True)
                env = dict(os.environ)
                env["PATH"] = "/usr/local/bin:" + env.get("PATH", "")
                import zipfile, shutil
                zpath = os.path.join("/tmp", rid + ".zip")
                xdir = os.path.join("/tmp", rid + "-x")
                usertracks = []
                try:
                    with open(zpath, "wb") as out:
                        subprocess.run(["/app/cook.sh", rid, "mp3", "zip"],
                                       stdout=out, timeout=3600, check=True, env=env)
                    os.makedirs(xdir, exist_ok=True)
                    with zipfile.ZipFile(zpath) as z:
                        z.extractall(xdir)
                    usertracks = sorted(
                        os.path.join(r, fn) for r, _, fns in os.walk(xdir) for fn in fns
                        if fn.endswith(".mp3") and os.path.getsize(os.path.join(r, fn)) > 10000)
                except Exception as e:
                    print("per-user cook fail", str(e)[:80], flush=True)
                if not usertracks:  # per-user 失敗才退混音，確保錄音不遺失
                    mixp = os.path.join("/tmp", rid + ".mix.mp3")
                    try:
                        with open(mixp, "wb") as out:
                            subprocess.run(["/app/cook.sh", rid, "mp3", "mix"],
                                           stdout=out, timeout=3600, check=True, env=env)
                        if os.path.getsize(mixp) > 10000:
                            usertracks = [mixp]
                    except Exception as e:
                        print("mix 也失敗", str(e)[:80], flush=True)
                if not usertracks:
                    print("cook 全失敗，保留重試", rid, flush=True)
                    fails[rid] = fails.get(rid, 0) + 1
                    if fails[rid] >= 5:
                        print("放棄", rid, flush=True); mark(rid)
                    time.sleep(30); continue
                # 每一軌：壓 32k 單聲道，>7.5MB 就再切 20 分段（過 Discord 8MB 上限）
                uploads = []  # (檔案, 講者名, 段序, 總段)
                for ut in usertracks:
                    spk = os.path.basename(ut).rsplit(".", 1)[0].split("-", 1)[-1] or "混音"
                    comp = ut + ".c.mp3"
                    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", ut,
                                    "-ac", "1", "-b:a", "32k", comp], check=True, timeout=1800, env=env)
                    if os.path.getsize(comp) <= 7_500_000:
                        uploads.append((comp, spk, 1, 1))
                    else:
                        segpat = ut + "-%03d.mp3"
                        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", comp,
                                        "-f", "segment", "-segment_time", "1200", "-c", "copy", segpat],
                                       check=True, timeout=1800, env=env)
                        segs = sorted(f for f in os.listdir(os.path.dirname(ut))
                                      if f.startswith(os.path.basename(ut) + "-") and f.endswith(".mp3"))
                        for j, sf in enumerate(segs):
                            uploads.append((os.path.join(os.path.dirname(ut), sf), spk, j + 1, len(segs)))
                if WEBHOOK:
                    for i, (f, spk, seg, tot) in enumerate(uploads):
                        mb = os.path.getsize(f) / 1e6
                        seglbl = f"（{seg}/{tot}段）" if tot > 1 else ""
                        note = (f"🎙 **會議側錄完成**（id {rid}，每人一軌，共 {len(usertracks)} 位）\n"
                                f"待處理：轉逐字稿→抽決策/行動項→建卡。\n**講者：{spk}**{seglbl} {mb:.1f}MB"
                                if i == 0 else f"**{spk}**{seglbl} {mb:.1f}MB")
                        upload(f, note)
                    print("uploaded", rid, len(uploads), "檔", flush=True)
                mark(rid)
                shutil.rmtree(xdir, ignore_errors=True)
                for f in [zpath, os.path.join("/tmp", rid + ".mix.mp3")]:
                    try:
                        os.remove(f)
                    except OSError:
                        pass
        except Exception as e:
            print("relay err", e, flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
