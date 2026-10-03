"""flat_trace branch: trace line-art from PyMOL + layered flat fills.

No lighting, no gradients, no ray-traced shading: ambient 1 / direct 0.
Run with a PyMOL environment's python. Branch copy of protein2vector/steps/
render_pass.py helpers (working scripts untouched).

The ~18 render channels (per-chain masks, depth, SSE, shades, ink, prev,
surface passes) are mutually independent and the camera setup is
deterministic, so they can be rendered by several PyMOL processes in
parallel (this build has no OpenMP, so one process = one core):
  --workers N   N parallel PyMOL workers (0 = auto = cores/2, capped 8)
  --workers 1   single sequential session (legacy behavior)
Each worker re-loads the PDB and re-applies the same deterministic view,
so output is pixel-identical to the sequential render.

Outputs (in --out-dir):
  mask{CH}.png   2x  chain CH frontmost-visible region, white on black
  depth.png      2x  fog depth map (near = white, far = black)
  sse.png + sse_palette.json  2x  per-run flat segment colors
  shade0/120/240.png  2x  three-light set for crease extraction
  ink.png        1x  mode-1 depth-edge outlines, white on black
  prev.png       1x  flat color raster preview (white bg)
  surfmask{CH}.png / surfshade.png / surfink.png  surface passes

Full layering (--layer-mode full) adds per-chain SOLO channels rendered
with every other chain disabled (the complete chain, occluded parts
included). The camera never changes, so solo channels stay pixel-aligned
with the scene channels:
  solomask{CH}.png / solodepth{CH}.png / soloink{CH}.png   cartoon
  solosurfmask{CH}.png / solosurfshade{CH}.png / solosurfink{CH}.png
                                                           surface passes

CLI:
  --pdb PATH --out-dir DIR
  --chains A,B,C          (default: all ATOM chains)
  --colors A:1,1,0.5;B:.. per-chain RGB 0-1
  --mono 1                one color for all chains
  --width 2400 --height 2132
  --view auto|orient --view-angles rx,ry,rz
  --rep cartoon|surface|both
  --layer-mode visible|full  (full = extra per-chain solo channels)
  --workers 0
  --specs maskA,depth     (internal: worker subset; empty = all)
"""
import argparse
import os
import subprocess
import sys
import time

import numpy as np

cmd = None            # lazily imported (the parallel parent needs no PyMOL)
COORDS0 = []          # pristine atom coordinates (restored after shades)


def get_cmd():
    global cmd
    if cmd is None:
        from pymol import cmd as _cmd
        cmd = _cmd
    return cmd


def rodrigues(a, b):
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else -np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / (np.linalg.norm(v) ** 2))


def rot_z(deg):
    t = np.radians(deg)
    return np.array([[np.cos(t), -np.sin(t), 0],
                     [np.sin(t), np.cos(t), 0], [0, 0, 1]])


def apply_transform(model, R, t):
    cmd.alter_state(1, model, "(x,y,z) = (R.dot((x,y,z)) + T).tolist()",
                    space={"R": R, "T": t})


def setup_view(model, mode, angles, chains):
    cents = {}
    for ch in chains:
        vals = []
        cmd.iterate_state(1, f"{model} and chain {ch} and name CA",
                          "vals.append((x,y,z))", space={"vals": vals})
        if vals:
            cents[ch] = np.array(vals).mean(axis=0)
    center = np.mean(list(cents.values()), axis=0)
    apply_transform(model, np.eye(3), -center)
    cents = {k: v - center for k, v in cents.items()}

    if mode == "auto" and len(cents) >= 3:
        ids = list(cents)
        n = np.cross(cents[ids[1]] - cents[ids[0]], cents[ids[2]] - cents[ids[0]])
        n = n / np.linalg.norm(n)
        R = rodrigues(n, np.array([0, 0, 1.0]))
        apply_transform(model, R, np.zeros(3))
        p = R @ cents[ids[0]]
        cur = np.degrees(np.arctan2(p[1], p[0]))
        apply_transform(model, rot_z(90 - cur), np.zeros(3))
    else:
        cmd.orient(model)

    if angles:
        rx, ry, rz = (float(v) for v in angles.split(","))
        cmd.rotate("x", rx)
        cmd.rotate("y", ry)
        cmd.rotate("z", rz)
    cmd.zoom("all", buffer=3)


