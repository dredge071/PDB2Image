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
        e.id === "mono") continue;                    // handled in section 1
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
  }));
  $("#chainBox").classList.remove("hidden");
  $("#formHint").textContent = "";
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
        e.id === "mono") continue;
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
    (j.files || []).includes("flat_palette.ai") || !ENV.illustrator);
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
    j.files.includes("flat_palette.ai") || !ENV.illustrator);
  $("#result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderPreview() {
  if (!jobId) { showPlaceholder(); return; }
  const box = $("#previewBox");
  box.innerHTML = currentFile.endsWith(".svg")
    ? `<object type="image/svg+xml" data="/jobs/${jobId}/${currentFile}?t=${Date.now()}"></object>`
    : `<img src="/jobs/${jobId}/${currentFile}?t=${Date.now()}">`;
}

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
  $("#envBadge").textContent =
    (ENV.pymol_python ? "PyMOL ✓" : "PyMOL ✗") +
    (ENV.illustrator ? " · Illustrator ✓" : " · 无 .ai 导出");
  loadResources();
  showPlaceholder();
  loadHistory();
})();
