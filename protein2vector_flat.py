"""flat_trace main pipeline: render -> vectorize -> AI export.

Run:
  python render_flat.py --pdb X.pdb --out-dir out --rep both   (PyMOL env)
  python vectorize_flat.py --renders-dir out
      --chains A,B,C --out-prefix out/flat --rep both
  python protein2vector_flat.py --out-dir out
      --svgs out/flat_palette.svg,out/flat_mono.svg --rep both
"""
import argparse
import os
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "templates", "ai_export_template.jsx")


def run_ai(svgs, names, out_dir):
    """Generate JSX from the template and drive Illustrator via COM."""
    out_dir = os.path.abspath(out_dir)
    jobs = []
    for svg in svgs:
        svg = os.path.abspath(svg)
        base = os.path.splitext(os.path.basename(svg))[0]
        out = os.path.join(out_dir, base + ".ai")
        jobs.append([svg.replace("\\", "/"), out.replace("\\", "/"),
                     base, names])
    report = os.path.join(out_dir, "_ai_report.txt")
    tpl = open(TEMPLATE, encoding="utf-8").read()
    js = (tpl.replace("{{OUTDIR}}", out_dir.replace("\\", "/"))
             .replace("{{JOBS}}", repr(jobs).replace("'", '"'))
             .replace("{{REPORT}}", report.replace("\\", "/")))
    js_path = os.path.join(tempfile.gettempdir(), "flat_ai_export.jsx")
    open(js_path, "w", encoding="utf-8").write(js)
    vbs = f'''Set ai = CreateObject("Illustrator.Application")
ai.DoJavaScript(GetFileContents("{js_path}"))
Function GetFileContents(p)
  Dim fso, f
  Set fso = CreateObject("Scripting.FileSystemObject")
  Set f = fso.OpenTextFile(p, 1)
  GetFileContents = f.ReadAll()
  f.Close
End Function
'''
    vbs_path = os.path.join(tempfile.gettempdir(), "flat_ai_export.vbs")
    open(vbs_path, "w").write(vbs)
    r = subprocess.run(["cscript", "//nologo", vbs_path], capture_output=True,
                       text=True, timeout=1200)
    print(r.stdout[-2000:])
    if r.returncode:
        print("VBS stderr:", r.stderr[-500:])
        combined = (r.stdout or "") + (r.stderr or "")
        if "创建对象" in combined or "create object" in combined.lower() \
                or "0x1ACE" in combined or "activeX" in combined.lower():
            print("[ai] Illustrator COM 创建失败：Illustrator 未安装，或安装后"
                  "未在系统注册表注册（部分精简版/绿色版会这样）。"
                  "SVG 产物不受影响；正式版重装或管理员运行一次通常可恢复。")
    print("[ai] report:")
    try:
        print(open(report, encoding="utf-8").read())
    except OSError:
        print("  (no report file)")
    missing = [j[1] for j in jobs if not os.path.exists(j[1])]
    if missing:
        print("[ai] MISSING:", missing)
    else:
        print("[ai] all outputs written")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--svgs", required=True,
                    help="comma-separated SVG paths to convert")
    ap.add_argument("--layers", default="Chain_A,Chain_B,Chain_C",
                    help="layer names (one per chain group)")
    args = ap.parse_args()
    names = [n.strip() for n in args.layers.split(",") if n.strip()]
    run_ai([s.strip() for s in args.svgs.split(",")], names, args.out_dir)


if __name__ == "__main__":
    main()
