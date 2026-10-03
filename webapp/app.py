"""flat_trace web tool - local Flask app wrapping the pipeline.

  upload PDB -> PyMOL render (pymol-env) -> vectorize (this interpreter)
  -> optional layered .ai export via Illustrator COM.

Run:  python app.py   (http://127.0.0.1:5000)

Jobs run one at a time in a background thread; the browser polls
/api/jobs/<id> for stage + log. Every artifact stays inside
webapp/jobs/<id>/ and is served through /jobs/<id>/<file>.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # flat_trace/
sys.path.insert(0, HERE)
from params_spec import SPEC, INTERNAL            # noqa: E402

def _detect_pymol_py():
    """Render-stage interpreter, in order of preference:
    1. FLAT_TRACE_PYMOL_PY (explicit override)
    2. THIS interpreter, if it can import pymol (single-env setup: one
       conda env holds PyMOL and the vectorize/webapp deps alike)
    3. a "pymol-env" venv/conda-prefix sitting NEXT to the repository
    """
    env = os.environ.get("FLAT_TRACE_PYMOL_PY")
    if env:
        return env
    import importlib.util
    if importlib.util.find_spec("pymol") is not None:
        return sys.executable
    return os.path.abspath(
        os.path.join(ROOT, "..", "pymol-env", "python.exe"))


PYMOL_PY = _detect_pymol_py()
RENDER_PY = os.path.join(ROOT, "render_flat.py")
VEC_PY = os.path.join(ROOT, "vectorize_flat.py")
AI_PY = os.path.join(ROOT, "protein2vector_flat.py")
JOBS_DIR = os.path.join(HERE, "jobs")

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

_lock = threading.Lock()          # one pipeline run at a time
_preview_lock = threading.Lock()  # one small view-preview render at a time
_jobs = {}                        # id -> job dict (also mirrored to job.json)
_procs = set()                    # live subprocesses (killed on shutdown)


def _kill_children():
    for p in list(_procs):
        try:
            p.terminate()
        except OSError:
            pass


import atexit
atexit.register(_kill_children)


def detect_chains(pdb_path):
    chains = []
    with open(pdb_path, errors="ignore") as fh:
        for ln in fh:
            if ln.startswith("ATOM"):
                ch = ln[21]
                if ch not in chains:
                    chains.append(ch)
    return chains or ["A"]


def hex_to_rgb01(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def log(job, line):
    job["log"].append(f"[{time.strftime('%H:%M:%S')}] {line}")
    if len(job["log"]) > 800:
        del job["log"][: len(job["log"]) - 800]


def stream(job, stage, args):
    """Run a subprocess, mirroring stdout into the job log."""
    log(job, f"$ {' '.join(str(a) for a in args)}")
    job["stage"] = stage
    p = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", cwd=ROOT)
    _procs.add(p)
    try:
        for ln in p.stdout:
            log(job, ln.rstrip())
        rc = p.wait()
    finally:
        _procs.discard(p)
    if rc:
        raise RuntimeError(f"{stage} 阶段失败，退出码 {rc}（见日志）")
    log(job, f"[{stage}] done")


def collect_colors(form, chains):
    """Per-chain hex colors from the form (fall back to the spec palette)."""
    from params_spec import CHAIN_PALETTE
    out = []
    for i, ch in enumerate(chains):
        c = (form.get(f"color_{ch}") or "").strip()
        out.append(c if re.fullmatch(r"#[0-9A-Fa-f]{6}", c)
                   else CHAIN_PALETTE[i % len(CHAIN_PALETTE)])
    return out


def run_job(job_id, form, pdb_path):
    job = _jobs[job_id]
    jd = job["dir"]
    try:
        with _lock:
            if job["status"] == "canceled":
                return
            job["status"] = "running"
            job["stage"] = "render"
            log(job, "轮到本任务，开始执行")
            chains = detect_chains(pdb_path)
            req = (form.get("chains") or "").strip()
            if req:
                keep = [c.strip() for c in req.split(",") if c.strip()]
                chains = [c for c in chains if c in keep] or chains
            job["chains"] = chains
            if form.get("mono") == "1":
                cols = [collect_colors(form, chains)[0]] * len(chains)
            else:
                cols = collect_colors(form, chains)

            p = {}
            for e in SPEC:
                if e["type"] == "file":
                    continue
                v = form.get(e["id"])
                if e["type"] == "bool":
                    p[e["id"]] = v in ("1", "true", "on")
                elif e["type"] == "int":
                    p[e["id"]] = int(float(v)) if v else e["default"]
                elif e["type"] == "float":
                    p[e["id"]] = float(v) if v else e["default"]
                else:
                    p[e["id"]] = v if v not in (None, "") else e["default"]

            # ---------- stage 1: PyMOL render ----------
            t0 = time.time()
            total = 3 if p["export_ai"] else 2
            rd = os.path.join(jd, "render")
            os.makedirs(rd, exist_ok=True)
            layer_mode = p.get("layer_mode") or "visible"
            if layer_mode == "full" and p["width"] < 2400:
                layer_mode = "visible"
                log(job, "完整分层（full）仅在渲染宽度 2400 下可用，"
                         "本次已自动回退为『仅可见』")
            log(job, f"━━ 第 1/{total} 步 · PyMOL 渲染 ━━")
            args = [PYMOL_PY, RENDER_PY, "--pdb", pdb_path,
                    "--out-dir", rd, "--rep", p["rep"],
                    "--layer-mode", layer_mode,
                    "--view", p["view"],
                    "--width", p["width"], "--height", p["height"]]
            if form.get("chains", "").strip():
                args += ["--chains", ",".join(chains)]
            if p["mono"]:
                args += ["--mono", "1"]
            if p["view_angles"]:
                args += ["--view-angles", p["view_angles"]]
            args += ["--workers", p["workers"]]
            args += ["--colors", ";".join(
                f"{ch}:{','.join(f'{v:g}' for v in hex_to_rgb01(c))}"
                for ch, c in zip(chains, cols))]
            stream(job, "render", args)

            # ---------- stage 2: vectorize ----------
            log(job, f"━━ 第 2/{total} 步 · 矢量化 ━━")
            args = [sys.executable, VEC_PY,
                    "--renders-dir", rd, "--chains", ",".join(chains),
                    "--out-prefix", os.path.join(jd, "flat"),
                    "--rep", p["rep"],
                    "--layer-mode", layer_mode,
                    "--colors", ";".join(f"{ch}:{c}"
                                         for ch, c in zip(chains, cols)),
                    "--depth-bands", p["depth_bands"],
                    "--shade-step", p["shade_step"],
                    "--ink-color", ",".join(str(int(p["ink_color"].lstrip('#')[i:i + 2], 16))
                                            for i in (0, 2, 4)),
                    "--mono-color", p["mono_color"],
                    "--mono-step", p["mono_step"],
                    "--ink-dilate", p["ink_dilate"],
                    "--bg", p["bg"]]
            if p["rep"] == "both":
                args += ["--surf-wash", p["surf_wash"]]
            if p["audit"]:
                args += ["--audit", os.path.join(jd, "audit")]
            stream(job, "vectorize", args)

            # ---------- stage 3 (optional): Illustrator .ai ----------
            if p["export_ai"]:
                if not illustrator_available(probe=True):
                    raise RuntimeError(
                        "未检测到可用的 Illustrator COM（未安装，或安装后未在"
                        "系统注册表注册——部分精简版/绿色版会这样）。"
                        "本次不生成 .ai；不勾选『导出分层 .ai』可正常出 SVG。"
                        "若已安装正式版，重新安装或以管理员运行一次 Illustrator "
                        "通常可恢复注册。")
                log(job, f"━━ 第 3/{total} 步 · Illustrator .ai 导出 ━━")
                args = [sys.executable, AI_PY, "--out-dir", jd,
                        "--svgs", os.path.join(jd, "flat_mono.svg") + "," +
                        os.path.join(jd, "flat_palette.svg"),
                        "--layers", ",".join(f"Chain_{c}" for c in chains)]
                stream(job, "export", args)
                job["files"] = ["flat_palette.svg", "flat_mono.svg",
                                "flat_palette.ai", "flat_mono.ai"]
            else:
                job["files"] = ["flat_palette.svg", "flat_mono.svg"]
            job["files"] += ["input.pdb"]
            job["files"] += [f for f in ("audit_palette.png", "audit_mono.png")
                             if os.path.exists(os.path.join(jd, f))]
            prev = os.path.join(rd, "prev.png")
            if os.path.exists(prev):
                import shutil
                shutil.copy(prev, os.path.join(jd, "prev.png"))
                job["files"].append("prev.png")
            job["status"] = "done"
            job["stage"] = "done"
            log(job, f"[job] 全部 {total} 步完成，"
                     f"用时 {(time.time()-t0)/60:.1f} 分钟")
    except Exception as e:                         # noqa: BLE001
        job["status"] = "error"
        log(job, f"[job] 出错: {e}")
    finally:
        if job["status"] == "canceled":
            shutil.rmtree(jd, ignore_errors=True)   # no artifacts to keep
        else:
            import json
        with open(os.path.join(jd, "job.json"), "w", encoding="utf-8") as fh:
            json.dump({k: job[k] for k in
                       ("id", "status", "stage", "chains", "files", "log")},
                      fh, ensure_ascii=False, indent=1)


# ============================ routes ============================

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/params")
def api_params():
    # illustrator=None: deliberately NOT probed here - probing launches
    # Illustrator; it is checked (and closed again) when an export runs
    return jsonify(spec=SPEC, internal=INTERNAL,
                   env={"pymol_python": os.path.exists(PYMOL_PY),
                        "illustrator": None,
                        "illustrator_os": os.name == "nt"})


PER_WORKER_GB = 1.0      # rough RAM footprint of one PyMOL worker

_ai_com_cache = None     # None = not probed yet


def _run_capture(cmd):
    """subprocess.run with text output that survives the system's GBK
    codepage (tasklist etc. print localized text; default utf-8 decoding
    crashes the reader thread and loses the output)."""
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="mbcs", errors="replace")


def _illustrator_running():
    if os.name != "nt":
        return False
    p = _run_capture(["tasklist", "/FI", "IMAGENAME eq Illustrator.exe"])
    return "Illustrator.exe" in (p.stdout or "")


def illustrator_available(probe=False):
    """Whether Illustrator can actually be driven. Being installed is not
    enough: some installs are missing the COM registration, and then
    CreateObject("Illustrator.Application") fails at export time.

    The COM check LAUNCHES Illustrator, so it is never done just to
    decorate a badge: only on demand (probe=True, i.e. an actual .ai
    export is about to run). If the probe started Illustrator itself,
    it is closed again right away - an Illustrator the user had open
    is left alone."""
    global _ai_com_cache
    if os.name != "nt":
        return False
    if _ai_com_cache is not None:
        return _ai_com_cache
    if not probe:                       # unknown until needed
        return True                     # assume OK; real check at export
    import tempfile
    was_running = _illustrator_running()
    vbs = os.path.join(tempfile.gettempdir(), "flat_ai_probe.vbs")
    with open(vbs, "w") as fh:
        fh.write('On Error Resume Next\n'
                 'CreateObject("Illustrator.Application")\n'
                 'If Err.Number <> 0 Then WScript.Quit 1\n')
    try:
        r = subprocess.run(["cscript", "//nologo", vbs],
                           capture_output=True, text=True,
                           encoding="mbcs", errors="replace", timeout=180)
        _ai_com_cache = (r.returncode == 0)
    except (OSError, subprocess.TimeoutExpired):
        _ai_com_cache = False
    if not was_running and _illustrator_running():
        _run_capture(["taskkill", "/IM", "Illustrator.exe", "/F"])
    return _ai_com_cache


@app.get("/api/resources")
def api_resources():
    """Idle CPU fraction + available RAM -> recommended worker count at
    80% of the currently idle capacity."""
    cores = os.cpu_count() or 4
    idle_frac = avail_gb = None
    try:
        if os.name == "nt":
            import ctypes
            import ctypes.wintypes as wt

            class FT(ctypes.Structure):
                _fields_ = [("lo", wt.DWORD), ("hi", wt.DWORD)]

            def i64(f):
                return (f.hi << 32) | f.lo

            k32 = ctypes.windll.kernel32
            i1, k1, u1 = FT(), FT(), FT()
            k32.GetSystemTimes(ctypes.byref(i1), ctypes.byref(k1),
                               ctypes.byref(u1))
            time.sleep(0.35)
            i2, k2, u2 = FT(), FT(), FT()
            k32.GetSystemTimes(ctypes.byref(i2), ctypes.byref(k2),
                               ctypes.byref(u2))
            busy = ((i64(k2) + i64(u2)) - (i64(k1) + i64(u1)))
            idle_frac = (i64(i2) - i64(i1)) / busy if busy > 0 else 0.0

            class MS(ctypes.Structure):
                _fields_ = [("len", wt.DWORD), ("load", wt.DWORD),
                            ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong),
                            ("tpf", ctypes.c_ulonglong),
                            ("atpf", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong),
                            ("av", ctypes.c_ulonglong),
                            ("aev", ctypes.c_ulonglong)]

            ms = MS()
            ms.len = ctypes.sizeof(MS)
            k32.GlobalMemoryStatusEx(ctypes.byref(ms))
            avail_gb = ms.avail / 1e9
    except Exception:                                          # noqa: BLE001
        pass
    if idle_frac is None:                       # non-windows best effort
        try:
            idle_frac = 1.0 - (os.getloadavg()[0] / max(1, cores))
            idle_frac = max(0.0, min(1.0, idle_frac))
        except (OSError, AttributeError):
            idle_frac = None
    by_cpu = max(1, int(cores * 0.8 * (idle_frac if idle_frac is not None
                                       else 1.0)))
    by_mem = (max(1, int(avail_gb * 0.8 / PER_WORKER_GB))
              if avail_gb is not None else None)
    rec = by_cpu if by_mem is None else max(1, min(by_cpu, by_mem))
    rec = min(rec, 12)
    return jsonify(cores=cores,
                   idle_cpu=round(idle_frac * 100, 1)
                   if idle_frac is not None else None,
                   avail_gb=round(avail_gb, 1) if avail_gb is not None
                   else None,
                   recommended=rec, per_worker_gb=PER_WORKER_GB)


@app.post("/api/preview_view")
def api_preview_view():
    """Render a small flat preview with the given view angles, so the user
    can see an orientation before submitting a job. Uses the real renderer
    (only the `prev` channel, 480px, single worker) - what you see is
    exactly what a job would produce, for every view mode and chain
    count. Serialized: one PyMOL preview at a time."""
    pdb = request.files.get("pdb")
    if pdb is None or not pdb.filename:
        return jsonify(error="缺少 PDB 文件"), 400
    form = request.form
    with _preview_lock:
        tmp = os.path.join(JOBS_DIR, "_viewpreview")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        pdb_path = os.path.join(tmp, "input.pdb")
        pdb.save(pdb_path)
        chains = detect_chains(pdb_path)
        req = (form.get("chains") or "").strip()
        if req:
            keep = [c.strip() for c in req.split(",") if c.strip()]
            chains = [c for c in chains if c in keep] or chains
        from params_spec import CHAIN_PALETTE
        colors = []
        for i, ch in enumerate(chains):
            c = (form.get(f"color_{ch}") or "").strip()
            colors.append(c if re.fullmatch(r"#[0-9A-Fa-f]{6}", c)
                          else CHAIN_PALETTE[i % len(CHAIN_PALETTE)])
        rep = form.get("rep") or "cartoon"
        args = [PYMOL_PY, RENDER_PY, "--pdb", pdb_path, "--out-dir", tmp,
                "--rep", rep, "--view", form.get("view") or "auto",
                "--width", "480", "--height", "427",
                "--chains", ",".join(chains), "--workers", "1",
                "--specs", "prev", "--colors",
                ";".join(f"{ch}:{','.join(f'{v:g}' for v in hex_to_rgb01(c))}"
                         for ch, c in zip(chains, colors))]
        if form.get("mono") == "1":
            args += ["--mono", "1"]
        va = (form.get("view_angles") or "").strip()
        if va:
            args += ["--view-angles", va]
        try:
            r = subprocess.run([str(a) for a in args], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               cwd=ROOT, timeout=300)
        except subprocess.TimeoutExpired:
            return jsonify(error="预览渲染超时"), 500
        prev = os.path.join(tmp, "prev.png")
        if r.returncode or not os.path.isfile(prev):
            tail = ((r.stdout or "") + (r.stderr or "")).strip()[-300:]
            return jsonify(error="预览渲染失败" + (f"：{tail}" if tail else "")), 500
        resp = send_file(prev, mimetype="image/png", max_age=0)
        resp.headers["Cache-Control"] = "no-store"
        return resp


QUEUE_TIMEOUT_S = 10      # queued job with no page heartbeat -> cancel


def _queue_reaper():
    """Cancel queued jobs whose browser page stopped polling (closed).
    Running jobs are never touched - they finish and go to history."""
    while True:
        time.sleep(3)
        now = time.time()
        for jid, j in list(_jobs.items()):
            if j["status"] == "queued" and now - j.get("last_seen", now) > QUEUE_TIMEOUT_S:
                j["status"] = "canceled"
                j["stage"] = "canceled"
                j["log"].append(f"[{time.strftime('%H:%M:%S')}] "
                                "页面已关闭，排队任务已取消")
                shutil.rmtree(j["dir"], ignore_errors=True)
                _jobs.pop(jid, None)
                print(f"[queue] canceled {jid} (page closed)", flush=True)


threading.Thread(target=_queue_reaper, daemon=True).start()


@app.post("/api/jobs")
def api_create_job():
    pdb = request.files.get("pdb")
    if pdb is None or not pdb.filename:
        return jsonify(error="缺少 PDB 文件"), 400
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    jd = os.path.join(JOBS_DIR, job_id)
    os.makedirs(jd, exist_ok=True)
    pdb_path = os.path.join(jd, "input.pdb")
    pdb.save(pdb_path)
    job = dict(id=job_id, status="queued", stage="queued", log=[],
               files=[], chains=[], dir=jd)
    job["last_seen"] = time.time()
    log(job, "任务已创建，排队等待中")
    _jobs[job_id] = job
    threading.Thread(target=run_job, args=(job_id, request.form, pdb_path),
                     daemon=True).start()
    return jsonify(id=job_id)


@app.get("/api/jobs")
def api_job_list():
    """Past jobs (from completed job.json files), newest first."""
    out = []
    if os.path.isdir(JOBS_DIR):
        for d in sorted(os.listdir(JOBS_DIR), reverse=True):
            jf = os.path.join(JOBS_DIR, d, "job.json")
            if os.path.isfile(jf):
                try:
                    meta = json.load(open(jf, encoding="utf-8"))
                    out.append({"id": meta.get("id", d),
                                "status": meta.get("status"),
                                "files": meta.get("files", [])})
                except (OSError, ValueError):
                    continue
    return jsonify(jobs=out[:20])


@app.get("/api/jobs/<job_id>")
def api_job(job_id):
    if not re.fullmatch(r"[\w-]+", job_id):
        abort(404)
    job = _jobs.get(job_id)
    if job is not None:
        job["last_seen"] = time.time()   # page heartbeat (poll = alive)
        queued = [jid for jid, j in _jobs.items() if j["status"] == "queued"]
        qpos = queued.index(job_id) + 1 if job_id in queued else None
        return jsonify(id=job_id, status=job["status"], stage=job["stage"],
                       log=job["log"][-60:], chains=job["chains"],
                       files=job["files"], queue_pos=qpos,
                       queue_len=len(queued))
    # job from an earlier server session: read its metadata from disk
    jf = os.path.join(JOBS_DIR, job_id, "job.json")
    if not os.path.isfile(jf):
        abort(404)
    meta = json.load(open(jf, encoding="utf-8"))
    return jsonify(id=meta.get("id", job_id), status=meta.get("status"),
                   stage=meta.get("stage"), log=meta.get("log", [])[-60:],
                   chains=meta.get("chains", []), files=meta.get("files", []))


@app.post("/api/jobs/<job_id>/export_ai")
def api_export_ai(job_id):
    job = _jobs.get(job_id)
    if job is None or job["status"] != "done":
        return jsonify(error="任务不存在或未完成"), 400
    if os.name != "nt":
        return jsonify(error="仅 Windows 支持 Illustrator 导出"), 400
    if not illustrator_available(probe=True):
        return jsonify(error="未检测到可用的 Illustrator COM（未安装或未注册）"), 400

    def work():
        try:
            with _lock:
                job["status"] = "running"
                job["stage"] = "export"
                chains = job.get("chains") or ["A"]
                args = [sys.executable, AI_PY, "--out-dir", job["dir"],
                        "--svgs", os.path.join(job["dir"], "flat_mono.svg") +
                        "," + os.path.join(job["dir"], "flat_palette.svg"),
                        "--layers", ",".join(f"Chain_{c}" for c in chains)]
                stream(job, "export", args)
                for f in ("flat_palette.ai", "flat_mono.ai"):
                    if f not in job["files"]:
                        job["files"].append(f)
                job["status"] = "done"
                log(job, "[job] .ai 导出完成")
        except Exception as e:                     # noqa: BLE001
            job["status"] = "error"
            log(job, f"[job] 出错: {e}")

    job["status"] = "queued"
    log(job, "排队等待 .ai 导出")
    threading.Thread(target=work, daemon=True).start()
    return jsonify(ok=True)


@app.get("/jobs/<job_id>/<path:fn>")
def job_file(job_id, fn):
    jd = os.path.realpath(os.path.join(JOBS_DIR, job_id))
    if not jd.startswith(os.path.realpath(JOBS_DIR) + os.sep):
        abort(404)
    full = os.path.realpath(os.path.join(jd, fn))
    if not full.startswith(jd + os.sep) or not os.path.isfile(full):
        abort(404)
    return send_from_directory(jd, fn)


if __name__ == "__main__":
    os.makedirs(JOBS_DIR, exist_ok=True)
    print(f"pymol-env : {PYMOL_PY} {'OK' if os.path.exists(PYMOL_PY) else 'MISSING'}")
    print("serving   : http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, threaded=True)
