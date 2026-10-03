# 完整分层（完整链图层）实现规格 — 已与用户确认的设计决策

日期：2026-10-03。本文件是实现依据，压缩上下文后照此实现。

## 目标

当前每链图层只含"最靠前可见"部分（拖开有缺口）。新功能：每链完整渲染
（被遮挡部分也在），在 Illustrator 里拖开任意一条链都是完整链，底下露出的
部分也完整。

## 用户确认的决策（5 条）

1. 组装态遮挡正确性优先：每链组带**剪贴蒙版**（内容=该链可见区掩膜），
   蒙版开着叠齐观感与现状一致；看完整链时在 AI 里"释放剪贴蒙版"。
2. 所有链都要完整版。
3. **both 模式也要**（含表面壳的完整化）。
4. 做成新参数 opt-in，默认保持现状"仅可见"。参数（网站+CLI）：
   `--layer-mode visible|full`，中文"分层方式：仅可见（当前）/完整（每链
   solo 渲染）"。
5. 完整分层只在 2400 出成品时用；600/1200 预览继续走老路（前端提示：
   layer-mode=full 时若 width<2400 自动回退 visible 并在日志说明）。

## 渲染端 render_flat.py

新参数 `--layer-mode`（render 端需要知道，因为要渲染 solo 通道）。
layer-mode=full 时，在现有通道之外为每链追加 solo 通道（场景只留该链）：

- `solomask{CH}.png`（2x）：该链单独的卡通掩膜（=它的完整外形）
- `solodepth{CH}.png`（2x）：该链单独的雾深图
- `soloink{CH}.png`（1x）：该链单独的 mode-1 墨线
- both 模式追加：`solosurfmask{CH}.png`（2x）、`solosurfshade{CH}.png`（2x）、
  `solosurfink{CH}.png`（1x）

实现要点：
- solo 会话里临时 disable 其他链的 cart/surf 对象即可（同一 worker 内按链
  切换：`enable_only([f"cart{ch}"])` 后照常 snap；色白/黑逻辑同现有 pass）。
- **相机/视角绝不能变**（solo 通道与合成的通道像素对齐是硬要求）。
- 墨线 solo 通道注意：模式 1 对单链也会画自遮挡棱线 → 完整链该有。
- 这些 solo 通道只在 layer-mode=full 时渲染；通道拆分（LPT）把 solo 通道
  当普通通道分配即可。

## 矢量化端 vectorize_flat.py

layer-mode=full 时，每链产出两套内容：

1. **可见部分**（现逻辑，输入 mask{CH}/depth/ink 全场景图）——不变；
2. **完整部分**（新）：`chain_fills(solomask, solodepth)` + solo 墨线
   （+both 的 solo 表面三件套），即用 solo 输入跑一遍现有 chain_fills/
   ink_paths 流程。

SVG/AI 结构（每链一层，层内自上而下）：

```
<g id="Chain_A">
  <g clip-path="url(#visA)">          ← 组装态剪贴（可见区掩膜）
    <g id="Chain_A_full">              ← 完整绘制：solo 填色+solo 墨线(+solo表面)
      ...paths...
    </g>
  </g>
  <g id="Chain_A_visible">             ← 现有内容（可见部分），保证叠齐像素一致
    ...paths...
  </g>
</g>
```

- `visA` 等 clipPath 定义放 defs（用现有可见区掩膜描摹，同 silhouette 做法）。
- AI 导入后：释放 Chain_A 的剪贴组 → Chain_A_full 完整显形；不释放则观感
  与现状一致（visible 在上层兜底）。
- 填色细节：solo 填色的 `allowed` 是**该链自身 silhouette+1px**（不是全场景
  union）；audit 只对可见部分做（现状逻辑）。
- 墨线宽度归一化对 solo 墨线同样生效（ink_raw_of 已与渲染宽度无关）。

## 网站端 webapp

- params_spec.py 增加参数 `layer_mode`（render 阶段，select: visible/full，
  默认 visible），desc 说明成本（both 2400 从 ~20 分钟到 ~35-40 分钟）。
- app.py 透传 `--layer-mode`。
- 前端提示：full + width<2400 时日志注明自动回退 visible（后端回退，
  app.py 在 full 且 width<2400 时改传 visible 并 log 一行说明）。

## 验收清单

- [ ] full 模式：叠齐观感与 visible 模式**逐像素一致**（可见部分输入相同）
- [ ] AI 中释放任一链剪贴 → 该链完整（被遮挡的螺旋/环带都在，填色+墨线齐全）
- [ ] visible 模式输出与改动前**逐字节一致**（回归）
- [ ] both 模式 full：表面壳的完整版也在剪贴组内
- [ ] 600/1200 + full 自动回退 visible 并有日志
- [ ] 排队/取消/历史等已有功能不回归

## 已知风险

- solo 墨线的自遮挡棱线可能比合成版多（被挡住的部分在完整链上本来就该有）。
- solo 填色与可见填色在边界处可能有 1px 级差异（两次独立描摹）；被 visible
  层兜底，释放蒙版后才可见。
- AI 对 SVG clipPath 的支持以实测为准（此前MuPDF 不支持 clip，AI 支持）。
