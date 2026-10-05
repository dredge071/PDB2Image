# flat_trace 网站工具

把 flat_trace 管线（PyMOL 平涂渲染 → 分层矢量化 → Illustrator 分层 .ai）
打包成本地网页应用：上传 PDB → 表单填参 → 网页里看进度和 SVG 预览 → 下载。

## 架构

```
浏览器（static/ 单页，参数表单由 params_spec.py 自动生成）
   │  upload pdb + form
   ▼
Flask 后端（app.py，任务后台线程，一次跑一个）
   ├─ 阶段① 渲染   : 子进程 PyMOL 环境 python render_flat.py   （需 PyMOL）
   ├─ 阶段② 矢量化 : 子进程 本解释器          vectorize_flat.py
   └─ 阶段③ .ai    : 子进程 protein2vector_flat.py → Illustrator COM（可选）
   │
   └─ 产物 webapp/jobs/<任务id>/  →  /jobs/<id>/<file> 预览与下载
```

**为什么需要后端**：渲染依赖本机 PyMOL（独立 pymol-env）、矢量化依赖
numpy/OpenCV、.ai 导出依赖本机 Illustrator COM，这些都只能在本地跑；
浏览器只负责参数表单、进度日志和预览。

## 运行 / 关闭

双击 **`start_web.bat`**：启动服务器（最小化窗口）并自动打开浏览器；
已在运行则直接开浏览器。双击 **`stop_web.bat`**：结束服务器及其
正在跑的子进程（taskkill /T 连同 PyMOL 一起终止）。

手动方式：

```bash
cd webapp
python app.py
# 打开 http://127.0.0.1:5000
```

PyMOL 环境路径按以下顺序自动探测：环境变量 `FLAT_TRACE_PYMOL_PY` →
当前解释器本身能 `import pymol`（单环境模式，README 安装·方案一）→
仓库旁的 `../pymol-env/python.exe`。两环境模式一般需显式设置
`FLAT_TRACE_PYMOL_PY`。

## 文件

| 文件 | 作用 |
|---|---|
| params_spec.py | **参数单一来源**：所有可变参数 + 内置固定参数；表单和说明表都由它生成 |
| params.md | 参数说明表（`python params_spec.py --md` 重新生成） |
| app.py | Flask 后端：任务创建/轮询/文件下载/.ai 导出；退出时自动终止子进程 |
| start_web.bat / stop_web.bat | 一键启动（含打开浏览器）/ 一键停止（连子进程） |
| static/ | 前端单页（原生 JS，无构建步骤） |
| jobs/ | 每个任务的产物目录（input.pdb、render/、flat_*.svg、*.ai、job.json） |
| 仓库根 `requirements.txt` | 依赖（渲染端 PyMOL 除外） |

## 接口

| 方法/路径 | 作用 |
|---|---|
| GET /api/params | 参数 spec + 环境检测（pymol-env、Illustrator） |
| POST /api/jobs | multipart：pdb 文件 + 表单字段 → `{id}` |
| GET /api/jobs/<id> | 状态 / 当前阶段 / 日志尾部 / 产物列表 |
| POST /api/jobs/<id>/export_ai | 对已完成任务补跑 Illustrator 分层导出 |
| GET /jobs/<id>/<file> | 下载产物（限定在任务目录内） |

## 注意

- 表现模式 `both`（表面+卡通）渲染最慢（surface_quality=1 + 2x 通道）；
  预览建议把渲染宽度降到 1200 或先跑 cartoon 模式。
- 浏览器能直接预览 SVG；`fill-opacity` 半透明外壳在 Illustrator 中打开
  .ai 效果一致（MuPDF 类预览器不支持 clipPath，以 AI 为准）。
- 任务按串行执行（全局锁），日志尾部 60 行随轮询返回。
