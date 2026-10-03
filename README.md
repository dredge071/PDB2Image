# flat_trace — PyMOL 平涂渲染 → 分层矢量 管线

把 PyMOL 的平涂渲染（无光影，ambient=1）临摹成分层 SVG，再导出
Illustrator 分层 .ai：每条链一层（Chain_A/B/C），层内含 cartoon 与
surface 子组；cartoon / surface / both 三种表现模式可选。可选
**完整分层**（full layering）：每链额外携带一条隐藏的完整链图层
（被遮挡部分也在），在 Illustrator 里点亮即可就地补全。

## 目录结构

| 位置 | 内容 |
|---|---|
| `render_flat.py` | 阶段① 渲染（PyMOL 环境运行）：输出各链掩膜、雾深图、mode-1 墨线、表面通道。通道拆分给多个 PyMOL 进程并行渲染（`--workers`，默认自动=4；`--workers 1` 串行），输出与串行逐字节一致 |
| `vectorize_flat.py` | 阶段② 矢量化（主 Python）：按链填色 + 按链墨线临摹 → `flat_palette.svg` / `flat_mono.svg` |
| `vec_core.py` | 描摹原语（trace_mask / svg_document 等），flat_trace 自包含，不依赖其他项目目录 |
| `protein2vector_flat.py` | 阶段③ .ai 导出：驱动本机 Illustrator（COM）转分层 .ai |
| `templates/ai_export_template.jsx` | Illustrator 导出用的 JSX 模板 |
| `webapp/` | **网站工具（推荐入口）**：上传 PDB → 表单 → 进度/预览/下载，见 `webapp/README.md` |
| `webapp/params.md` | 全部可调参数 + 内置固定参数的说明表（由 `webapp/params_spec.py` 生成） |
| `docs/full_layering_spec.md` | 完整分层功能的设计规格与实测结论 |

## 安装

1. **主 Python 环境**（矢量化 + 网站工具，≥3.10）：

   ```bash
   pip install -r webapp/requirements.txt
   ```

2. **PyMOL 环境**：渲染在独立的 PyMOL 环境中运行（如 conda 安装的
   `pymol` 或 open-source PyMOL 的 venv，环境名叫 `pymol-env` 会被自动
   发现，也可以任意命名后用环境变量指定，见下节）。

3. **（可选）分层 .ai 导出**：仅 Windows + 已装 Adobe Illustrator
   （COM 接口）。不需要 .ai 时可完全忽略。

## 环境变量

| 变量 | 作用 |
|---|---|
| `FLAT_TRACE_PYMOL_PY` | 渲染端解释器（PyMOL 环境的 python.exe）。默认找仓库旁的 `../pymol-env/python.exe` |
| `FLAT_TRACE_PYTHON` | 网站工具的解释器（`start_web.bat` 使用；不设则用 PATH 里的 python） |

## 快速开始

```
双击 webapp\start_web.bat            → http://127.0.0.1:5000
```

命令行方式：

```bash
# ① 渲染（约数分钟；both 模式表面通道最慢；--layer-mode full 追加每链
#    solo 通道做完整分层，仅 2400 出成品时用，其他宽度自动回退）
python render_flat.py --pdb structure.pdb --out-dir out --rep both --width 2400

# ② 矢量化（输出画布固定 2400 规范空间；--layer-mode full 与渲染端一致）
python vectorize_flat.py --renders-dir out ^
    --chains A,B,C --out-prefix out/flat --rep both

# ③ 导出分层 .ai（需本机 Illustrator；①用了 --layer-mode full 时这里
#    的 SVG 已含隐藏的完整链图层）
python protein2vector_flat.py --out-dir out ^
    --svgs out/flat_mono.svg,out/flat_palette.svg --layers Chain_A,Chain_B,Chain_C
```

## 完整分层（full layering）用法

`--layer-mode full`（网站表单"分层方式 → full"）时，每链图层内多一个
默认隐藏的 `Chain X full` 子组：该链单独渲染（其他链禁用、相机不变）
并描摹的**完整**链——被遮挡部分的填色、明暗、墨线、半透明表面壳俱全。
组装观感与 visible 模式逐字节一致；在 Illustrator 图层面板点亮
`Chain X full` 即可就地补全，拖出该链图层即得一条完整链。
设计细节与实测结论见 `docs/full_layering_spec.md`。

## 依赖小结

- 渲染：独立 PyMOL 环境（PyMOL 3.x；本管线会多进程并行调用，`--workers 1` 退回串行）
- 矢量化 / 网站：numpy、opencv-python、pillow、pymupdf、flask（见 `webapp/requirements.txt`）
- .ai 导出：Windows + 本机 Adobe Illustrator（COM）；导出结束后自动关闭 Illustrator
- 自包含：`vec_core.py` 内置全部描摹原语，仓库不依赖其他项目目录