def parse_colors(spec, chains, mono):
    defaults = [
        (1.00, 1.00, 0.50), (0.67, 0.67, 1.00), (1.00, 0.71, 0.76),
        (0.67, 0.87, 0.67), (1.00, 0.78, 0.47), (0.78, 0.67, 1.00),
        (0.98, 0.50, 0.45), (0.40, 0.80, 0.80), (1.00, 0.84, 0.00),
        (0.47, 0.53, 0.80), (0.85, 0.44, 0.84), (0.70, 0.90, 0.30),
        (0.53, 0.81, 0.92), (0.88, 0.20, 0.40), (0.50, 0.90, 0.75),
        (0.90, 0.90, 0.55), (0.87, 0.68, 1.00), (1.00, 0.55, 0.40),
        (0.60, 1.00, 0.80), (0.55, 0.70, 0.90)]
    out = {}
    if spec:
        for part in spec.split(";"):
            if part:
                ch, rgb = part.split(":")
                out[ch] = tuple(float(v) for v in rgb.split(","))
    for i, ch in enumerate(chains):
        if ch not in out:
            out[ch] = defaults[0] if mono else defaults[i % len(defaults)]
    return out


def enable_only(names):
    for obj in cmd.get_names("objects"):
        cmd.disable(obj)
    for n in names:
        cmd.enable(n)


def snap(name, w, h, opaque, bg):
    cmd.set("ray_opaque_background", 1 if opaque else 0)
    cmd.bg_color(bg)
    cmd.zoom("cplx", buffer=3)
    cmd.ray(w, h)
    cmd.png(name, dpi=300)
    print("[render]", name, flush=True)


def flat_light():
    cmd.set("ambient", 1.0)
    cmd.set("direct", 0.0)
    cmd.set("specular", 0.0)
    cmd.set("depth_cue", 0)
    cmd.set("fog", 0)
    cmd.set("gamma", 1.0)
    cmd.set("ray_trace_mode", 0)


def detect_chains(pdb_path, keep_arg):
    chains, seen = [], set()
    for ln in open(pdb_path):
        if ln.startswith("ATOM"):
            ch = ln[21]
            if ch not in seen:
                seen.add(ch)
                chains.append(ch)
    if keep_arg:
        keep = [c.strip() for c in keep_arg.split(",")]
        chains = [c for c in chains if c in keep]
    return chains


def setup_session(args, chains, colors, with_surface):
    """Deterministic session state: identical in every worker."""
    global COORDS0
    get_cmd()
    cmd.reinitialize()
    os.makedirs(args.out_dir, exist_ok=True)
    cmd.load(args.pdb, "cplx")
    cmd.remove("solvent")
    cmd.remove("hetatm")
    cmd.remove("cplx and not (chain " + " or chain ".join(chains) + ")")
    for ch in chains:
        cmd.set_color(f"_col{ch}", colors[ch])
    setup_view("cplx", args.view, args.view_angles, chains)
    cmd.set("antialias", 2)
    for ch in chains:
        cmd.create(f"cart{ch}", f"cplx and chain {ch}")
        cmd.hide("everything", f"cart{ch}")
        cmd.show("cartoon", f"cart{ch}")
    all_cart = [f"cart{ch}" for ch in chains]
    all_surf = []
    if with_surface:
        cmd.set("surface_quality", 1)
        for ch in chains:
            cmd.create(f"surf{ch}", f"cplx and chain {ch}")
            cmd.hide("everything", f"surf{ch}")
            cmd.show("surface", f"surf{ch}")
        all_surf = [f"surf{ch}" for ch in chains]
    cmd.disable("all")
    # pristine coordinates: cmd.rotate bakes rotations into the coordinate
    # cache with float rounding (view/object matrices stay untouched), so
    # shade passes rotate via alter_state and restore from this snapshot
    COORDS0 = []
    cmd.iterate_state(1, "cplx", "C.extend((x,y,z))", space={"C": COORDS0})
    return all_cart, all_surf


def restore_coords():
    cmd.alter_state(1, "cplx", "(x,y,z) = (C.pop(0), C.pop(0), C.pop(0))",
                    space={"C": list(COORDS0)})


