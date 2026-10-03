# flat_trace 管线参数说明表

三个阶段：**渲染**（render_flat.py，pymol-env）→ **矢量化**（vectorize_flat.py，Python 主环境）→ **.ai 导出**（protein2vector_flat.py + Illustrator COM）。网站工具的表单与本表均由 `params_spec.py` 生成。

## 一、渲染端（render_flat.py，pymol-env 运行）

| 参数 | 类型 | 默认值 | 生效模式 | 说明 |
|---|---|---|---|---|
| PDB 结构文件（网站上传） | file | （空） | 全部模式 | 上传 .pdb/.ent；网站据此自动识别链并列出取色器 |
| `rep`（表现模式） | 选项：cartoon/surface/both | cartoon | 全部模式 | cartoon=仅卡通；surface=仅表面（不透明正常上色）；both=每链一层表面+卡通整体，表面做半透明浅色外壳（fill-opacity 0.4），卡通在内部正常上色 |
| `chains`（链选择） | 文本 | （空） | 全部模式 | 逗号分隔，如 A,B,C；留空=自动取 PDB 中全部 ATOM 链。渲染与矢量化两端共用 |
| `colors`（各链颜色） | 颜色列表 | #F2F0B0 #C7C7F0 #F7C2C7 … | 全部模式 | 按链顺序取色（十六进制）；渲染端转成 0-1 RGB，矢量化端转成 #HEX。mono 开启时只用第一格 |
| `mono`（单色模式） | 开关 | false | 全部模式 | 所有链用同一颜色（取颜色选择器第一格） |
| `width`（渲染宽度） | 整数（800~4800） | 2400 | 全部模式 | 1x 渲染宽度（px）。掩膜/深度通道自动渲染 2x；mode-1 墨线通道固定 1x（线宽固定，放大相对变细会断线）。surface 通道很慢，预览可用 1200 |
| `height`（渲染高度） | 整数（600~4300） | 2132 | 全部模式 | 1x 渲染高度（px），一般保持 2400:2132 的默认比例 |
| `layer-mode`（分层方式） | 选项：visible/full | visible | 全部模式 | visible=仅可见（当前行为）：每链图层只含最靠前的可见部分；full=完整分层：每链额外 solo 渲染完整链（被遮挡部分也在），AI 里释放该链的剪贴蒙版即可看到完整链，组装观感不变。仅 2400 宽度可用，其他宽度自动回退 visible；渲染时间约 +60% |
| `view`（视角） | 选项：auto/orient | auto | 全部模式 | auto=按链质心自动摆正三聚体（>=3 链）；orient=PyMOL orient |
| `workers`（并行渲染进程数） | 整数（1~12） | 0 | 全部模式 | 渲染通道拆给几个 PyMOL 进程（0=自动，默认 4）。每个进程约占 1GB 内存；1=串行。CPU 核多内存大可加大，近似线性加速 |
| `view-angles`（视角微调） | 文本 | （空） | 全部模式 | 在上述视角基础上绕 x,y,z 各旋转的度数，如 0,15,0；留空不转 |

## 二、矢量化端（vectorize_flat.py）

| 参数 | 类型 | 默认值 | 生效模式 | 说明 |
|---|---|---|---|---|
| `depth-bands`（卡通明暗层数） | 整数（1~6） | 3 | cartoon、both | 按雾深图把卡通分成几档深浅（1=纯平涂）；分档阈值取分位数 |
| `shade-step`（每档加深比例） | 小数（0.02~0.3） | 0.1 | cartoon、both | 卡通相邻明暗档之间颜色乘 0.9^(档序) 的步长 |
| `surf-wash`（表面洗白比例） | 小数（0.0~0.6） | 0.25 | both | both 模式下表面颜色向白色混合的比例（配合 fill-opacity 0.4 做半透明浅色外壳）；surface 单独模式恒不透明，此参数不生效 |
| `ink-color`（墨线颜色） | 颜色 | #2D2A28 | 全部模式 | 卡通墨线 RGB；表面墨线自动取 0.75*v+30 的浅灰版本 |
| `ink-dilate`（卡通墨线粗细） | 选项：0/1 | 0 | 全部模式 | 0=细（约3px）；1=常规（约5px）。按 2400 输出画布计，矢量化时实测带宽自动归一化，任意渲染宽度观感一致。表面墨线恒为常规 |
| `mono-color`（mono 变体基色） | 颜色 | #7FA8D9 | 全部模式 | 同时输出的 _mono 单色版 SVG 的基色（逐链按 mono-step 加深） |
| `mono-step`（mono 逐链加深） | 小数（0.0~0.5） | 0.24 | 全部模式 | mono 变体中相邻链颜色乘 (1-step)^i |
| `bg`（背景色） | 颜色 | #FFFFFF | 全部模式 | SVG/.ai 画布背景色 |
| `audit`（填色审计图） | 开关 | false | 全部模式 | 额外输出 audit_*.png：红=超出轮廓的填色，黄=轮廓内漏填 |

