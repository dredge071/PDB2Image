/* flat_trace web tool frontend */
"use strict";
let SPEC = [], ENV = {}, RES = null;
let jobId = null, poller = null, currentFile = null;
let chainColors = {};

const $ = (s, el) => (el || document).querySelector(s);
const $$ = (s, el) => [...(el || document).querySelectorAll(s)];

// ---------- params form from /api/params ----------
function ctlFor(e) {
  const v = localStorage.getItem("ft_" + e.id) ?? e.default;
  if (e.type === "bool") {
    return `<input class="ctl-b" type="checkbox" id="f_${e.id}" ${v && v !== "false" ? "checked" : ""}>`;
  }
  if (e.type === "select") {
    return `<select id="f_${e.id}">` + e.options.map(
      o => `<option ${String(o) === String(v) ? "selected" : ""}>${o}</option>`).join("") + "</select>";
  }
  if (e.type === "color") {
    return `<input type="color" id="f_${e.id}" value="${v}">`;
  }
  if (e.type === "int" || e.type === "float") {
    return `<input type="text" id="f_${e.id}" value="${v}" inputmode="decimal">`;
  }
  return `<input type="text" id="f_${e.id}" value="${v || ""}">`;
}

function buildForm() {
  const box = $("#specForm");
  box.innerHTML = "";
  for (const e of SPEC) {
    if (e.type === "file" || e.id === "colors" || e.id === "chains" ||
        e.id === "mono" || e.id === "view" || e.id === "view_angles") continue;                    // handled in section 1
    const div = document.createElement("div");
    div.className = "fld";
    div.dataset.modes = (e.modes || []).join(",");
    const cli = (e.cli || e.id).replace(/_/g, "-");
    div.innerHTML = `<label><span class="lbl">${e.label}
        <span class="cli">${cli}</span></span><span class="ctl">${ctlFor(e)}</span></label>
        <div class="desc">${e.desc}</div>`;
    box.appendChild(div);
  }
  $$("#specForm input, #specForm select").forEach(el =>
    el.addEventListener("change", () => {
      const id = el.id.slice(2);
      const val = el.type === "checkbox" ? el.checked : el.value;
      localStorage.setItem("ft_" + id, val);
      syncModeVisibility();
    }));
  syncModeVisibility();
}

function rep() { return $("#f_rep") ? $("#f_rep").value : "cartoon"; }

function syncModeVisibility() {
  const r = rep();
  $$("#specForm .fld").forEach(d => {
    const m = d.dataset.modes;
    d.classList.toggle("off", !!m && !m.split(",").includes(r));
  });
}

// ---------- idle-resource hint for the workers field ----------
async function loadResources() {
  const el = $("#resHint");
  try {
    RES = await (await fetch("/api/resources")).json();
  } catch (e) { return; }
  const parts = [];
  if (RES.idle_cpu !== null)
    parts.push(`CPU 闲置 ${RES.idle_cpu}%（共 ${RES.cores} 线程）`);
  if (RES.avail_gb !== null) parts.push(`可用内存 ${RES.avail_gb}GB`);
  if (!parts.length) { return; }
  el.innerHTML = `💡 当前资源：${parts.join(" · ")}　→　按 80% 闲置量估算，` +
    `推荐并行进程数 ≤ <b>${RES.recommended}</b>` +
    `<button id="useRec" type="button">采用推荐值</button>` +
    (RES.avail_gb !== null ? `<span style="opacity:.7">（按每进程约 ${RES.per_worker_gb}GB 内存估算）</span>` : "");
  el.classList.add("show");
  $("#useRec").addEventListener("click", () => {
    const f = $("#f_workers");
    if (f) { f.value = RES.recommended; f.dispatchEvent(new Event("change")); }
  });
}

