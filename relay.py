#!/usr/bin/env python3
"""肆方搬運器：偵測 /app/rec 完成的錄音 → cook.sh 轉 mp3 → 上傳 Discord #會議入庫。
判定完成：.ogg.data 檔 90 秒沒再長大。處理過的記在 /app/rec/.relayed。"""
import json, os, re, subprocess, time, urllib.request

REC = "/app/rec"
WEBHOOK = os.environ.get("INBOX_WEBHOOK_URL", "")
DONE_F = os.path.join(REC, ".relayed")

# Craig 帳號 → 真名（逐字稿標籤用；未知的原樣保留）
NAME_MAP = {"bath_helloword": "巴斯", "shon0981186963": "Shon",
            "jinnit00": "Joe", "hows8062": "HOWS", "aes1004": "aes1004"}


def speaker_name(track_basename):
    """1-bath_helloword_0.mp3 → 巴斯。抓中間帳號段，去頭序號與尾 _0。"""
    stem = track_basename.rsplit(".", 1)[0]
    acct = stem.split("-", 1)[-1]
    acct = re.sub(r"_\d+$", "", acct)
    return NAME_MAP.get(acct, acct)


def transcribe_meeting(rid, tracks, txdir):
    """每軌（單人）丟 TurboScribe 轉稿 → 合成標名逐字稿貼回 #會議入庫。
    轉完從 TurboScribe 帳號刪除（帳號零紀錄）。任何步驟失敗只記 log，不影響已上傳的音檔。"""
    try:
        import ts_cloud as ts
    except Exception as e:
        print("轉稿：ts_cloud 匯入失敗", str(e)[:80], flush=True)
        return
    try:
        s = ts.sess()
        if not ts.probe(s):
            print("轉稿：Cloudflare 擋 或 cookie 過期 — 雲端轉稿走不通，音檔已在庫，需本機轉", flush=True)
            _tx_note(f"⚠️ 會議 {rid}：雲端轉稿被 Cloudflare 擋（或 cookie 過期）。"
                     f"音檔已進庫，逐字稿需改本機轉或更新 TS_COOKIE。")
            return
    except Exception as e:
        print("轉稿：session 建立失敗", str(e)[:80], flush=True)
        return
    parts = []
    for t in sorted(tracks):
        spk = speaker_name(os.path.basename(t))
        try:
            stem = ts.upload_local(s, t, diarize=False)
            txt = ts.wait_and_fetch(s, stem, purge=True, timeout_min=45)
            if txt and txt.strip():
                parts.append((spk, txt.strip()))
                print("轉稿完成", spk, len(txt), "字", flush=True)
            else:
                print("轉稿逾時/空", spk, flush=True)
        except Exception as e:
            print("轉稿單軌失敗", spk, str(e)[:80], flush=True)
    if not parts:
        _tx_note(f"⚠️ 會議 {rid}：5 軌都沒轉出逐字稿（見 log）。")
    else:
        body = f"📝 **會議逐字稿**（id {rid}，每人一軌，共 {len(parts)} 位講者）\n" \
               f"以下每段開頭是講者名。抽決策/行動項→建卡交給分析。\n\n"
        blob = ""
        for spk, txt in parts:
            blob += f"\n===== {spk} =====\n{txt}\n"
        _tx_file(body, f"{rid}-逐字稿.txt", blob.encode("utf-8"))
        print("逐字稿已貼回", rid, len(parts), "位", flush=True)
    try:
        import shutil as _sh
        _sh.rmtree(txdir, ignore_errors=True)
    except Exception:
        pass


def _tx_note(text):
    if not WEBHOOK:
        return
    try:
        body = json.dumps({"content": text[:1900], "username": "會議側錄"}).encode()
        urllib.request.urlopen(urllib.request.Request(
            WEBHOOK, body, {"Content-Type": "application/json",
                            "User-Agent": "DiscordBot (sifang-relay,1)"}), timeout=60)
    except Exception as e:
        print("_tx_note fail", str(e)[:60], flush=True)


def _tx_file(note, fname, blob):
    if not WEBHOOK:
        return
    bnd = "----tx" + str(int(time.time()))
    payload = json.dumps({"content": note[:1900], "username": "會議側錄"})
    body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n{payload}\r\n"
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
            f"filename=\"{fname}\"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            ).encode() + blob + f"\r\n--{bnd}--\r\n".encode()
    try:
        urllib.request.urlopen(urllib.request.Request(
            WEBHOOK, body, {"Content-Type": f"multipart/form-data; boundary={bnd}",
                            "User-Agent": "DiscordBot (sifang-relay,1)"}), timeout=300)
    except Exception as e:
        print("_tx_file fail", str(e)[:60], flush=True)


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
    # 開機探針：雲端 IP 過不過 TurboScribe 的 Cloudflare（轉稿能不能在雲上跑的關鍵）
    try:
        import ts_cloud as _ts
        ok = _ts.probe(_ts.sess())
        print("TS 探針:", "PASS 雲端可轉稿 ✅" if ok else "FAIL 被 Cloudflare 擋或 cookie 過期 ❌", flush=True)
    except Exception as _e:
        print("TS 探針 例外:", str(_e)[:100], flush=True)
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
                errlog = os.path.join("/tmp", rid + ".cook.err")
                try:
                    with open(zpath, "wb") as out, open(errlog, "wb") as err:
                        subprocess.run(["/app/cook.sh", rid, "mp3", "zip"],
                                       stdout=out, stderr=err, timeout=3600, check=True, env=env)
                    zsz = os.path.getsize(zpath)
                    os.makedirs(xdir, exist_ok=True)
                    with zipfile.ZipFile(zpath) as z:
                        names = z.namelist()
                        z.extractall(xdir)
                    print("cook zip ok:", zsz, "bytes,", len(names), "entries:", names[:8], flush=True)
                    usertracks = sorted(
                        os.path.join(r, fn) for r, _, fns in os.walk(xdir) for fn in fns
                        if fn.endswith(".mp3") and os.path.getsize(os.path.join(r, fn)) > 10000)
                    print("usertracks 過濾後:", [os.path.basename(u) for u in usertracks], flush=True)
                except Exception as e:
                    try:
                        tail = open(errlog).read()[-1500:]
                    except Exception:
                        tail = "(無 stderr)"
                    print("per-user cook fail:", repr(e)[:120], "\n--- cook stderr ---\n", tail, flush=True)
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
                # 轉稿改由 basidemac 常駐 worker 做（住宅 IP 過 Cloudflare）；
                # 雲端機房 IP 被 Cloudflare 擋，預設不在此轉稿。要開才設 CRAIG_CLOUD_TX=1
                if os.environ.get("CRAIG_CLOUD_TX") == "1":
                    try:
                        txdir = os.path.join("/tmp", rid + "-tx")
                        os.makedirs(txdir, exist_ok=True)
                        fulls = []
                        for ut in usertracks:
                            dst = os.path.join(txdir, os.path.basename(ut))
                            shutil.copy(ut, dst)
                            fulls.append(dst)
                        import threading
                        threading.Thread(target=transcribe_meeting, args=(rid, fulls, txdir),
                                         daemon=True).start()
                        print("轉稿執行緒已開", rid, len(fulls), "軌", flush=True)
                    except Exception as e:
                        print("轉稿啟動失敗", str(e)[:80], flush=True)
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