def render_spec(spec, args, chains, all_cart, all_surf):
    """Configure lighting/colors for one channel and snapshot it."""
    W2, H2 = args.width * 2, args.height * 2
    od = args.out_dir

    if spec.startswith("surfmask"):
        ch = spec[len("surfmask"):]
        flat_light()
        for c2 in chains:
            cmd.color("white" if c2 == ch else "black", f"surf{c2}")
        enable_only(all_surf)
        snap(rf"{od}\surfmask{ch}.png", W2, H2, True, "black")

    elif spec == "surfshade":
        for c2 in chains:
            cmd.color("white", f"surf{c2}")
        enable_only(all_surf)
        cmd.set("ambient", 0.45)
        cmd.set("direct", 0.55)
        cmd.set("specular", 0.0)
        cmd.set("depth_cue", 0)
        cmd.set("fog", 0)
        cmd.set("gamma", 1.0)
        cmd.set("ray_trace_mode", 0)
        snap(rf"{od}\surfshade.png", W2, H2, True, "black")

    elif spec == "surfink":
        flat_light()
        cmd.set("ray_trace_mode", 1)
        for c2 in chains:
            cmd.color("black", f"surf{c2}")
        enable_only(all_surf)
        snap(rf"{od}\surfink.png", args.width, args.height, True, "black")
        cmd.set("ray_trace_mode", 0)

    # ---- full-layering solo channels: this chain alone in the scene ----
    elif spec.startswith("solomask"):
        ch = spec[len("solomask"):]
        flat_light()
        cmd.color("white", f"cart{ch}")
        enable_only([f"cart{ch}"])
        snap(rf"{od}\solomask{ch}.png", W2, H2, True, "black")

    elif spec.startswith("solodepth"):
        ch = spec[len("solodepth"):]
        flat_light()
        cmd.color("white", f"cart{ch}")
        enable_only([f"cart{ch}"])
        cmd.set("depth_cue", 1)
        cmd.set("fog", 1)
        cmd.set("fog_start", 0.0)   # same fog slab as the scene depth pass
        snap(rf"{od}\solodepth{ch}.png", W2, H2, True, "black")
        cmd.set("depth_cue", 0)
        cmd.set("fog", 0)

    elif spec.startswith("soloink"):
        ch = spec[len("soloink"):]
        cmd.set("ambient", 0.35)
        cmd.set("direct", 0.65)
        cmd.set("specular", 0.0)
        cmd.set("depth_cue", 0)
        cmd.set("fog", 0)
        cmd.set("gamma", 1.0)
        cmd.set("ray_trace_mode", 1)
        cmd.color("black", f"cart{ch}")
        enable_only([f"cart{ch}"])
        snap(rf"{od}\soloink{ch}.png", args.width, args.height, True, "black")
        cmd.set("ray_trace_mode", 0)

    elif spec.startswith("solosurfmask"):
        ch = spec[len("solosurfmask"):]
        flat_light()
        cmd.color("white", f"surf{ch}")
        enable_only([f"surf{ch}"])
        snap(rf"{od}\solosurfmask{ch}.png", W2, H2, True, "black")

    elif spec.startswith("solosurfshade"):
        ch = spec[len("solosurfshade"):]
        cmd.color("white", f"surf{ch}")
        enable_only([f"surf{ch}"])
        cmd.set("ambient", 0.45)
        cmd.set("direct", 0.55)
        cmd.set("specular", 0.0)
        cmd.set("depth_cue", 0)
        cmd.set("fog", 0)
        cmd.set("gamma", 1.0)
        cmd.set("ray_trace_mode", 0)
        snap(rf"{od}\solosurfshade{ch}.png", W2, H2, True, "black")

    elif spec.startswith("solosurfink"):
        ch = spec[len("solosurfink"):]
        flat_light()
        cmd.set("ray_trace_mode", 1)
        cmd.color("black", f"surf{ch}")
        enable_only([f"surf{ch}"])
        snap(rf"{od}\solosurfink{ch}.png", args.width, args.height, True,
             "black")
        cmd.set("ray_trace_mode", 0)

    elif spec.startswith("mask"):
        ch = spec[len("mask"):]
        flat_light()
        for c2 in chains:
            cmd.color("white" if c2 == ch else "black", f"cart{c2}")
        enable_only(all_cart)
        snap(rf"{od}\mask{ch}.png", W2, H2, True, "black")

    elif spec == "depth":
        flat_light()          # explicit: legacy inherited it from the mask
        for c2 in chains:     # passes that always preceded depth
            cmd.color("white", f"cart{c2}")
        enable_only(all_cart)
        cmd.set("depth_cue", 1)
        cmd.set("fog", 1)
        cmd.set("fog_start", 0.0)   # full contrast: fog spans the whole slab
        snap(rf"{od}\depth.png", W2, H2, True, "black")
        cmd.set("depth_cue", 0)
        cmd.set("fog", 0)

    elif spec == "sse":
        import colorsys
        import json
        info = []
        cmd.iterate("cplx and name CA", "info.append((chain, resi, ss))",
                    space={"info": info})
        info.sort(key=lambda t: (t[0], int(t[1])))
        runs = []
        for ch, resi, ss in info:
            resi = int(resi)
            if runs and runs[-1][0] == ch and runs[-1][3] == ss \
                    and resi - runs[-1][2] <= 5:
                runs[-1][2] = resi
            else:
                runs.append([ch, resi, resi, ss])
        palette = []
        flat_light()
        for i, run in enumerate(runs):
            h = (i * 0.61803) % 1.0
            s = 0.55 + ((i * 0.61803) % 1.0) * 0.35
            col = colorsys.hsv_to_rgb(h, s, 0.95)
            cmd.set_color(f"_sse{i}", col)
            palette.append([round(c * 255) for c in col])
            ch, r0, r1, _ = run
            cmd.color(f"_sse{i}", f"cplx and chain {ch} and resi {r0}-{r1}")
        for i, run in enumerate(runs):      # mirror onto the cartoon objects
            ch, r0, r1, _ = run
            cmd.color(f"_sse{i}", f"cart{ch} and resi {r0}-{r1}")
        enable_only(all_cart)
        cmd.set("ray_trace_mode", 0)
        snap(rf"{od}\sse.png", W2, H2, True, "black")
        with open(rf"{od}\sse_palette.json", "w") as fh:
            json.dump({"palette": palette,
                       "chains": [r[0] for r in runs]}, fh)
        print(f"[render] {len(runs)} SSE runs", flush=True)

    elif spec.startswith("shade"):
        deg = int(spec[len("shade"):])
        if deg:
            th = np.radians(deg)
            c, s = np.cos(th), np.sin(th)
            apply_transform("cplx",
                            np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]]),
                            np.zeros(3))
        for c2 in chains:
            cmd.color("white", f"cart{c2}")
        enable_only(all_cart)
        cmd.set("ambient", 0.35)
        cmd.set("direct", 0.65)
        cmd.set("specular", 0.0)
        cmd.set("depth_cue", 0)
        cmd.set("fog", 0)
        cmd.set("gamma", 1.0)
        cmd.set("ray_trace_mode", 0)
        snap(rf"{od}\shade{deg}.png", W2, H2, True, "black")
        if deg:
            restore_coords()          # exact float32 snapshot: no residue

    elif spec == "ink":
        # lighting explicit: legacy behavior inherited it from the shade
        # passes, which always preceded ink in the sequential order
        cmd.set("ambient", 0.35)
        cmd.set("direct", 0.65)
        cmd.set("specular", 0.0)
        cmd.set("depth_cue", 0)
        cmd.set("fog", 0)
        cmd.set("gamma", 1.0)
        cmd.set("ray_trace_mode", 1)
        for c2 in chains:
            cmd.color("black", f"cart{c2}")
        enable_only(all_cart)
        snap(rf"{od}\ink.png", args.width, args.height, True, "black")
        cmd.set("ray_trace_mode", 0)

    elif spec == "prev":
        flat_light()
        for c2 in chains:
            cmd.color(f"_col{c2}", f"cart{c2}")
        enable_only(all_cart)
        snap(rf"{od}\prev.png", args.width, args.height, True, "white")

    else:
        raise SystemExit(f"unknown channel spec: {spec}")