## 三、导出端（protein2vector_flat.py）

| 参数 | 类型 | 默认值 | 生效模式 | 说明 |
|---|---|---|---|---|
| `export-ai`（导出分层 .ai） | 开关 | false | 全部模式 | 调用本机 Adobe Illustrator（COM）把两个 SVG 转成保留 Chain_A/B/C 图层的原生 .ai（PDF 兼容）。仅 Windows + 已装 Illustrator 可用；每链一层，层内含 cartoon/surface 子组 |

## 四、内置固定参数（不进表单，改脚本源码）

| 阶段 | 参数 | 当前值 |
|---|---|---|
| render | 平涂光照（卡通/表面通道） | ambient 1.0 / direct 0.0 / specular 0 / fog 0 / gamma 1 |
| render | 表面明暗通道 surfshade | ambient 0.45 / direct 0.55（亮暗分档依据） |
| render | 三光折痕通道 shade0/120/240 | ambient 0.35 / direct 0.65，模型绕视轴转 0/120/240 度各一张（预留内部棱线用，当前矢量化未使用） |
| render | 雾深图 depth | fog_start 0，雾贯穿整个深度 slab，近白远黑 |
| render | 多进程并行渲染 | 渲染通道拆给 4 个 PyMOL 进程（--workers 默认自动=4，可手动调；--workers 1 退回串行）。输出与串行逐字节一致；内存按约 1GB/进程估算。注意：本环境 PyMOL 无 OpenMP，单进程只能吃 1 核（逻辑线程），并行是多进程级的 |
| render | 抗锯齿 / surface_quality | antialias 2 / surface_quality 1（surface 慢的主因，可降 0 提速） |
| render | 通道分辨率 | 掩膜/深度/SSE 2x，mode-1 墨线与预览 1x（mode-1 线宽固定像素） |
| render | 预处理 | remove solvent + hetatm；只保留所选链 |
| vectorize | 描摹空间 | CANON_W=2400（所有掩膜/深度统一到此宽度再描）；输出画布随之固定 2400，--width 仅保留兼容 |
| vectorize | 填色形状下限 / 简化 / 圆角 | MIN_AREA=260 px²，EPS=1.3，CORNER_DEG=35°（4x 上采样描摹） |
| vectorize | 卡通填色外扩 | mask 外扩 4px 再裁到 silhouette+1px（消灭墨线内侧白缝） |
| vectorize | 墨线临摹 | 阈值>50 → 闭运算x2 → 膨胀x1（约3px带宽）→ 小于20px碎片丢弃 → min_area=40, eps=1.2, corner_deg=45°（保交叉锐角） |
| vectorize | 按链墨线分配 | 全场景按最近链划分领土（各链掩膜外扩生长 16 轮），每链只临摹自己领土内的墨线 |
| vectorize | 表面明暗分档阈值 | surfshade 亮度在 union 内的 66/33 百分位 |
| vectorize | 剪影 clipPath | silhouette（4x 描摹）；MuPDF 预览不支持，以 Illustrator 为准 |
| render | 完整分层 solo 通道（--layer-mode full） | 每链追加 solomask/solodepth/soloink（surface/both 再加 solosurfmask/solosurfshade/solosurfink），该链单独在场景中渲染（其他链 disable），相机不变、像素对齐；仅 2400 出成品时可用，其他宽度自动回退 visible |
| vectorize | 完整分层 SVG 结构 | full 模式每链组内含 <g clip-path=url(#vis链)> 包裹的 Chain_链_full（display=none 默认隐藏）+ 原有可见内容。clip=该链被遮挡区外扩1px；AI 里点亮 Chain 链 full 子组即就地补全被遮挡部分（组装态与 visible 模式逐字节一致） |
| export | 保存选项 | IllustratorSaveOptions pdfCompatible=true；超时 1200s；图层名 = Chain_<链号>（AI 导入时 _ 转空格，脚本已处理） |

## 五、输出文件一览（每次任务目录内）

| 文件 | 内容 |
|---|---|
| render/prev.png | 渲染端平涂彩色栅格预览（1x） |
| flat_palette.svg | 彩色版分层矢量（每链一层） |
| flat_mono.svg | 单色版分层矢量（同时输出） |
| audit_palette/mono.png | 填色审计叠加图（开启审计时） |
| flat_palette.ai / flat_mono.ai | Illustrator 分层工程（开启导出时） |
| _job.log | 任务全程日志 |
