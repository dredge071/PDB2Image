"""flat_trace branch vectorizer — v7 architecture (restored) + fixes.

BASE (the version the user approved as the direction):
  ink   PyMOL mode-1 line art (1x render, black objects) -> skeleton ->
        graph walk -> direction-continuous merge -> SVG strokes with
        uniform width and miter joins. Follows the full mode-1 drawing:
        helix ribbon edges, loop outlines, arrow shapes.
  fills chain masks -> flat colour, split into depth tones by cumulative
        masks; grown 2px under the ink strokes (clipped to the ink
        neighbourhood so nothing pokes out the other side).

FIXES relative to v7:
  - open-polyline smoothing (smooth_open): the closed variant wrapped the
    two ends of a stroke together and manufactured straight chords across
    the canvas - the main "wrong connections".
  - no loose endpoint bridging (the other chord source).
  - stroke width 1.0.

Run:  python vectorize_flat.py --renders-dir out
      --chains A,B,C --out-prefix out/flat --compare out/compare.png
"""
import argparse
import os
from collections import defaultdict

import numpy as np
import cv2
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))

from vec_core import svg_document, trace_mask, trace_mask_g1  # noqa: E402

CANON_W = 2400          # masks/depth are traced in this space
MIN_AREA = 260          # at 2400
EPS = 1.3
CORNER_DEG = 35.0


def clean_band(b, band_min=None):
    """De-speckle a thresholded shading band: smooth ray noise turns a
    hard cut into hundreds of islands and pinholes (the 'crackle'
    artifact), so close, open, fill pinholes, drop debris islands."""
    h, w = b.shape
    band_min = band_min or max(MIN_AREA * 6, 1500)
    m = b.astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    ff = m.copy()
    pad = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(ff, pad, (0, 0), 1)
    m[ff == 0] = 1                       # fill interior pinholes
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    for i in range(1, n):
        if stats[i, 4] < band_min:
            m[lab == i] = 0              # drop debris islands
    return m.astype(bool)

DEFAULT_PALETTE = ["#F2F0B0", "#C7C7F0", "#F7C2C7", "#BFEABF", "#FACF9F",
                   "#C9B4F7", "#F2A19C", "#A7DBDB", "#FAD600", "#9CA8E0",
                   "#E0A2DD", "#BDEB67", "#C1E8F5", "#E05A75", "#A6F0D2",
                   "#E8E8C7", "#E3B4F7", "#FFA875", "#B2F2D6", "#A4B8E5"]