def build_specs(args, chains):
    specs = [f"mask{ch}" for ch in chains]
    specs += ["depth", "sse", "shade0", "shade120", "shade240", "ink", "prev"]
    if args.rep in ("surface", "both"):
        specs += [f"surfmask{ch}" for ch in chains]
        specs += ["surfshade", "surfink"]
    if args.layer_mode == "full":
        # solo = this chain alone: the complete chain (occluded parts in)
        specs += [f"solomask{ch}" for ch in chains]
        specs += [f"solodepth{ch}" for ch in chains]
        specs += [f"soloink{ch}" for ch in chains]
        if args.rep in ("surface", "both"):
            specs += [f"solosurfmask{ch}" for ch in chains]
            specs += [f"solosurfshade{ch}" for ch in chains]
            specs += [f"solosurfink{ch}" for ch in chains]
    return specs


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdb", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--chains", default="")
    ap.add_argument("--colors", default="")
    ap.add_argument("--mono", type=int, default=0)
    ap.add_argument("--width", type=int, default=2400)
    ap.add_argument("--height", type=int, default=2132)
    ap.add_argument("--view", choices=["auto", "orient"], default="auto")
    ap.add_argument("--view-angles", default="")
    ap.add_argument("--rep", choices=["cartoon", "surface", "both"],
                    default="cartoon")
    ap.add_argument("--layer-mode", choices=["visible", "full"],
                    default="visible",
                    help="full = also render per-chain solo channels "
                         "(complete chains for full layering)")
    ap.add_argument("--workers", type=int, default=0,
                    help="parallel PyMOL workers; 0 = auto (4), "
                         "1 = single sequential session")
    ap.add_argument("--specs", default="",
                    help="internal: comma-separated channel subset for one "
                         "worker; empty = all channels")
    return ap.parse_args()