// ---------- PDB upload + chain detection ----------
async function handlePDB(file) {
  const text = await file.text();
  const chains = [];
  for (const ln of text.split("\n"))
    if (ln.startsWith("ATOM") && !chains.includes(ln[21])) chains.push(ln[21]);
  if (!chains.length) { $("#formHint").textContent = "未在该文件中找到 ATOM 链"; return; }
  const pal = SPEC.find(e => e.id === "colors").default;
  chainColors = {};
  chains.forEach((c, i) => chainColors[c] = pal[i % pal.length]);
  $("#chains").value = chains.join(",");
  $("#colorRow").innerHTML = chains.map(c =>
    `<span class="sw">${c}<input type="color" data-ch="${c}"
       value="${chainColors[c]}"></span>`).join("");
  $$("#colorRow input").forEach(inp => inp.addEventListener("input", () => {
    chainColors[inp.dataset.ch] = inp.value;
    if (viewGL) {                       // keep the 3D card colors in sync
      viewGL.setStyle({ chain: inp.dataset.ch },
                      { cartoon: { color: inp.value } });
      viewGL.render();
    }
  }));
  $("#chainBox").classList.remove("hidden");
  $("#formHint").textContent = "";

  // build the 3D view card: replicate the pipeline's auto base, show the
  // molecule in cartoon style, live-rotatable
  viewNChains = chains.length;
  viewBasePdb = prepareViewPdb(text, chains);
  $("#viewCard").classList.remove("hidden");
  $("#f_view_angles").value = "";
  if (viewBasePdb) {
    initViewer();
    refreshViewOut();
    viewCardWarning();
  } else {
    $("#view3d").innerHTML = '<div class="ph">PDB 解析失败</div>';
  }
}

// ---------- collect form ----------
function collect() {
  const fd = new FormData();
  const f = $("#pdbFile").files[0];
  if (!f) { $("#formHint").textContent = "请先上传 PDB 文件"; return null; }
  fd.append("pdb", f);
  const chains = $("#chains").value.trim();
  if (chains) fd.append("chains", chains);
  for (const [ch, c] of Object.entries(chainColors)) fd.append("color_" + ch, c);
  fd.append("mono", $("#mono").checked ? "1" : "0");
  for (const e of SPEC) {
    if (e.type === "file" || e.id === "colors" || e.id === "chains" ||
        e.id === "mono" || e.id === "view" || e.id === "view_angles") continue;
    const el = $("#f_" + e.id);
    if (!el) continue;
    fd.append(e.id, el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value);
  }
  return fd;
}

// ---------- job history (reopen past results in any browser) ----------
async function loadHistory() {
  try {
    const d = await (await fetch("/api/jobs")).json();
    const sel = $("#jobHistory");
    sel.innerHTML = '<option value="">— 选择以前的任务 —</option>' +
      d.jobs.map(j => {
        const tag = j.files.includes("flat_palette.ai") ? " · 含.ai" : "";
        return `<option value="${j.id}">${j.id}${tag}</option>`;
      }).join("");
  } catch (e) { /* server list unavailable */ }
}

$("#jobHistory").addEventListener("change", async e => {
  const id = e.target.value;
  if (!id) return;
  const j = await (await fetch("/api/jobs/" + id)).json();
  jobId = id;
  currentFile = "flat_palette.svg";
  $("#term").classList.remove("hidden");
  $("#log").textContent = j.log.join("\n");
  $("#log").scrollTop = $("#log").scrollHeight;
  const lastStage = j.files.includes("flat_palette.ai") ? "export" : "vectorize";
  setStage(lastStage, "done");
  $("#result").classList.remove("hidden");
  renderPreview();
  $("#downloads").innerHTML = (j.files || []).map(f =>
    `<a href="/jobs/${id}/${f}" download>${f}</a>`).join("");
  $("#aiBtn").classList.toggle("hidden",
    (j.files || []).includes("flat_palette.ai") ||
    (ENV.illustrator === false || !ENV.illustrator_os));
  $("#result").scrollIntoView({ behavior: "smooth", block: "nearest" });
});

// ---------- job polling ----------
function setStage(stage, status) {
  const order = ["render", "vectorize", "export"];
  const cur = order.indexOf(stage);
  $$("#stageBar span").forEach((sp, i) => {
    sp.classList.toggle("active", status === "running" && i === cur);
    sp.classList.toggle("done", (status === "done" && i <= cur) ||
                                (status === "running" && i < cur));
  });
  $("#stageBar").classList.remove("hidden");
  $("#term").classList.remove("hidden");
}

function showPlaceholder() {
  $("#previewBox").innerHTML =
    '<div class="ph">&nbsp;&nbsp;&nbsp;结果预览，<br>任务完成后展示</div>';
}

function showLog(lines) {
  const lg = $("#log");
  lg.textContent = lines.join("\n");
  lg.scrollTop = lg.scrollHeight;
}