def hex_to_rgb(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def scale_hex(h, f):
    return tuple(max(0, min(255, int(c * f))) for c in hex_to_rgb(h))


def load_canon(png, w=CANON_W, resample=Image.LANCZOS):
    im = Image.open(png)
    if im.width != w:
        im = im.resize((w, round(im.height * w / im.width)), resample)
    return im


OFFS8 = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def thinning(img_bool):
    """Zhang-Suen thinning, vectorized. Returns a 1px-wide bool skeleton."""
    I = np.pad(img_bool.astype(np.uint8), 1)

    def sh(dy, dx):
        return np.roll(np.roll(I, dy, 0), dx, 1)

    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            P2, P3, P4 = sh(-1, 0), sh(-1, 1), sh(0, 1)
            P5, P6, P7 = sh(1, 1), sh(1, 0), sh(1, -1)
            P8, P9 = sh(0, -1), sh(-1, -1)
            B = P2 + P3 + P4 + P5 + P6 + P7 + P8 + P9
            seq = (P2, P3, P4, P5, P6, P7, P8, P9, P2)
            A = np.zeros_like(I)
            for i in range(8):
                A += ((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8)
            m = (I == 1) & (B >= 2) & (B <= 6) & (A == 1)
            if step == 0:
                c = m & (P2 * P4 * P6 == 0) & (P4 * P6 * P8 == 0)
            else:
                c = m & (P2 * P4 * P8 == 0) & (P2 * P6 * P8 == 0)
            if c.any():
                I[c] = 0
                changed = True
    return I[1:-1, 1:-1].astype(bool)


def smooth_open(pts, win=5, rounds=1):
    """Moving-average smoothing for OPEN polylines: pads by replicating the
    first/last point. (tracer_cv.smooth_polyline is the CLOSED variant - its
    wrap-around pulls the two ends of an open stroke toward each other and
    manufactures straight chords across the canvas.)"""
    for _ in range(rounds):
        k = win // 2
        ext = np.vstack([np.repeat(pts[:1], k, 0), pts,
                         np.repeat(pts[-1:], k, 0)])
        ker = np.ones(win) / win
        sm = np.empty_like(pts)
        for d in (0, 1):
            sm[:, d] = np.convolve(ext[:, d], ker, mode="same")[k:k + len(pts)]
        pts = sm
    return pts


def collapse_double_strands(S, max_rounds=4):
    """Zhang-Suen leaves 2px-wide sections on even-width strokes; every
    pixel there reads as a graph junction, which later shows as dashes
    (the section's micro-edges) or beads (disks painted on the nodes).
    Collapse each fully-set 2x2 block by removing the corner pixel whose
    removal keeps its neighbours 8-connected. Returns a真-1px skeleton."""
    S = S.copy()
    for _ in range(max_rounds):
        t = S.astype(np.uint8)
        blk = (t[1:, 1:] & t[:-1, 1:] & t[1:, :-1] & t[:-1, :-1])
        ys, xs = np.nonzero(blk)
        if not len(ys):
            break
        removed_any = False
        for y, x in zip((ys + 1).tolist(), (xs + 1).tolist()):
            # neighbours of (y, x) except itself
            nbrs = [(y + dy, x + dx) for dy, dx in OFFS8
                    if 0 <= y + dy < S.shape[0] and 0 <= x + dx < S.shape[1]
                    and S[y + dy, x + dx]]
            if len(nbrs) < 2:
                continue
            # does removing p keep nbrs in one 8-connected group?
            nset = set(nbrs)
            stack = [nbrs[0]]
            seen = {nbrs[0]}
            while stack:
                cy, cx = stack.pop()
                for dy, dx in OFFS8:
                    q = (cy + dy, cx + dx)
                    if q in nset and q not in seen:
                        seen.add(q)
                        stack.append(q)
            if len(seen) == len(nset):
                S[y, x] = False
                removed_any = True
        if not removed_any:
            break
    return S


def prune_spurs(S, rounds=2):
    """Drop endpoint pixels a few times: kills tiny spurs that would
    otherwise become false graph nodes."""
    S = S.copy()
    H, W = S.shape
    for _ in range(rounds):
        nb = np.zeros((H, W), np.uint8)
        for dy, dx in OFFS8:
            nb += np.roll(np.roll(S, dy, 0), dx, 1).astype(np.uint8)
        endp = S & (nb <= 1)
        if not endp.any():
            break
        S[endp] = False
    return S


def bridge_endpoints(S, max_dist=18.0, min_dot=-0.4, max_perp=3.5):
    """Strictly connect skeleton endpoints that continue each other:
    (1) close, (2) outward directions opposing, (3) COLLINEAR - each
    endpoint's line direction must point at the other endpoint with less
    than max_perp px of perpendicular offset. The collinearity test is what
    rejects the endpoints of two PARALLEL lines facing each other across a
    gap (their connecting segment is skewed off-axis), which is what
    produced the long wrong connections of earlier versions."""
    S = S.copy().astype(np.uint8)
    H, W = S.shape

    def nbcount():
        nb = np.zeros((H, W), np.uint8)
        for dy, dx in OFFS8:
            nb += np.roll(np.roll(S, dy, 0), dx, 1).astype(np.uint8)
        return nb

    nb = nbcount()
    ys, xs = np.nonzero(S & (nb == 1))
    if len(ys) < 2:
        return S.astype(bool)
    pts = np.stack([xs, ys], 1).astype(float)
    d = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=2)
    iu = np.triu_indices(len(pts), 1)

    def outward(p):
        for dy, dx in OFFS8:
            q = (p[0] + dy, p[1] + dx)
            if 0 <= q[0] < H and 0 <= q[1] < W and S[q]:
                v = np.array([p[1] - q[1], p[0] - q[0]], float)
                return v / (np.linalg.norm(v) + 1e-9)
        return None

    dirs = [outward((int(y), int(x))) for y, x in zip(ys, xs)]
    for k in np.nonzero((d[iu] > 1) & (d[iu] < max_dist))[0]:
        a, b = iu[0][k], iu[1][k]
        if dirs[a] is None or dirs[b] is None:
            continue
        if float(np.dot(dirs[a], dirs[b])) >= min_dot:
            continue
        seg = pts[b] - pts[a]
        seg_len = np.linalg.norm(seg) + 1e-9
        u = seg / seg_len
        # both lines must aim at each other (small perpendicular offset)
        perp_a = abs(float(np.cross(dirs[a], u))) * seg_len
        perp_b = abs(float(np.cross(dirs[b], u))) * seg_len
        aim_a = float(np.dot(dirs[a], u))
        aim_b = float(np.dot(dirs[b], -u))
        if (perp_a < max_perp and perp_b < max_perp
                and aim_a > 0.6 and aim_b > 0.6):
            cv2.line(S, (int(xs[a]), int(ys[a])), (int(xs[b]), int(ys[b])),
                     1, 1)
    return S.astype(bool)


def ribbon_bridge(m, max_dist=18.0, dot_thr=0.7, width=4):
    """Reconnect stroke breaks at the PAINTED-RIBBON level without
    welding parallel strokes.

    A plain morphological close heals real breaks but also merges two
    lines that merely pass near each other (loop turns): close k5 took
    wide-ink area from 3.5k to 29k px on the trimer test. Here the
    ribbon is skeletonized; stroke TIPS (skeleton endpoints) closer than
    max_dist are connected only when both local tangents run ALONG the
    connecting line - true break tips face each other axially, while
    neighbouring parallel strokes' tips sit perpendicular to their
    separation and are refused."""
    Sk = thinning(m > 0)
    Hh, Ww = Sk.shape
    nb = np.zeros((Hh, Ww), np.uint8)
    for dy, dx in OFFS8:
        nb += np.roll(np.roll(Sk, dy, 0), dx, 1).astype(np.uint8)
    ys, xs = np.nonzero(Sk & (nb == 1))
    if len(ys) < 2:
        return m
    pts = np.stack([xs, ys], 1).astype(float)
    d = np.linalg.norm(pts[:, None] - pts[None, :], axis=2)
    iu = np.triu_indices(len(pts), 1)
    out = m.copy()
    used = set()

    def tangent(pi):
        # walk ~6 px into the stroke from its tip; that is the local
        # direction the pen was moving
        y, x = int(pts[pi][1]), int(pts[pi][0])
        cur, prev = (y, x), None
        for _ in range(6):
            nxt = []
            for dy2, dx2 in OFFS8:
                q = (cur[0] + dy2, cur[1] + dx2)
                if 0 <= q[0] < Hh and 0 <= q[1] < Ww and Sk[q] and q != prev:
                    nxt.append(q)
            if not nxt:
                break
            prev, cur = cur, nxt[0]
        v = np.array([cur[1] - x, cur[0] - y], float)
        nn = np.linalg.norm(v)
        return v / nn if nn > 0 else np.zeros(2)

    cand = [(d[iu[0][k], iu[1][k]], iu[0][k], iu[1][k])
            for k in range(len(iu[0]))
            if 3 < d[iu[0][k], iu[1][k]] < max_dist]
    cand.sort()
    for dist, a, b in cand:
        if a in used or b in used:
            continue
        ta, tb = tangent(a), tangent(b)
        seg = pts[b] - pts[a]
        u = seg / (np.linalg.norm(seg) + 1e-9)
        if (abs(float(np.dot(ta, u))) < dot_thr
                or abs(float(np.dot(tb, -u))) < dot_thr):
            continue
        # extend 4px INTO each stroke so the joint is a solid overlap,
        # not a point contact that bezier retracing (eps 1.2) shaves
        # apart again
        pa = pts[a] - ta * 4.0
        pb = pts[b] - tb * 4.0
        cv2.line(out, (int(pa[0]), int(pa[1])),
                 (int(pb[0]), int(pb[1])), 1, width)
        used.update((a, b))
    return out


def skeleton_polylines(S, scale, min_len, smooth_win=9, smooth_rounds=2):
    """Skeleton pixel graph -> merged, SMOOTHED open polylines.

    Edges between junction/endpoint nodes are merged THROUGH nodes by
    direction continuity (a pen crossing an intersection without lifting),
    then smoothed and returned as (n, 2) float point arrays. Callers can
    stroke them directly or thicken them into ribbon masks."""
    return _skeleton_polyline_impl(S, scale, min_len, smooth_win,
                                   smooth_rounds, emit_d=False)


def skeleton_strokes(S, scale, eps, min_len):
    """Skeleton pixel graph -> merged vector strokes -> one 'd' per stroke.

    Edges between junction/endpoint nodes are merged THROUGH nodes by
    direction continuity (a pen crossing an intersection without lifting),
    then smoothed and emitted as open-path beziers."""
    return _skeleton_polyline_impl(S, scale, min_len, 9, 2, emit_d=True,
                                   eps=eps)


def _skeleton_polyline_impl(S, scale, min_len, smooth_win, smooth_rounds,
                            emit_d, eps=1.5):
    H, W = S.shape
    nb = np.zeros((H, W), np.uint8)
    for dy, dx in OFFS8:
        nb += np.roll(np.roll(S, dy, 0), dx, 1).astype(np.uint8)
    node = S & (nb != 2)

    visited = np.zeros((H, W), bool)

    def neighbors(p):
        y, x = p
        for dy, dx in OFFS8:
            q = (y + dy, x + dx)
            if 0 <= q[0] < H and 0 <= q[1] < W and S[q]:
                yield q

    edges = []
    for p in zip(*np.nonzero(S)):
        if not node[p]:
            continue
        for q in neighbors(p):
            if node[q]:
                if p < q:
                    edges.append([p, q])
                continue
            if visited[q]:
                continue
            path, prev, cur = [p, q], p, q
            visited[q] = True
            while not node[cur]:
                nxt = [r for r in neighbors(cur)
                       if r != prev and not visited[r]]
                if not nxt:
                    break
                r = nxt[0]
                visited[r] = True
                path.append(r)
                prev, cur = cur, r
            edges.append(path)

    rem = S & ~visited & ~node
    for p in zip(*np.nonzero(rem)):
        if visited[p]:
            continue
        path, prev, cur = [p], None, p
        visited[p] = True
        while True:
            nxt = [r for r in neighbors(cur) if r != prev and not visited[r]]
            if not nxt:
                break
            r = nxt[0]
            visited[r] = True
            path.append(r)
            prev, cur = cur, r
        if len(path) > 2:
            path.append(p)
        edges.append(path)

    # ---- merge edges through nodes by direction continuity ----
    adj = defaultdict(list)
    for i, pl in enumerate(edges):
        if pl[0] != pl[-1]:          # skip closed loops
            adj[pl[0]].append((i, 0))
            adj[pl[-1]].append((i, -1))
    used = [False] * len(edges)

    def far_dir(pl, end, k=4):
        """unit direction pointing INTO the edge from the given end"""
        seg = pl[1:k + 1] if end == 0 else pl[-2:-k - 2:-1]
        v = np.array(seg[-1], float) - np.array(seg[0], float)
        n = np.linalg.norm(v)
        return v / n if n > 0 else np.zeros(2)

    merged = []
    for i, pl in enumerate(edges):
        if used[i]:
            continue
        used[i] = True
        if pl[0] == pl[-1]:          # closed loop
            merged.append(pl)
            continue
        chain = list(pl)
        for tail in (False, True):
            while True:
                p = chain[-1] if not tail else chain[0]
                cands = [(j, e) for (j, e) in adj[p] if not used[j]]
                if not cands:
                    break
                h = far_dir(chain, 1 if not tail else 0, 6) * -1
                best, best_score = None, -2.0
                for j, e in cands:
                    c = far_dir(edges[j], e, 4)
                    score = float(np.dot(h, c))
                    if score > best_score:
                        best, best_score = (j, e), score
                j, e = best
                used[j] = True
                if not tail:                    # append: continue forward
                    pl2 = edges[j][::-1] if e == -1 else edges[j]
                    chain += pl2[1:]
                else:                           # prepend: extend backward
                    pl2 = edges[j][::-1] if e == 0 else edges[j]
                    chain[:0] = pl2[:-1]
        merged.append(chain)

    # ---- polylines -> smoothed points (or bezier 'd') ----
    out = []
    for path in merged:
        if len(path) < 2:
            continue
        a = np.array([(x, y) for y, x in path], float) * scale
        length = float(np.linalg.norm(np.diff(a, axis=0), axis=1).sum())
        if length < min_len:
            continue
        if len(a) > 4:
            a = smooth_open(a, smooth_win, smooth_rounds)
        if not emit_d:
            out.append(a)
            continue
        # closed=False: these are OPEN strokes - with closed=True the
        # decimator treats start/end as neighbours and manufactures
        # straight chords across the canvas
        dec = cv2.approxPolyDP(a.reshape(-1, 1, 2).astype(np.float32),
                               eps, False)[:, 0, :]
        if len(dec) < 2:
            dec = a
        n = len(dec)
        parts = [f"M{dec[0][0]:.2f} {dec[0][1]:.2f}"]
        for i in range(1, n - 1):
            mid = (dec[i] + dec[i + 1]) / 2
            parts.append(f"C{dec[i][0]:.2f} {dec[i][1]:.2f} "
                         f"{dec[i][0]:.2f} {dec[i][1]:.2f} "
                         f"{mid[0]:.2f} {mid[1]:.2f}")
        parts.append(f"L{dec[-1][0]:.2f} {dec[-1][1]:.2f}")
        out.append("".join(parts))
    return out


def prep4(mask, allowed=None, up=4):
    """Resize a raster mask to 4x and optionally clip it (nearest for the
    clip so its edge stays exact). Shared by fills and the undercoat."""
    h, w = mask.shape
    big = cv2.resize(mask.astype(np.float32), (w * up, h * up),
                     interpolation=cv2.INTER_LINEAR) > 0.5
    if allowed is not None:
        big &= cv2.resize(allowed.astype(np.float32), (w * up, h * up),
                          interpolation=cv2.INTER_NEAREST) > 0.5
    return big.astype(np.uint8) * 255


def chain_fills(mask, depth, thresholds, color_hex, shade_step, allowed=None):
    """Base fill + cumulative deeper masks -> [(d, rgb)] back-to-front safe.

    Deeper cumulative masks are painted over the base, so every boundary
    always sits on an existing fill (no background-colored seams).
    Tracing happens at 4x (subpixel edges), and the raster is clipped to
    `allowed` AT 4x before tracing, so the traced boundary follows the
    allowed region itself - overshoot from bezier smoothing is gone.

    Depth bands are de-speckled by clean_band() before tracing."""
    up = 4
    h, w = mask.shape

    def prep(m):
        big = cv2.resize(m.astype(np.float32), (w * up, h * up),
                         interpolation=cv2.INTER_LINEAR) > 0.5
        if allowed is not None:
            big &= cv2.resize(allowed.astype(np.float32), (w * up, h * up),
                              interpolation=cv2.INTER_NEAREST) > 0.5
        return big.astype(np.uint8) * 255

    shapes = [(d, hex_to_rgb(color_hex))
              for d in trace_mask(prep(mask), MIN_AREA * up * up,
                                  1.0 / up, EPS * up, CORNER_DEG)]
    for j, t in enumerate(thresholds):
        deep = mask & (depth <= t)
        if deep.sum() < 40:
            continue
        deep = clean_band(deep)
        if not deep.any():
            continue
        col = scale_hex(color_hex, 1.0 - (j + 1) * shade_step)
        shapes += [(d, col) for d in trace_mask(prep(deep),
                                                MIN_AREA * up * up, 1.0 / up,
                                                EPS * up, CORNER_DEG)]
    return shapes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--renders-dir", required=True)
    ap.add_argument("--chains", required=True)
    ap.add_argument("--colors", default="")     # A:#hex;B:#hex
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--depth-bands", type=int, default=3)
    ap.add_argument("--shade-step", type=float, default=0.10)
    ap.add_argument("--mono-color", default="#7FA8D9")
    ap.add_argument("--mono-step", type=float, default=0.24)
    ap.add_argument("--ink-color", default="45,42,40")
    ap.add_argument("--ink-width", type=float, default=1.0,
                    help="stroke width in output units (2400-space)")
    ap.add_argument("--rep", choices=["cartoon", "surface", "both"],
                    default="cartoon")
    ap.add_argument("--layer-mode", choices=["visible", "full"],
                    default="visible",
                    help="full = each chain additionally gets its COMPLETE "
                         "content traced from solo renders (occluded parts "
                         "included), wrapped in a clip path of the chain's "
                         "visible region; release the clip in Illustrator "
                         "to expose it. Needs the solo render channels")
    ap.add_argument("--surf-wash", type=float, default=0.25,
                    help="both mode: whiten the surface tone (pale shell)")
    ap.add_argument("--ink-dilate", type=int, default=0, choices=[0, 1],
                    help="cartoon ink band: 0=thin ~1.5px, 1=regular ~3px")
    ap.add_argument("--ink-source", choices=["mode1", "depth"],
                    default="mode1",
                    help="cartoon ink source: mode1 = trace PyMOL's "
                         "ray_trace_mode-1 raster; depth = detect "
                         "depth-jump edges on the native 2x fog-depth "
                         "map (ChimeraX-style), cleaner for cartoon. "
                         "Falls back to mode1 when depth.png is missing")
    ap.add_argument("--ink-depth-t", type=float, default=100.0,
                    help="depth-ink: STRONG-edge gradient threshold in "
                         "0-255 depth units (calibrated: gradient dist is "
                         "bimodal, p90 approx 9 = noise vs p99 approx 500 "
                         "= real jumps). Weak extension threshold is 30 "
                         "percent of this")
    ap.add_argument("--width", type=int, default=2400)
    ap.add_argument("--bg", default="#FFFFFF")
    ap.add_argument("--compare", default="")
    ap.add_argument("--audit", default="",
                    help="write audit overlay PNGs (red=overshoot, "
                         "yellow=shortfall) with this prefix")
    args = ap.parse_args()

    chains = [c.strip() for c in args.chains.split(",") if c.strip()]
    colors = {}
    if args.colors:
        for part in args.colors.split(";"):
            if part:
                ch, hx = part.split(":")
                colors[ch] = hx
    for i, ch in enumerate(chains):
        colors.setdefault(ch, DEFAULT_PALETTE[i % len(DEFAULT_PALETTE)])

    rd = args.renders_dir
    out_w = args.width

    # ===================== representation assembly =====================
    # cartoon mode : cartoon fills + ink per chain                (opaque)
    # surface mode : surface fills (light/shade bands) + surface ink
    # both mode    : surface opaque on top, cartoon whitened below,
    #                each chain's surface+cartoon in ONE layer group
    rep = args.rep
    full = args.layer_mode == "full"
    have_surf = rep in ("surface", "both")
    have_cart = rep in ("cartoon", "both")

    # ---- canonical-space cartoon masks + shared depth ----
    masks, depth_img = {}, None
    for ch in chains:
        # binary mask: bilinear + midpoint threshold (LANCZOS rings would
        # shift region edges when a low-res render is upscaled)
        im = load_canon(os.path.join(rd, f"mask{ch}.png"),
                        resample=Image.BILINEAR)
        a = np.array(im.convert("RGB"))
        masks[ch] = (a.min(axis=2) > 128)
        depth_img = im
    # the output canvas is ALWAYS the canonical trace space (2400): fill/
    # ink paths and the audit all assume CANON_W coordinates. --width is
    # accepted for backwards compat but no longer changes the geometry.
    out_w = CANON_W
    size = (out_w, round(out_w * depth_img.height / depth_img.width))

    dim = np.array(load_canon(os.path.join(rd, "depth.png")).convert("L"),
                   dtype=float)
    union = np.zeros_like(dim, dtype=bool)
    for ch in chains:
        union |= masks[ch]

    lo, hi = np.percentile(dim[union], (2, 98))
    dim = np.clip((dim - lo) / max(1e-6, hi - lo) * 255.0, 0, 255)
    k = max(0, args.depth_bands - 1)
    thresholds = (np.quantile(dim[union], np.linspace(0, 1, k + 2)[1:-1])
                  if k else np.array([]))

    # ---- surface masks + shading luminance ----
    smasks, slum = {}, None
    if have_surf:
        sprev = load_canon(os.path.join(rd, "surfmask" + chains[0] + ".png"))
        del sprev
        for ch in chains:
            im = load_canon(os.path.join(rd, f"surfmask{ch}.png"),
                            resample=Image.BILINEAR)
            smasks[ch] = (np.array(im.convert("RGB")).min(axis=2) > 128)
        sh = np.array(load_canon(os.path.join(rd, "surfshade.png"))
                      .convert("L"), dtype=float)
        sunion = np.zeros_like(sh, dtype=bool)
        for ch in chains:
            sunion |= smasks[ch]
        slo, shi = np.percentile(sh[sunion], (2, 98))
        slum = np.clip((sh - slo) / max(1e-6, shi - slo) * 255.0, 0, 255)
        union = sunion if rep == "surface" else (union | sunion)

    allowed = cv2.dilate(union.astype(np.uint8), np.ones((3, 3), np.uint8),
                         iterations=1).astype(bool)

    # ---- nearest-chain partition of the scene: every pixel is grown out
    #      to its closest chain so per-chain ink tracing keeps only that
    #      chain's own lines (the ink render is full-scene) ----
    chain_lbl = np.full(dim.shape, -1, np.int32)
    for i, ch in enumerate(chains):
        seed = masks[ch].copy()
        if have_surf:
            seed |= smasks[ch]
        chain_lbl[seed] = i
    grow_k = np.ones((3, 3), np.uint8)
    for _ in range(16):
        if not (chain_lbl < 0).any():
            break
        for i in range(len(chains)):
            d = cv2.dilate((chain_lbl == i).astype(np.uint8), grow_k,
                           iterations=1).astype(bool)
            chain_lbl[d & (chain_lbl < 0)] = i

    up = 4

    def prep4m(m, alw=None):
        # alw=None -> the scene-wide allowed region; solo content passes
        # the chain's own silhouette+1px instead
        alw = allowed if alw is None else alw
        big = cv2.resize(m.astype(np.float32),
                         (m.shape[1] * up, m.shape[0] * up),
                         interpolation=cv2.INTER_LINEAR) > 0.5
        big &= cv2.resize(alw.astype(np.float32),
                          (m.shape[1] * up, m.shape[0] * up),
                          interpolation=cv2.INTER_NEAREST) > 0.5
        return big.astype(np.uint8) * 255

    def band_paths(mask, alw=None, min_area=MIN_AREA):
        return trace_mask(prep4m(mask, alw), min_area * up * up, 1.0 / up,
                          EPS * up, CORNER_DEG)

    def wash(c, f):
        return tuple(int(v + (255 - v) * f) for v in c)

    # ---- trace the mode-1 ink of whichever reps are active, per chain:
    #      the ink PNG is a full-scene render, so each trace is restricted
    #      to the chain's own territory instead of handing the whole
    #      trimer's ink to every chain layer ----
    def ink_raw_of(png):
        """Canonicalize an ink render to CANON_W and binarize it. No width
        normalization here: ink_paths_keep rebuilds every stroke as a
        fixed-width ribbon around its skeleton centerline, so the drawn
        line weight is identical at every render resolution."""
        im = Image.open(png).convert("RGB")
        if im.width != CANON_W:
            im = im.resize((CANON_W, round(im.height * CANON_W / im.width)),
                           Image.LANCZOS)
        a = np.array(im)
        raw = (a.min(axis=2) > 50).astype(np.uint8) * 255
        raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE,
                               np.ones((3, 3), np.uint8), iterations=2)
        return raw

    def ink_centerlines(raw):
        """Full-scene ink centerline polylines: skeleton of the CONTINUOUS
        ink mask, merged through junctions, smoothed.

        The skeleton is deliberately computed on the FULL-SCENE mask, not
        on per-chain-restricted masks: the chain-territory partition cuts
        the ink into fragments, and a skeleton of fragments wanders
        through the gaps between them - visibly OFF the ink line. One
        clean skeleton, split into chains afterwards, is the fix."""
        S = thinning(cv2.dilate((raw > 0).astype(np.uint8) * 255,
                                np.ones((3, 3), np.uint8), iterations=2) > 0)
        S = collapse_double_strands(S)
        S = prune_spurs(S, rounds=2)
        # smooth the centerline BEFORE thickening: the raw 1px skeleton
        # zigzags at pixel scale and the ribbon inherits every step.
        # skeleton_polylines merges through junctions and applies the
        # same smoothing the .ai heritage path used (window 9, 2 rounds)
        return skeleton_polylines(S, scale=1.0, min_len=2.0)

    def ink_paths_keep(pls, width_px, label=None):
        """Deprecated wrapper kept for reference; the live path is
        ribbon_network() + chain_ink_paths()."""
        raise NotImplementedError

    def depth_edge_pls(dep_png, allow):
        """Cartoon ink from DEPTH-JUMP edges (ChimeraX principle): Sobel
        gradient of the raw fog-depth render, thresholded, clipped to the
        cartoon silhouette, then the SAME skeleton->smooth->ribbon backend
        the mode-1 path uses.

        Detected at the depth map's NATIVE 2x resolution (the threshold
        was calibrated there; canonical upscaling would dilute gradients
        by a fixed W2->CANON_W factor) and lifted into canvas coordinates
        by skeleton_polylines' scale. The RAW depth is used, not the
        lo/hi-normalized dim: percentile clipping flattens the depth
        tails and would forge or lose edges exactly there."""
        dep = Image.open(dep_png).convert("L")
        a8 = np.asarray(dep, dtype=np.uint8)
        a = a8.astype(np.float32) / 255.0
        gx = cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)
        # gradient magnitude in 0-255 depth units. The distribution is
        # strongly bimodal (p90 ~ 9, p99 ~ 500 on the test complex):
        # real depth jumps vs quantization noise, cleanly separable.
        # HYSTERESIS instead of a single cut: a silhouette/occlusion
        # edge crosses smooth ramps where its gradient sags, and a
        # single threshold breaks the line exactly there. Strong pixels
        # seed; weak-but-connected pixels extend the line through the
        # sag; weak components with no strong pixel (noise contours)
        # drop out. (Canny's own NMS output fragments the same way -
        # the ragged ridge defeats NMS too - so do it on the raw mask.)
        g = np.hypot(gx, gy) * 255.0
        strong = (g > args.ink_depth_t) & allow
        weak = (g > args.ink_depth_t * 0.3) & allow
        nl, lab = cv2.connectedComponents(weak.astype(np.uint8), 8)
        keep = np.unique(lab[strong])
        raw = np.isin(lab, keep[keep != 0]).astype(np.uint8) * 255
        k3 = np.ones((3, 3), np.uint8)
        raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, k3, iterations=1)
        S = thinning(raw > 0)
        S = collapse_double_strands(S)
        S = prune_spurs(S, rounds=3)
        # reconnect small residual breaks: collinear endpoints facing
        # each other (the heritage bridge; safe here because this mask
        # is one continuous full-scene network, not territory-cut ink)
        S = bridge_endpoints(S, max_dist=25.0)
        return skeleton_polylines(S, scale=CANON_W / dep.width, min_len=2.0)

    def native_allow(png_fmt, extra_png=None):
        """Cartoon-pixels-only allow mask at the DEPTH map's native
        resolution: the depth band straddles the silhouette (its AA ramp
        and the mask's >128 threshold disagree by ~1px), so unrestricted
        edges would draw lines OUTSIDE the object. Dilate 2px keeps the
        line ON the silhouette without clipping interior creases."""
        dep = Image.open(os.path.join(rd, "depth.png"))
        Wn, Hn = dep.size
        del dep
        un = np.zeros((Hn, Wn), dtype=bool)
        for ch in chains:
            un |= np.array(Image.open(os.path.join(
                rd, png_fmt.format(ch=ch))).convert("L")
                .resize((Wn, Hn), Image.BILINEAR)) > 128
        if extra_png:
            un |= np.array(Image.open(os.path.join(rd, extra_png)
                                      ).convert("L").resize((Wn, Hn),
                                                            Image.BILINEAR)
                           ) > 128
        return cv2.dilate(un.astype(np.uint8), np.ones((3, 3), np.uint8),
                          iterations=2).astype(bool)

    # ink weights (2400-canvas px, identical at every render width):
    # cartoon 4px (5px with --ink-dilate 1), surface 5px; the extra px
    # over the old 3px buys eps=1.2 smoothing without wall-crossing
    cart_w = 4.0 if args.ink_dilate == 0 else 5.0
    surf_w = 5.0

    def ribbon_network(pls, width_px):
        """Whole-scene ink as ONE bridged ribbon mask: strokes are drawn
        full-network, then breaks are reconnected by ribbon_bridge
        (endpoint pairs whose tangents run along the connecting line -
        refuses to weld parallel strokes that pass near each other, the
        loop-turn 'sticking' a plain close caused). Per-chain splitting
        happens afterwards at the mask level, so a break that straddles
        a chain boundary is healed BEFORE the split cuts it."""
        H, W = chain_lbl.shape
        up = 2
        m = np.zeros((H * up, W * up), dtype=np.uint8)
        th = int(width_px * up)
        for a in pls:
            cv2.polylines(m, [np.round(a * up).astype(np.int32)],
                          False, 255, thickness=th)
        if not (m > 0).any():
            return None
        m = cv2.resize(m, (W, H), interpolation=cv2.INTER_AREA)
        m = (m > 127).astype(np.uint8)
        return (ribbon_bridge(m, width=int(round(width_px))) > 0
                ).astype(np.uint8)

    def chain_ink_paths(net, ci_, width_px):
        """One chain's share of a bridged ribbon network: pixel-level
        territory mask, debris drop, G1 trace. Territory dilated 4 so
        neighbouring chains' drawings overlap 6-8px at the boundary -
        the eps=1.2 contour shrinkage of the G1 trace then cannot open
        a seam between the two halves of a stroke crossing the boundary
        (dilate 2 abutted exactly and rendered with 1-3px hairline
        gaps). The debris cutoff is deliberately low (40px): the cut
        splits real strokes into short halves, and a 150px cutoff
        deleted those halves; only 1-5px slivers are debris at ribbon
        width 4-5."""
        if net is None:
            return []
        H, W = chain_lbl.shape
        terr = cv2.dilate((chain_lbl == ci_).astype(np.uint8),
                          np.ones((3, 3), np.uint8), iterations=4) > 0
        m = net & terr
        nf, lf, stf, _ = cv2.connectedComponentsWithStats(m, 8)
        for i in range(1, nf):
            if stf[i, cv2.CC_STAT_AREA] < 40:
                m[lf == i] = 0
        if not (m > 0).any():
            return []
        return trace_mask_g1(m * 255, min_area=25, scale=1.0, eps=1.2,
                             corner_deg=80.0)

    use_depth_ink = (args.ink_source == "depth"
                     and os.path.exists(os.path.join(rd, "depth.png")))
    if use_depth_ink:
        print(f"[vec] cartoon ink source: depth-jump "
              f"(hysteresis {args.ink_depth_t:.0f}/"
              f"{args.ink_depth_t * 0.3:.0f})", flush=True)
        allow_cart = native_allow("mask{ch}.png")
        cart_pls = depth_edge_pls(os.path.join(rd, "depth.png"),
                                  allow_cart)
    else:
        cart_pls = (ink_centerlines(
                        ink_raw_of(os.path.join(rd, "ink.png")))
                    if have_cart else [])
    surf_pls = (ink_centerlines(ink_raw_of(os.path.join(rd, "surfink.png")))
                if have_surf else [])
    cart_net = ribbon_network(cart_pls, cart_w) if have_cart else None
    surf_net = ribbon_network(surf_pls, surf_w) if have_surf else None
    cart_ink, surf_ink = {}, {}
    for ci, ch in enumerate(chains):
        cart_ink[ch] = chain_ink_paths(cart_net, ci, cart_w) if have_cart else []
        surf_ink[ch] = chain_ink_paths(surf_net, ci, surf_w) if have_surf else []
    for ch in chains:
        print(f"[vec] ink {ch}: cart={len(cart_ink[ch])} "
              f"surf={len(surf_ink[ch])} paths", flush=True)

    ink_rgb = tuple(int(v) for v in args.ink_color.split(","))
    surf_ink_rgb = tuple(int(v * 0.75 + 30) for v in ink_rgb)

    # surface tone thresholds from the shading luminance
    sthr_hi = sthr_lo = None
    if slum is not None:
        sthr_hi = np.percentile(slum[union], 66)
        sthr_lo = np.percentile(slum[union], 33)

    # ---- full layering: per-chain solo data (the complete chains) ----
    solo_masks, solo_smasks = {}, {}
    solo_dim, solo_slum = {}, {}
    solo_allow, solo_surf_allow = {}, {}
    solo_cart_ink, solo_surf_ink = {}, {}
    if full:
        print("[vec] layer-mode full: tracing complete chains from solo "
              "renders", flush=True)
        k3 = np.ones((3, 3), np.uint8)
        for ch in chains:
            im = load_canon(os.path.join(rd, f"solomask{ch}.png"),
                            resample=Image.BILINEAR)
            solo_masks[ch] = (np.array(im.convert("RGB")).min(axis=2) > 128)
            if have_surf:
                im = load_canon(os.path.join(rd, f"solosurfmask{ch}.png"),
                                resample=Image.BILINEAR)
                solo_smasks[ch] = (np.array(im.convert("RGB"))
                                   .min(axis=2) > 128)
            # solo depth / surface luminance are normalized with the
            # SCENE calibration (same lo/hi), so depth tones continue
            # seamlessly across the visible/hidden border of the chain
            sraw = np.array(load_canon(os.path.join(rd,
                            f"solodepth{ch}.png")).convert("L"), dtype=float)
            solo_dim[ch] = np.clip((sraw - lo) / max(1e-6, hi - lo) * 255.0,
                                   0, 255)
            if have_surf:
                sraw = np.array(load_canon(os.path.join(rd,
                                f"solosurfshade{ch}.png")).convert("L"),
                                dtype=float)
                solo_slum[ch] = np.clip(
                    (sraw - slo) / max(1e-6, shi - slo) * 255.0, 0, 255)
            solo_allow[ch] = cv2.dilate(
                solo_masks[ch].astype(np.uint8), k3,
                iterations=1).astype(bool)
            if have_surf:
                solo_surf_allow[ch] = cv2.dilate(
                    solo_smasks[ch].astype(np.uint8), k3,
                    iterations=1).astype(bool)
        for ch in chains:
            # solo renders contain only this chain's ink, so there is no
            # territory to split by - centerlines go straight to ribbons
            if have_cart:
                if use_depth_ink and os.path.exists(
                        os.path.join(rd, f"solodepth{ch}.png")):
                    # solo: the chain's own silhouette at native res is
                    # the allow mask; single chain, no territory split
                    dep = Image.open(os.path.join(rd, f"solodepth{ch}.png"))
                    Wn, Hn = dep.size
                    del dep
                    sn = np.array(Image.open(
                        os.path.join(rd, f"solomask{ch}.png")
                        ).convert("L").resize((Wn, Hn), Image.BILINEAR)) > 128
                    sn = cv2.dilate(sn.astype(np.uint8),
                                    np.ones((3, 3), np.uint8),
                                    iterations=2).astype(bool)
                    solo_net = ribbon_network(
                        depth_edge_pls(
                            os.path.join(rd, f"solodepth{ch}.png"), sn),
                        cart_w)
                    m = solo_net if solo_net is not None else np.zeros(
                        chain_lbl.shape, np.uint8)
                    nf, lf, stf, _ = cv2.connectedComponentsWithStats(m, 8)
                    for i2 in range(1, nf):
                        if stf[i2, cv2.CC_STAT_AREA] < 40:
                            m[lf == i2] = 0
                    solo_cart_ink[ch] = (trace_mask_g1(
                        m * 255, min_area=25, scale=1.0, eps=1.2,
                        corner_deg=80.0) if (m > 0).any() else [])
                else:
                    solo_net = ribbon_network(
                        ink_centerlines(
                            ink_raw_of(os.path.join(rd,
                                                    f"soloink{ch}.png"))),
                        cart_w)
                    m = solo_net if solo_net is not None else np.zeros(
                        chain_lbl.shape, np.uint8)
                    nf, lf, stf, _ = cv2.connectedComponentsWithStats(m, 8)
                    for i2 in range(1, nf):
                        if stf[i2, cv2.CC_STAT_AREA] < 40:
                            m[lf == i2] = 0
                    solo_cart_ink[ch] = (trace_mask_g1(
                        m * 255, min_area=25, scale=1.0, eps=1.2,
                        corner_deg=80.0) if (m > 0).any() else [])
            if have_surf:
                solo_net = ribbon_network(
                    ink_centerlines(
                        ink_raw_of(os.path.join(rd,
                                       f"solosurfink{ch}.png"))),
                    surf_w)
                m = solo_net if solo_net is not None else np.zeros(
                    chain_lbl.shape, np.uint8)
                nf, lf, stf, _ = cv2.connectedComponentsWithStats(m, 8)
                for i2 in range(1, nf):
                    if stf[i2, cv2.CC_STAT_AREA] < 40:
                        m[lf == i2] = 0
                solo_surf_ink[ch] = (trace_mask_g1(
                    m * 255, min_area=25, scale=1.0, eps=1.2,
                    corner_deg=80.0) if (m > 0).any() else [])
            print(f"[vec] solo ink {ch}: cart={len(solo_cart_ink.get(ch, []))}"
                  f" surf={len(solo_surf_ink.get(ch, []))} paths", flush=True)

    def build(variant):
        """ONE layer per chain: <g id="Chain_ch"> containing that chain's
        own cartoon subgroup (inner) and surface subgroup (outer wrap).
        both mode: the surface subgroup carries fill-opacity -> the classic
        'cartoon seen through a translucent surface' figure. Single-rep
        modes: that rep only, fully opaque.

        full mode: beneath the visible content, the chain group also
        carries its COMPLETE content traced from the solo renders, wrapped
        in <g clip-path="url(#visCH)"> (the chain's visible region).
        Assembled look is unchanged (visible content paints on top);
        releasing the clip in Illustrator exposes the occluded parts."""
        groups = []
        for ci, ch in enumerate(chains):
            def cart_col():
                return (colors[ch] if variant == "palette"
                        else "#%02X%02X%02X" % scale_hex(
                            args.mono_color,
                            max(0.25, 1.0 - ci * args.mono_step)))

            lines = [f'<g id="Chain_{ch}">']

            # ---------- complete chain (solo) ----------
            # no clip-path wrapper: Illustrator's SVG import may DROP
            # clipPaths with many/small contours (the "clipping will be
            # lost" import warning), and it is not needed - the group is
            # hidden anyway (display=none), which alone keeps the
            # assembled render byte-identical to visible mode. The user
            # enables Chain_X_full in the layers panel to reveal the
            # complete chain in place.
            if full:
                # display=none: hidden by default. The both-mode surface
                # shell is translucent (fill-opacity 0.4) - a visible full
                # shell would tint through the occluding chains' own
                # translucent shells and shift the assembled colors.
                lines.append(f'<g id="Chain_{ch}_full" display="none">')
                if have_cart:
                    salw = solo_allow[ch]
                    sfm = (cv2.dilate(solo_masks[ch].astype(np.uint8),
                                      np.ones((3, 3), np.uint8),
                                      iterations=4) & salw).astype(bool)
                    fills = chain_fills(sfm, solo_dim[ch], thresholds,
                                        cart_col(), args.shade_step,
                                        allowed=salw)
                    lines.append(f'<g id="Chain_{ch}_full_cartoon">')
                    lines += [f'<path d="{d}" fill="rgb({c[0]},{c[1]},{c[2]})"/>'
                              for d, c in fills]
                    lines += [f'<path d="{d}" fill="rgb({c[0]},{c[1]},{c[2]})"/>'
                              for d, c in [(d, ink_rgb) for d in
                                           solo_cart_ink[ch]]]
                    lines.append("</g>")
                    print(f"[vec] {variant} full cart {ch}: "
                          f"{len(fills)} paths", flush=True)
                if have_surf:
                    base = (hex_to_rgb(colors[ch]) if variant == "palette"
                            else scale_hex(args.mono_color,
                                           max(0.25, 1.0 - ci * args.mono_step)))
                    if rep == "both":
                        base = wash(base, args.surf_wash)
                    mid = tuple(int(v * 0.94) for v in base)
                    dark = tuple(int(v * 0.85) for v in base)
                    s_sm = solo_smasks[ch]
                    s_lum = solo_slum[ch]
                    alw = solo_surf_allow[ch]
                    fills = ([(d, base) for d in band_paths(s_sm, alw)]
                             + [(d, mid) for d in
                                band_paths(clean_band(s_sm & (s_lum < sthr_hi)), alw)]
                             + [(d, dark) for d in
                                band_paths(clean_band(s_sm & (s_lum < sthr_lo)), alw)])
                    op = ' fill-opacity="0.4"' if rep == "both" else ""
                    lines.append(f'<g id="Chain_{ch}_full_surface"{op}>')
                    lines += [f'<path d="{d}" fill="rgb({c[0]},{c[1]},{c[2]})"/>'
                              for d, c in fills]
                    lines += [f'<path d="{d}" fill="rgb({c[0]},{c[1]},{c[2]})"/>'
                              for d, c in [(d, surf_ink_rgb) for d in
                                           solo_surf_ink[ch]]]
                    lines.append("</g>")
                    print(f"[vec] {variant} full surf {ch}: "
                          f"{len(fills)} paths", flush=True)
                lines.append("</g>")        # Chain_ch_full

            # ---------- visible content (original logic) ----------
            vis = [f'<g id="Chain_{ch}_visible">'] if full else []

            if have_cart:
                col_hex = cart_col()
                m = masks[ch].astype(np.uint8)
                grown = cv2.dilate(m, np.ones((3, 3), np.uint8), iterations=4)
                fm = (grown & allowed).astype(bool)
                fills = chain_fills(fm, dim, thresholds, col_hex,
                                    args.shade_step, allowed=allowed)
                ci_ink = [(d, ink_rgb) for d in cart_ink[ch]]
                vis.append(f'<g id="Chain_{ch}_cartoon">')
                vis += [f'<path d="{d}" fill="rgb({c[0]},{c[1]},{c[2]})"/>'
                        for d, c in fills]
                vis += [f'<path d="{d}" fill="rgb({c[0]},{c[1]},{c[2]})"/>'
                        for d, c in ci_ink]
                vis.append("</g>")
                print(f"[vec] {variant} cart {ch}: {len(fills)} paths",
                      flush=True)

            if have_surf:
                base = (hex_to_rgb(colors[ch]) if variant == "palette"
                        else scale_hex(args.mono_color,
                                       max(0.25, 1.0 - ci * args.mono_step)))
                if rep == "both":
                    base = wash(base, args.surf_wash)   # pale + translucent
                mid = tuple(int(v * 0.94) for v in base)
                dark = tuple(int(v * 0.85) for v in base)
                fills = [(d, base) for d in band_paths(smasks[ch])]
                fills += [(d, mid) for d in
                          band_paths(clean_band(smasks[ch] & (slum < sthr_hi)))]
                fills += [(d, dark) for d in
                          band_paths(clean_band(smasks[ch] & (slum < sthr_lo)))]
                si = [(d, surf_ink_rgb) for d in surf_ink[ch]]
                op = ' fill-opacity="0.4"' if rep == "both" else ""
                vis.append(f'<g id="Chain_{ch}_surface"{op}>')
                vis += [f'<path d="{d}" fill="rgb({c[0]},{c[1]},{c[2]})"/>'
                        for d, c in fills]
                vis += [f'<path d="{d}" fill="rgb({c[0]},{c[1]},{c[2]})"/>'
                        for d, c in si]
                vis.append("</g>")
                print(f"[vec] {variant} surf {ch}: {len(fills)} paths",
                      flush=True)

            lines += vis
            if full:
                lines.append("</g>")        # Chain_ch_visible
            lines.append("</g>")            # Chain_ch
            groups.append(lines)
        return groups

    # ---- silhouette clip (covers whichever reps are active) ----
    h4, w4 = union.shape[0] * up, union.shape[1] * up
    big_u = cv2.resize(union.astype(np.float32), (w4, h4),
                       interpolation=cv2.INTER_LINEAR) > 0.5
    clip_d = [d for d in trace_mask(big_u.astype(np.uint8) * 255,
                                    MIN_AREA * up * up, 1.0 / up, EPS * up,
                                    CORNER_DEG)]
    clip_defs = ['<defs><clipPath id="silhouette">']
    for d in clip_d:
        clip_defs.append(f'<path d="{d}"/>')
    clip_defs.append('</clipPath></defs>')
    clip_attr = ' clip-path="url(#silhouette)"'

    for suffix, groups in (("_palette", build("palette")),
                           ("_mono", build("mono"))):
        path = args.out_prefix + suffix + ".svg"
        lines = clip_defs[:] + [ln for g in groups for ln in g]
        open(path, "w", encoding="utf-8").write(svg_document(size, args.bg,
                                                             lines))
        print("[svg] wrote", path, flush=True)

    # ---- automatic fill audit: render the fills and compare against the
    #      allowed region. Reports (and visualizes, if --audit given)
    #      overshoot (painted outside silhouette+1px) and shortfall
    #      (silhouette pixels left unpainted). ----
    import re as _re
    import fitz
    for suffix in ("_palette", "_mono"):
        svg = open(args.out_prefix + suffix + ".svg", encoding="utf-8").read()
        if full:
            # audit only the VISIBLE content: in full mode the chain
            # groups also carry the clipped solo content, which MuPDF
            # cannot clip (it ignores clipPath)
            fills = "".join(m.group(0) for m in _re.finditer(
                r'<g id="Chain_\w+_visible">.*?</g>\s*</g>', svg, _re.S))
        else:
            i0 = svg.find('<g id="Chain_')
            i1 = svg.rfind("</g>")            # close of the last chain group
            fills = svg[i0:i1 + 4] if i0 != -1 else ""
        mini = ('<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{size[0]}" height="{size[1]}" '
                f'viewBox="0 0 {size[0]} {size[1]}">'
                f'<rect width="{size[0]}" height="{size[1]}" '
                f'fill="#FFFFFF"/>'
                + "".join(clip_defs) + fills + "</svg>")
        fn = os.path.join(rd, f"_audit{suffix}.svg")
        open(fn, "w", encoding="utf-8").write(mini)
        pm = fitz.open(fn)[0].get_pixmap(matrix=fitz.Matrix(1, 1))
        rast = (np.frombuffer(pm.samples, np.uint8)
                .reshape(pm.height, pm.width, pm.n)[:, :, :3])
        painted = rast.min(axis=2) < 240
        over = painted & ~allowed
        short = union & ~painted
        print(f"[audit{suffix}] overshoot px: {int(over.sum())}, "
              f"shortfall px: {int(short.sum())} "
              f"of {int(union.sum())} silhouette px", flush=True)
        if getattr(args, "audit", None):
            vis = rast.copy()
            vis[over] = (255, 0, 0)
            vis[short] = (255, 255, 0)
            Image.fromarray(vis).save(args.audit + suffix + ".png")

    if args.compare:
        import fitz
        rows = []
        prev = Image.open(os.path.join(rd, "prev.png")).convert("RGB")
        prev = prev.resize((1200, round(1200 * prev.height / prev.width)),
                           Image.LANCZOS)
        for suffix in ("_palette", "_mono"):
            doc = fitz.open(args.out_prefix + suffix + ".svg")
            pm = doc[0].get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
            vec = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
            rows.append((prev, vec))
        wmax = max(p.width for r in rows for p in r)
        htot = sum(max(a.height, b.height) for a, b in rows) + 12 * (len(rows) + 1)
        sheet = Image.new("RGB", (wmax * 2 + 36, htot), (230, 230, 230))
        y = 12
        for a, b2 in rows:
            sheet.paste(a, (12, y))
            sheet.paste(b2, (24 + a.width, y))
            y += max(a.height, b2.height) + 12
        sheet.save(args.compare)
        print("[cmp] wrote", args.compare, flush=True)


if __name__ == "__main__":
    main()