def run_batch(args, chains, colors, specs):
    """One PyMOL session: set up state once, render its channels."""
    have_surf = any("surf" in s for s in specs)   # also solo surf channels
    all_cart, all_surf = setup_session(args, chains, colors, have_surf)
    for spec in specs:
        render_spec(spec, args, chains, all_cart, all_surf)
    cmd.quit()


def main():
    args = parse_args()
    chains = detect_chains(args.pdb, args.chains)
    if not chains:
        raise SystemExit("no chains selected")
    colors = parse_colors(args.colors, chains, args.mono)
    specs = args.specs.split(",") if args.specs else build_specs(args, chains)
    specs = [s for s in specs if s]

    workers = args.workers
    if workers == 0:
        workers = max(1, min(len(specs), 4))   # default: 4 parallel PyMOLs
    workers = min(workers, len(specs))

    t0 = time.time()
    if workers <= 1:
        run_batch(args, chains, colors, specs)
        print(f"[render] {len(specs)} channels in {time.time()-t0:.0f}s "
              "(sequential)", flush=True)
        return

    # parent: split channels across N workers by longest-processing-time
    # first (surface passes cost ~2x a cartoon pass); deterministic order
    batches = [[] for _ in range(workers)]
    load = [0.0] * workers
    for spec in sorted(specs, key=lambda s: (-(2.0 if "surf" in s
                                              else 1.0), specs.index(s))):
        i = load.index(min(load))
        batches[i].append(spec)
        load[i] += 2.0 if "surf" in spec else 1.0
    procs = []
    for b in batches:
        argv = [sys.executable, os.path.abspath(__file__),
                "--pdb", args.pdb, "--out-dir", args.out_dir,
                "--chains", ",".join(chains),
                "--colors", ";".join(f"{ch}:{','.join(str(v) for v in colors[ch])}"
                                     for ch in chains),
                "--mono", str(args.mono),
                "--width", str(args.width), "--height", str(args.height),
                "--view", args.view, "--rep", args.rep,
                "--layer-mode", args.layer_mode,
                "--workers", "1", "--specs", ",".join(b)]
        if args.view_angles:
            argv += ["--view-angles", args.view_angles]
        procs.append(subprocess.Popen(argv))
    rc = 0
    for p in procs:
        rc |= p.wait()
    if rc:
        sys.exit(rc)
    print(f"[render] {len(specs)} channels, {workers} workers, "
          f"{time.time()-t0:.0f}s", flush=True)


main()
