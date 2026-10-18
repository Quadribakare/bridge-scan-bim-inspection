"""
Scan-vs-BIM bridge inspection: one command for the whole pipeline.

    python run_pipeline.py --bim data/bim/bridge_BIM.obj \
        --scan data/scan/bridge_scan_west.laz data/scan/bridge_scan_east.laz \
        --out outputs [--answer-key <folder>]

The BIM's component table must sit next to the mesh as <stem>_components.csv.
Scan tile names containing "west"/"east" are used to break the end-to-end
orientation tie on symmetric structures (see step 4b). All thresholds are in
config.yaml. Each step's log goes to <out>/logs/. --answer-key is optional and
only used by the separate scoring step (step 9); the pipeline never reads it.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT / "pipeline"
STEPS = {p.stem: p for p in PIPELINE.rglob("step*.py")}   # step name -> script, in its stage folder


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bim", required=True, help="BIM mesh (.obj)")
    ap.add_argument("--scan", required=True, nargs="+", help="scan tiles (.laz)")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--answer-key", help="optional: answer-key folder, to score the result (step 9)")
    ap.add_argument("--report-only", action="store_true",
                    help="only (re)write report.md from an existing run in --out (step 10)")
    args = ap.parse_args()

    bim = Path(args.bim).resolve()
    comps = bim.with_name(bim.stem + "_components.csv")
    scans = [Path(s).resolve() for s in args.scan]
    out = Path(args.out).resolve()
    for p in [bim, comps, *scans]:
        if not p.exists():
            sys.exit(f"missing input: {p}")
    (out / "logs").mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())["pipeline"]

    env = dict(os.environ, LASER_BIM=str(bim), LASER_SCANS=os.pathsep.join(map(str, scans)),
               LASER_OUT=str(out), PYTHONIOENCODING="utf-8", MPLBACKEND="Agg",
               PYTHONPATH=os.pathsep.join([str(PIPELINE / "common"), os.environ.get("PYTHONPATH", "")]))

    def run(step, *extra, tag=None):
        t0 = time.time()
        log = out / "logs" / f"{tag or step}.log"
        print(f"  {step:32s}", end="", flush=True)
        with log.open("w", encoding="utf-8") as f:
            r = subprocess.run([sys.executable, "-u", str(STEPS[step]), *extra],
                               stdout=f, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
        print(f"{'ok' if r.returncode == 0 else 'FAILED'}  ({time.time() - t0:.0f} s)")
        if r.returncode != 0:
            sys.exit(f"step {step} failed -- see {log}")

    def movement_model():
        p = out / "step7b_global_model.npz"
        if not p.exists():
            return None
        g = np.load(p)
        xs = np.linspace(g["support_x"].min(), g["support_x"].max(), 200)
        return (np.interp(xs, g["support_x"], g["support_dz"])
                + np.interp(xs, g["sag_curve_x"], g["sag_curve_y"], left=0.0, right=0.0))

    print(f"BIM   {bim}\nscans {', '.join(s.name for s in scans)}\nout   {out}\n")
    t_all = time.time()
    if args.report_only:
        print("10   report (LLM-written, from the existing results)")
        run("step10_report")
        print(f"\ndone -> {out / 'report.md'}")
        return
    print("1-3  load, clean, extract the structure")
    for s in ["step1_load", "step2_clean", "step3_connected_bridge", "step3_render_bridge_only",
              "step3_render_connected"]:
        run(s)
    print("4    align (coarse + ICP) and orientation ambiguity check")
    for s in ["step4_align", "step4_fix_twist", "step4_rigorous_align", "step4b_orientation_check"]:
        run(s)
    print("5-6  deviation profile and heatmaps")
    for s in ["step5_dz_profile", "step6_damage_heatmap", "step6_damage_heatmap_topdown"]:
        run(s)
    print("7A/7B component matching <-> deck movement model, until the model settles")
    run("step7a_realign")
    prev = None
    for it in range(1, cfg["movement_model_max_iterations"] + 1):
        run("step7a_component_deviation", tag=f"step7a_component_deviation_{it}")
        run("step7b_global_movement", tag=f"step7b_global_movement_{it}")
        cur = movement_model()
        change = np.inf if prev is None or cur is None else float(np.abs(cur - prev).max())
        print(f"       iteration {it}: deck movement model changed by "
              f"{'-' if change == np.inf else f'{change:.2f} mm'}")
        if change <= cfg["movement_model_tolerance_mm"]:
            break
        prev = cur
    print("7B   local defects, girder flanges, inspection views")
    for s in ["step7b_local_defects", "step7b_girder_flanges", "step7b_inspection_views"]:
        run(s)
    print("8    defect list")
    run("step8_defects")
    if args.answer_key:
        print("9    scoring against the answer key")
        run("step9_evaluate", "--answer-key", str(Path(args.answer_key).resolve()))
    print("10   report")
    run("step10_report")
    print(f"\ndone in {(time.time() - t_all) / 60:.1f} min -> {out / 'defects.csv'}, {out / 'report.md'}")


if __name__ == "__main__":
    main()
