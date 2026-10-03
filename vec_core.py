"""Verbatim extraction of the tracing primitives used by
vectorize_flat.py, copied from protein2vector/steps/tracer_cv.py
(as_pts, signed_area, smooth_segments, find_corners,
smooth_segments_corners, path_d, trace_contours) and
protein2vector/steps/vectorize_core.py (trace_mask, svg_document).
flat_trace is a self-contained branch: if the originals change and the
change is wanted here, re-copy and re-verify.
"""
import numpy as np
import cv2

MIN_AREA = 260          # at canonical 2400 space (drops tiny fragments)
EPS = 1.3
CORNER_DEG = 35.0

def as_pts(cnt):
    a = np.asarray(cnt)
    if a.ndim == 3:
        a = a[:, 0, :]
    return a.astype(float)

def signed_area(cnt):
    x, y = cnt[:, 0].astype(float), cnt[:, 1].astype(float)
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))

def smooth_segments(pts):
    n = len(pts)
    mids = (pts + np.roll(pts, -1, axis=0)) / 2.0
    segs = []
    for i in range(n):
        p0 = mids[i]
        ctrl = pts[(i + 1) % n]
        p2 = mids[(i + 1) % n]
        c1 = p0 + 2.0 / 3.0 * (ctrl - p0)
        c2 = p2 + 2.0 / 3.0 * (ctrl - p2)
        segs.append((p0, c1, c2, p2))
    return segs

def find_corners(pts, corner_deg=35.0):
    """Vertex indices whose turn angle exceeds corner_deg (sharp corners)."""
    n = len(pts)
    if n < 3:
        return []
    v1 = pts - np.roll(pts, 1, axis=0)
    v2 = np.roll(pts, -1, axis=0) - pts
    cross = v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0]
    dot = v1[:, 0] * v2[:, 0] + v1[:, 1] * v2[:, 1]
    ang = np.degrees(np.abs(np.arctan2(cross, dot)))
    return list(np.where(ang > corner_deg)[0])

def smooth_segments_corners(pts, corners):
    """Corner-aware smoothing: midpoint cubics within runs between corners,
    straight lines across corner vertices -> sharp tips stay sharp."""
    n = len(pts)
    segs = []          # list of ('C', p0, c1, c2, p2) or ('L', p0, p1)
    cset = set(int(c) for c in corners)
    if not cset:
        return [("C", p0, c1, c2, p2) for (p0, c1, c2, p2) in smooth_segments(pts)]
    starts = sorted(cset)
    for i, c0 in enumerate(starts):
        c1v = starts[(i + 1) % len(starts)]
        # vertices strictly after c0 up to (and including) c1v, cyclic
        run = []
        j = (c0 + 1) % n
        while j != c1v:
            run.append(pts[j])
            j = (j + 1) % n
        target = pts[c1v]
        if not run:
            segs.append(("L", pts[c0], target))
            continue
        # smooth run: from corner c0 through run points to target
        chain = [pts[c0]] + run + [target]
        mids = [(chain[k] + chain[k + 1]) / 2.0 for k in range(len(chain) - 1)]
        prev = chain[0]
        for k in range(len(run)):
            ctrl = chain[k + 1]
            p2 = mids[k]
            c1 = prev + 2.0 / 3.0 * (ctrl - prev)
            c2 = p2 + 2.0 / 3.0 * (ctrl - p2)
            segs.append(("C", prev, c1, c2, p2))
            prev = p2
        segs.append(("L", prev, target))
    return segs

def path_d(contours_pts, corner_deg=None):
    parts = []
    for pts in contours_pts:
        if corner_deg:
            segs = smooth_segments_corners(pts, find_corners(pts, corner_deg))
            p0 = segs[0][1] if segs[0][0] == "C" else segs[0][1]
            parts.append(f"M{p0[0]:.1f} {p0[1]:.1f}")
            for seg in segs:
                if seg[0] == "C":
                    _, p0_, c1, c2, p2 = seg
                    parts.append(f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} "
                                 f"{p2[0]:.1f} {p2[1]:.1f}")
                else:
                    _, pa, pb = seg
                    parts.append(f"L{pb[0]:.1f} {pb[1]:.1f}")
            parts.append("Z")
        else:
            segs = smooth_segments(pts)
            p0 = segs[0][0]
            parts.append(f"M{p0[0]:.1f} {p0[1]:.1f}")
            for _, c1, c2, p2 in segs:
                parts.append(f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} "
                             f"{p2[0]:.1f} {p2[1]:.1f}")
            parts.append("Z")
    return "".join(parts)

def trace_contours(mask, min_area, eps):
    out = []
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP,
                                           cv2.CHAIN_APPROX_NONE)
    if hierarchy is None:
        return out
    hierarchy = hierarchy[0]
    for j, cnt in enumerate(contours):
        if hierarchy[j][3] != -1:
            continue
        if cv2.contourArea(cnt) < min_area:
            continue
        outer = as_pts(cv2.approxPolyDP(cnt, eps, True))
        if len(outer) < 3:
            continue
        if signed_area(outer) < 0:
            outer = outer[::-1]
        holes = []
        k = hierarchy[j][2]
        while k != -1:
            hcnt = contours[k]
            if cv2.contourArea(hcnt) >= min_area * 0.4:
                hpts = as_pts(cv2.approxPolyDP(hcnt, eps, True))
                if signed_area(hpts) > 0:
                    hpts = hpts[::-1]
                if len(hpts) >= 3:
                    holes.append(hpts)
            k = hierarchy[k][0]
        out.append((outer, holes))
    return out

def trace_mask(mask, min_area=MIN_AREA, scale=0.5, eps=EPS, corner_deg=CORNER_DEG):
    shapes = []
    m = mask.astype(np.uint8) * 255
    for outer, holes in trace_contours(m, min_area, eps):
        shapes.append(path_d([outer * scale] + [h * scale for h in holes],
                             corner_deg=corner_deg))
    return shapes

def svg_document(size, bg, groups):
    w, h = size
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}"><g id="Background_Black">'
            f'<rect x="0" y="0" width="{w}" height="{h}" fill="{bg}"/></g>')
    return "\n".join([head] + groups + ["</svg>"])
