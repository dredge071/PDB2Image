# flat_trace — PyMOL 平涂 → 分层矢量 管线

把 PyMOL 的平涂渲染（无光影，ambient=1）临摹成分层 SVG，再导出
Illustrator 分层 .ai：每条链一层（Chain_A/B/C），层内含 cartoon 与
surface 子组；cartoon / surface / both 三种表现模式可选。

## 目录结构

| 位置 | 内容 |
|---|---|
| `render_flat.py` | 阶段① 渲染（pymol-env 运行）：输出各链掩膜、雾深图、mode-1 墨线、表面通道。通道拆分给多个 PyMOL 进程并行渲染（`--workers`，默认自动=4；`--workers 1` 串行），输出与串行逐字节一致 |
| `vectorize_flat.py` | 阶段② 矢量化（主 Python）：按链填色 + 按链墨线临摹 → `flat_palette.svg` / `flat_mono.svg` |
| `vec_core.py` | 描摹原语（trace_mask / svg_document 等，自 `protein2vector\steps\` 原样抽取），flat_trace 自包含，不再依赖主项目目录 |
| `protein2vector_flat.py` | 阶段③ .ai 导出：驱动本机 Illustrator（COM）转分层 .ai |
| `webapp/` | **网站工具（推荐入口）**：上传 PDB → 表单 → 进度/预览/下载，见 `webapp/README.md` |
| `webapp/params.md` | 全部可调参数 + 内置固定参数的说明表（由 `webapp/params_spec.py` 生成） |
| `tools/` | Illustrator COM 诊断/辅助脚本（jsx+vbs）：分层检查、单层隔离渲染等 |
| `archive/` | 早期实验：`archive/out/` 为迭代过程产物（含 `_mono_full_v7.png` 基准版），`test_surface.py` 为表面通道前身 |
| `out2/` | 当前工作成果（6SZW 三聚体，both 模式 2400px，含最新细墨线 .ai） |

## 快速开始

```
双击 webapp\start_web.bat            → http://127.0.0.1:5000
```

命令行方式：

```bash
# ① 渲染（约数分钟；both 模式表面通道最慢；--layer-mode full 追加每链
#    solo 通道做完整分层，仅 2400 出成品时用，其他宽度自动回退）
D:\A_task\1_Project\1_picture\pymol-env\python.exe render_flat.py ^
    --pdb 6SZW_ABC.pdb --out-dir out2 --rep both --width 2400

# ② 矢量化（输出画布固定 2400 规范空间；--layer-mode full 与渲染端一致）
C:\Python314\python.exe vectorize_flat.py --renders-dir out2 ^
    --chains A,B,C --out-prefix out2/flat --rep both

# ③ 导出分层 .ai（需本机 Illustrator）
C:\Python314\python.exe protein2vector_flat.py --out-dir out2 ^
    --svgs out2/flat_mono.svg,out2/flat_palette.svg --layers Chain_A,Chain_B,Chain_C
```

## 依赖

- 渲染：独立 `pymol-env`（PyMOL 3.1，路径可经 `FLAT_TRACE_PYMOL_PY` 覆盖）
- 矢量化：numpy / opencv-python / pillow / pymupdf（C:\Python314 已装）
- .ai 导出：Windows + 本机 Adobe Illustrator
- 自包含：`vec_core.py` 内置全部描摹原语（自 protein2vector 主项目原样
  抽取，如主项目算法更新且需要同步，重新拷贝并验证）
