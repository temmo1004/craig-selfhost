#!/usr/bin/env python3
"""肆方搬運器：偵測 /app/rec 完成的錄音 → cook.sh 轉 mp3 → 上傳 Discord #會議入庫。
判定完成：.ogg.data 檔 90 秒沒再長大。處理過的記在 /app/rec/.relayed。"""
import json, os, re, subprocess, threading, time, urllib.request

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
    parts, failed = [], []
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
            failed.append(spk)
        except Exception as e:
            print("轉稿單軌失敗", spk, str(e)[:80], flush=True)
            failed.append(f"{spk}（{str(e)[:60]}）")
    if not parts:
        alarm(f"🔴 **會議 `{rid}` 一軌都沒轉出逐字稿**（共 {len(tracks)} 軌）。\n"
              f"失敗：{', '.join(failed) or '見 log'}\n音檔已在庫，逐字稿要重轉。")
    elif failed:
        alarm(f"⚠️ **會議 `{rid}` 逐字稿不完整**：{len(tracks)} 軌只成功 {len(parts)} 軌。\n"
              f"**缺**：{', '.join(failed)}\n"
              f"貼上去的逐字稿少了這幾個人，不要當成完整紀錄。")
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


_ALARMED = set()


def alarm(text, key=None):
    """壞掉要出聲。成功的路徑一直都有通知，失敗的只 print 到沒人看的 stdout——
    8/27 那場 4 小時的會就是這樣沒的：cook 逾時、重試五次、mark() 永久跳過，
    全程只在 log 裡留下兩個字「放棄」。

    key 用來去重：cook 失敗會每分鐘重試，不去重會把頻道洗掉。"""
    if key:
        if key in _ALARMED:
            return
        _ALARMED.add(key)
    print("ALARM:", text.replace("\n", " ")[:160], flush=True)
    _tx_note(text)


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


_MARK_LOCK = threading.Lock()


def mark(rid):
    with _MARK_LOCK:                 # 併發 cook 會同時寫 .relayed
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
    # 併發數由實際核心數決定。cook 是 ffmpeg，CPU-bound：
    # 單核平行只會讓每一場都變慢、總時間不變，還多耗記憶體與 /tmp 空間。
    # 除以 3 而不是跑滿：cook.sh 內部已經分軌併發，外層再開滿只會互搶 CPU；
    # 而且一場 4 小時的錄音在 /tmp 會產生 200MB+ 的 zip 與解開的軌，開太多會塞爆磁碟。
    _cpu = os.cpu_count() or 1
    COOK_PAR = max(2, _cpu // 3)
    print(f"cook 併發：{COOK_PAR}（偵測到 {_cpu} 核）", flush=True)
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
                sz_mb = sz / 1048576
                alarm(f"⏳ 開始處理側錄 `{rid}`（原始檔 {sz_mb:.0f} MB）。"
                      f"每人一軌，長會議可能要跑一小時以上，完成會再通知。",
                      key="start:" + rid)
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
                                       stdout=out, stderr=err, timeout=14400, check=True, env=env)
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
                                           stdout=out, timeout=14400, check=True, env=env)
                        if os.path.getsize(mixp) > 10000:
                            usertracks = [mixp]
                    except Exception as e:
                        print("mix 也失敗", str(e)[:80], flush=True)
                    alarm(f"⚠️ 側錄 `{rid}`：per-user 與 mix 都煮失敗，將重試。\n"
                          f"`{str(e)[:120]}`", key="mixfail:" + rid)
                if not usertracks:
                    print("cook 全失敗，保留重試", rid, flush=True)
                    fails[rid] = fails.get(rid, 0) + 1
                    if fails[rid] >= 5:
                        print("放棄", rid, flush=True); mark(rid)
                        alarm(f"🔴 **側錄處理失敗，已放棄** `{rid}`\n"
                              f"重試 5 次都煮不出音軌，之後不會再自動處理。\n"
                              f"**錄音原始檔還在伺服器的 rec/ 裡，沒有刪掉。**\n"
                              f"要救回來：把服務的 `CRAIG_REDO` 設成 `{rid}` 再重新部署。")
                    time.sleep(30); continue
                # 每一軌：壓 32k 單聲道，>7.5MB 就再切 20 分段（過 Discord 8MB 上限）
                uploads = []  # (檔案, 講者名, 段序, 總段)
                for ut in usertracks:
                    spk = os.path.basename(ut).rsplit(".", 1)[0].split("-", 1)[-1] or "混音"
                    comp = ut + ".c.mp3"
                    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", ut,
                                    "-ac", "1", "-b:a", "32k", comp], check=True, timeout=5400, env=env)
                    if os.path.getsize(comp) <= 7_500_000:
                        uploads.append((comp, spk, 1, 1))
                    else:
                        segpat = ut + "-%03d.mp3"
                        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", comp,
                                        "-f", "segment", "-segment_time", "1200", "-c", "copy", segpat],
                                       check=True, timeout=5400, env=env)
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
                    # 哨兵：轉稿 worker 每 60 秒掃一次，掃到的瞬間如果檔還在陸續上傳，
                    # 它會拿到不完整的一批就開工並把整場標記處理完 —— 8/27 那場 44 個檔
                    # 只轉到第 1 個就是這樣。發完最後一個檔才給這則，worker 看到才動。
                    _tx_note(f"✅ **側錄上傳完畢**（id {rid}，共 {len(uploads)} 檔、"
                             f"{len(usertracks)} 軌）")
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
                        alarm(f"⚠️ 側錄 `{rid}`：音檔已上傳，但轉逐字稿沒有啟動。\n"
                              f"`{str(e)[:120]}`", key="txstart:" + rid)
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