async function poll() {
  if (!jobId) return;
  const r = await fetch("/api/jobs/" + jobId);
  if (!r.ok) return;
  const j = await r.json();
  let lines = j.log;
  const waiting = j.status === "queued" ||
                  (j.status === "running" && j.log.length <= 1);
  if (j.status === "queued") {
    setStage("render", "queued");
    lines = [`[排队] 本任务排在第 ${j.queue_pos ?? "?"} 位` +
             (j.queue_len > 1 ? `（共 ${j.queue_len} 个在等）` : "") +
             `，前面的任务完成后自动开始…`, "", ...lines];
  } else if (waiting) {
    // 旧后端没有 queued 状态：running 但日志为空 = 在等全局锁
    setStage("render", "queued");
    lines = ["[排队] 前一个任务正在执行，本任务等待中…", "", ...lines];
  } else {
    setStage(j.stage, j.status);
  }
  showLog(lines);
  if (j.status === "done") {
    stopPoll(); showResult(j);
  } else if (j.status === "canceled") {
    stopPoll();
    setStage("render", "queued");
    showLog([...lines, "", "[已取消] 页面关闭，本任务已从队列移除"]);
    $("#runBtn").disabled = false;
    loadHistory();
  } else if (j.status === "error") {
    stopPoll(); $("#runBtn").disabled = false;
  }
}
function stopPoll() { if (poller) { clearInterval(poller); poller = null; } }

function showResult(j) {
  $("#runBtn").disabled = false;
  $("#result").classList.remove("hidden");
  currentFile = "flat_palette.svg";
  renderPreview();
  $("#downloads").innerHTML = j.files.map(f =>
    `<a href="/jobs/${jobId}/${f}" download>${f}</a>`).join("");
  $("#aiBtn").classList.toggle("hidden",
    j.files.includes("flat_palette.ai") ||
    (ENV.illustrator === false || !ENV.illustrator_os));
  $("#result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderPreview() {
  if (!jobId) { showPlaceholder(); return; }
  const box = $("#previewBox");
  box.innerHTML = currentFile.endsWith(".svg")
    ? `<object type="image/svg+xml" data="/jobs/${jobId}/${currentFile}?t=${Date.now()}"></object>`
    : `<img src="/jobs/${jobId}/${currentFile}?t=${Date.now()}">`;
}

// ---------- 3D view card (merged 视角 + 视角微调) ----------
// The 3Dmol canvas shows the molecule in the render pipeline's BASE frame
// (replicating render_flat.setup_view's auto alignment: center on chain
// centroids, chain-plane normal to +z, first chain to +x). The user
// drags to a final orientation; the net rotation matrix is decomposed
// into x/y/z degrees that PyMOL's cmd.rotate (x, then y, then z, about
// view axes) reproduces - the render pipeline is the same code path, so
// what the canvas shows is what the job renders.
let viewGL = null, viewBasePdb = null, viewNChains = 0;

const mID = () => [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
function mMul(A, B) {
  const C = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++)
      for (let k = 0; k < 3; k++) C[i][j] += A[i][k] * B[k][j];
  return C;
}
function mApply(R, p) {
  return [R[0][0] * p[0] + R[0][1] * p[1] + R[0][2] * p[2],
          R[1][0] * p[0] + R[1][1] * p[1] + R[1][2] * p[2],
          R[2][0] * p[0] + R[2][1] * p[1] + R[2][2] * p[2]];
}
function rodrigues(a, b) {           // matrix rotating vector a onto b
  const nl = v => { const l = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; };
  a = nl(a); b = nl(b);
  const v = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const c = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  if (Math.hypot(v[0], v[1], v[2]) < 1e-9)
    return c > 0 ? mID() : [[-1, 0, 0], [0, -1, 0], [0, 0, -1]];
  const k = (1 - c) / (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
  const vx = [[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]];
  const m = [[1 + vx[0][0], vx[0][1], vx[0][2]],
             [vx[1][0], 1 + vx[1][1], vx[1][2]],
             [vx[2][0], vx[2][1], 1 + vx[2][2]]];
  const v2 = mMul(vx, vx);
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++) m[i][j] += k * v2[i][j];
  return m;
}
function rotZ(deg) {
  const t = deg * Math.PI / 180, c = Math.cos(t), s = Math.sin(t);
  return [[c, -s, 0], [s, c, 0], [0, 0, 1]];
}

function parsePdbAtoms(text) {
  const atoms = [];
  for (const ln of text.split("\n")) {
    if (!ln.startsWith("ATOM  ")) continue;    // pipeline drops HETATM too
    const x = parseFloat(ln.substr(30, 8)), y = parseFloat(ln.substr(38, 8)),
          z = parseFloat(ln.substr(46, 8));
    if (!Number.isFinite(x)) continue;
    atoms.push({ line: ln, ch: ln[21], x, y, z,
                 ca: ln.substr(12, 4).trim() === "CA" });
  }
  return atoms;
}

function prepareViewPdb(text, chains) {
  const atoms = parsePdbAtoms(text);
  if (!atoms.length) return null;
  const cents = chains.map(ch => {
    const pts = atoms.filter(a => a.ch === ch && a.ca);
    const n = pts.length || 1;
    return [pts.reduce((s, a) => s + a.x, 0) / n,
            pts.reduce((s, a) => s + a.y, 0) / n,
            pts.reduce((s, a) => s + a.z, 0) / n];
  }).filter(c => Number.isFinite(c[0]));
  const center = [0, 1, 2].map(i =>
    cents.reduce((s, c) => s + c[i], 0) / cents.length);
  let R = mID();
  if (cents.length >= 3) {                    // matches setup_view's auto
    const n = [(cents[1][0] - cents[0][0]) * (cents[2][1] - cents[0][1]) -
               (cents[1][1] - cents[0][1]) * (cents[2][0] - cents[0][0]),
               (cents[1][1] - cents[0][1]) * (cents[2][2] - cents[0][2]) -
               (cents[1][2] - cents[0][2]) * (cents[2][1] - cents[0][1]),
               (cents[1][2] - cents[0][2]) * (cents[2][0] - cents[0][0]) -
               (cents[1][0] - cents[0][0]) * (cents[2][2] - cents[0][2])];
    R = rodrigues(n, [0, 0, 1]);
    const p = mApply(R, [cents[0][0] - center[0], cents[0][1] - center[1],
                         cents[0][2] - center[2]]);
    R = mMul(rotZ(90 - Math.atan2(p[1], p[0]) * 180 / Math.PI), R);
  }
  const out = [];
  for (const a of atoms) {
    const q = mApply(R, [a.x - center[0], a.y - center[1], a.z - center[2]]);
    out.push(a.line.substr(0, 30) +
             q[0].toFixed(3).padStart(8) + q[1].toFixed(3).padStart(8) +
             q[2].toFixed(3).padStart(8) + a.line.substr(54));
  }
  return out.join("\n");
}

function initViewer() {
  if (typeof $3Dmol === "undefined") {
    $("#view3d").innerHTML = '<div class="ph">3D 组件加载失败</div>';
    return;
  }
  const el = $("#view3d");
  el.innerHTML = "";
  viewGL = $3Dmol.createViewer(el, { backgroundColor: "white" });
  viewGL.addModel(viewBasePdb, "pdb");
  for (const [ch, c] of Object.entries(chainColors))
    viewGL.setStyle({ chain: ch }, { cartoon: { color: c } });
  viewGL.zoomTo();
  viewGL.render();
}

function refreshViewOut() {
  const a = viewAngles();
  $("#viewOut").textContent = `当前视角：${a[0]}°, ${a[1]}°, ${a[2]}°`;
}

// net object->screen rotation of the 3D canvas, decomposed as
// R = Rz(gamma)Ry(beta)Rx(alpha) (the render pipeline rotates x, then y,
// then z about successive view axes). VIEW_SIGN calibrated against real
// renders.
const VIEW_SIGN = { x: 1, y: 1, z: 1 };
function viewAngles() {
  if (!viewGL) return [0, 0, 0];
  // 3Dmol getView() returns 8 floats: [tx, ty, tz, dist, qx, qy, qz, qw]
  // (a quaternion for the model rotation). Convert to a matrix and
  // decompose as R = Rz(gamma)Ry(beta)Rx(alpha), matching the pipeline's
  // cmd.rotate x, then y, then z about view axes.
  const v = viewGL.getView();
  const qx = v[4], qy = v[5], qz = v[6], qw = v[7];
  const R = [
    [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
    [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
    [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
  ];
  const sy = Math.hypot(R[2][1], R[2][2]);
  let a, b, g;
  if (sy < 1e-8) {                            // gimbal lock
    a = 0;
    b = (R[2][1] < 0 ? 90 : -90) * VIEW_SIGN.y;
    g = VIEW_SIGN.z * VIEW_SIGN.x *
      Math.atan2(-R[0][1] * VIEW_SIGN.x, R[1][1] * VIEW_SIGN.y) * 180 / Math.PI;
  } else {
    a = VIEW_SIGN.x * Math.atan2(R[2][1], R[2][2]) * 180 / Math.PI;
    b = VIEW_SIGN.y * Math.atan2(-R[2][0], sy) * 180 / Math.PI;
    g = VIEW_SIGN.z * Math.atan2(R[1][0], R[0][0]) * 180 / Math.PI;
  }
  const r = d => Math.round(d * 10) / 10;
  return [r(a), r(b), r(g)].map(d => (d > 180 ? d - 360 : d < -180 ? d + 360 : d));
}

function applyView() {
  const a = viewAngles();
  $("#f_view_angles").value = (a[0] || a[1] || a[2]) ? a.join(",") : "";
  $("#f_view").value = $("#viewBase").value;
  localStorage.setItem("ft_view", $("#viewBase").value);
  localStorage.setItem("ft_view_angles", $("#f_view_angles").value);
  $("#viewHint").textContent = "已应用：渲染端角度 = " +
    ($("#f_view_angles").value || "0,0,0（不旋转）");
}

function resetView() {
  if (!viewGL) return;
  initViewer();
  $("#f_view_angles").value = "";
  localStorage.setItem("ft_view_angles", "");
  refreshViewOut();
  $("#viewHint").textContent = "";
}

function viewCardWarning() {
  if ($("#viewBase").value !== "auto" || viewNChains < 3)
    $("#viewHint").textContent =
      "注意：orient 基准（或链数 <3 时的 auto）用的是 PyMOL 内部算法，"
      + "此预览无法精确复现，应用的角度请以实际渲染为准。3 链以上建议用 auto。";
}

// ---------- wire up ----------
// ---------- wire up ----------
$("#pdbFile").addEventListener("change", e => e.target.files[0] && handlePDB(e.target.files[0]));
const dz = $("#dropZone");
dz.addEventListener("click", () => $("#pdbFile").click());
dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("over"); });
dz.addEventListener("dragleave", () => dz.classList.remove("over"));
dz.addEventListener("drop", e => {
  e.preventDefault(); dz.classList.remove("over");
  if (e.dataTransfer.files[0]) {
    $("#pdbFile").files = e.dataTransfer.files;
    handlePDB(e.dataTransfer.files[0]);
  }
});
document.addEventListener("change", e => {
  if (e.target.id === "f_rep") syncModeVisibility();
});

// view card wiring (the card owns 视角 + 视角微调; spec form skips both)
function initViewTools() {
  const lv = localStorage.getItem("ft_view");
  if (lv === "auto" || lv === "orient") {
    $("#viewBase").value = lv;
    $("#f_view").value = lv;
  }
  $("#viewBase").addEventListener("change", () => {
    $("#f_view").value = $("#viewBase").value;
    localStorage.setItem("ft_view", $("#viewBase").value);
    viewCardWarning();
  });
  $("#viewApply").addEventListener("click", applyView);
  $("#viewReset").addEventListener("click", resetView);
}

$("#runBtn").addEventListener("click", async () => {
  const fd = collect();
  if (!fd) return;
  $("#runBtn").disabled = true;
  $("#result").classList.add("hidden");
  $("#formHint").textContent = "";
  const r = await fetch("/api/jobs", { method: "POST", body: fd });
  const j = await r.json();
  if (j.error) { $("#formHint").textContent = j.error; $("#runBtn").disabled = false; return; }
  jobId = j.id;
  $("#log").textContent = "";
  setStage("render", "running");
  stopPoll(); poller = setInterval(poll, 1200);
});

$("#aiBtn").addEventListener("click", async () => {
  if (!jobId) return;
  $("#aiBtn").disabled = true;
  await fetch(`/api/jobs/${jobId}/export_ai`, { method: "POST" });
  $("#log").textContent = "";
  setStage("export", "running");
  stopPoll(); poller = setInterval(poll, 1200);
  $("#aiBtn").disabled = false;
});

document.addEventListener("click", e => {
  if (e.target.classList.contains("tabbtn")) {
    $$(".tabbtn").forEach(b => b.classList.remove("active"));
    e.target.classList.add("active");
    currentFile = e.target.dataset.tab;
    renderPreview();
  }
});

// ---------- init ----------
(async () => {
  const d = await (await fetch("/api/params")).json();
  SPEC = d.spec; ENV = d.env;
  buildForm();
  initViewTools();
  $("#envBadge").textContent =
    (ENV.pymol_python ? "PyMOL ✓" : "PyMOL ✗") +
    (ENV.illustrator === true ? " · Illustrator ✓"
      : ENV.illustrator === false ? " · 无 .ai 导出"
      : (ENV.illustrator_os ? " · Illustrator 用时检测" : ""));
  loadResources();
  showPlaceholder();
  loadHistory();
})();
