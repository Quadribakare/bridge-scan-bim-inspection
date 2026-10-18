"""
Step 9: score outputs/defects.csv against an answer key. Separate from the
pipeline: the answer-key folder is passed on the command line and nothing in
the pipeline reads it.

    python src/step9_evaluate.py --answer-key <folder with ground_truth.json + per_point_labels_*.npz>

Each true defect is located from its labelled points, put into BIM coordinates
with the answer key's own scan->BIM transform (so the scoring does not depend
on this pipeline's alignment). A reported finding matches a true defect if it
sits on one of the defect's labelled components, or within MATCH_M of its
labelled points (global findings without a height: within MATCH_M along x).
Findings matching no true defect are false alarms, reported by confidence.
"""
import argparse
import json
import re
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402
MATCH_M = 2.0

ap = argparse.ArgumentParser()
ap.add_argument("--answer-key", required=True)
ap.add_argument("--defects", default=str(OUT_DIR / "defects.csv"))
args = ap.parse_args()
key = Path(args.answer_key)
gt = json.loads((key / "ground_truth.json").read_text())

s1 = np.load(OUT_DIR / "step1_loaded.npz")
tile_order = [str(t) for t in s1["tile_names"]]          # same order step 1 loaded the tiles in
labels = [np.load(key / f"per_point_labels_{t}.npz") for t in tile_order]
defect_id = np.concatenate([lb["defect_id"] for lb in labels])
comp_id = np.concatenate([lb["component"] for lb in labels])
assert len(defect_id) == len(s1["xyz"]), "label rows must match step 1 point order"
tile_offset = np.cumsum([0] + [len(lb["defect_id"]) for lb in labels])[:-1]
M = np.array(gt["scan_to_BIM_transform_4x4"])
xyz_bim = s1["xyz"] @ M[:3, :3].T + M[:3, 3]
names = gt["components"]


def magnitudes(text):
    mm = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)\s*mm", text)]
    deg = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)\s*deg", text)]
    return (max(mm) if mm else np.nan), (deg[0] if deg else np.nan)


truth = []
for did, text in gt["defects"].items():
    m = defect_id == int(did)
    pts_all = xyz_bim[m]
    # robust extent: 2nd-98th percentile, so stray labelled points don't stretch it
    lo, hi = np.percentile(pts_all, 2, axis=0), np.percentile(pts_all, 98, axis=0)
    pts = pts_all[np.all((pts_all >= lo) & (pts_all <= hi), axis=1)]
    comps = sorted({names[c] for c in np.unique(comp_id[m]) if 0 <= c < len(names)})
    mm, deg = magnitudes(text)
    truth.append(dict(id=int(did), text=text, pts=pts, comps=comps, mm=mm, deg=deg,
                      centre=np.median(pts, axis=0), lo=lo, hi=hi))
# a defect labelled on many components is structure-wide (e.g. a sag, or a
# settlement the deck follows); a local defect sits on one or two components
for t in truth:
    t["is_global"] = len(t["comps"]) > 5

found = pd.read_csv(args.defects)


def base(name):
    return re.sub(r"_\d+$", "", str(name))   # parapet_post_N_012 -> parapet_post_N (answer-key naming)


def matches(f, t):
    """Global findings (sag, support movement: no height) can only match global
    defects, by distance along the bridge from the defect's centre. Local
    findings can only match local defects, and must be on one of the defect's
    labelled components AND within MATCH_M of its labelled points."""
    f_global = "global" in str(f["element_type"]) or np.isnan(f["z"])
    if f_global != t["is_global"]:
        return False
    if f_global:
        part_of = any(c.startswith(str(f["component"]) + "_") for c in t["comps"])
        return part_of or abs(t["centre"][0] - f["x"]) <= MATCH_M
    on_component = str(f["component"]) in t["comps"] or base(f["component"]) in t["comps"]
    near = np.min(np.linalg.norm(t["pts"] - [f["x"], f["y"], f["z"]], axis=1)) <= MATCH_M
    return on_component and near


hits = {t["id"]: [] for t in truth}
matched_rows = set()
for i, f in found.iterrows():
    for t in truth:
        if matches(f, t):
            hits[t["id"]].append(i)
            matched_rows.add(i)

lines = ["# Scorecard", "",
         f"Findings scored: `{Path(args.defects).name}` ({len(found)} rows). Answer key: {len(truth)} defects "
         f"({sum(t['is_global'] for t in truth)} structure-wide, {sum(not t['is_global'] for t in truth)} local).", "",
         f"Matching: a local finding matches a local defect only if it is on one of the defect's labelled components "
         f"AND within {MATCH_M} m of its labelled points. A global finding (sag, support movement) matches a "
         f"structure-wide defect if it is within {MATCH_M} m of the defect's centre along the bridge. "
         "Locations come from the answer key's own labels and scan-to-BIM transform, not from this pipeline's alignment.", "",
         "## Per defect", "",
         "| # | True defect | Found? | Matched finding(s) | Reported | True | Size error |",
         "|---|---|---|---|---|---|---|"]
n_found = 0
for t in truth:
    rows = found.loc[hits[t["id"]]]
    if len(rows):
        n_found += 1
        # best = the highest-confidence, then largest, matching finding
        rank = {"high": 0, "medium": 1, "low": 2}
        best = rows.assign(_r=rows["confidence"].map(rank), _a=rows["max_deviation_mm"].abs()) \
                   .sort_values(["_r", "_a"], ascending=[True, False]).iloc[0]
        rep_mm = abs(best["max_deviation_mm"])
        dm = re.search(r"lean ([+-]?\d+(?:\.\d+)?) deg", str(best["defect_type"]))
        rng = re.search(r"by (\d+)-(\d+) mm", str(best["defect_type"]))
        reported = f"{rep_mm:.1f} mm"
        err = f"{rep_mm - t['mm']:+.1f} mm ({(rep_mm - t['mm']) / t['mm'] * 100:+.0f}%)" if t["mm"] == t["mm"] else "-"
        if rng:
            lo, hi = float(rng.group(1)), float(rng.group(2))
            reported = f"{lo:.0f}-{hi:.0f} mm"
            err = (f"range {lo - t['mm']:+.0f} to {hi - t['mm']:+.0f} mm" + (" (contains true value)" if lo <= t["mm"] <= hi else ""))
        if dm and t["deg"] == t["deg"]:
            reported += f", {abs(float(dm.group(1))):.2f} deg"
            err += f"; angle {abs(float(dm.group(1))) - t['deg']:+.2f} deg"
        ids = ", ".join(f"{r.id} ({r.confidence})" for r in rows.itertuples())
        lines.append(f"| {t['id']} | {t['text']} | **found** | {ids} | {reported} | "
                     f"{t['mm']:g} mm" + (f", {t['deg']:g} deg" if t['deg'] == t['deg'] else "") + f" | {err} |")
    else:
        lines.append(f"| {t['id']} | {t['text']} | **missed** | - | - | {t['mm']:g} mm | - |")

fa = found.loc[[i for i in found.index if i not in matched_rows]]
lines += ["", "## Summary", "",
          f"- **Found {n_found} of {len(truth)}** true defects.",
          f"- Findings matching a true defect: {len(matched_rows)} of {len(found)}.",
          f"- **False alarms: {len(fa)}** - by confidence: " + ", ".join(
              f"{k} {int((fa['confidence'] == k).sum())}" for k in ("high", "medium", "low")) + "."]
hm = found[found["confidence"].isin(["high", "medium"])]
lines.append(f"- High + medium confidence findings: {len(hm)}, of which {len(hm) - int(fa['confidence'].isin(['high', 'medium']).sum())} "
             f"match a true defect.")
lines += ["", "## False alarms", ""]
if len(fa):
    lines += ["| id | component | finding | size (mm) | stations | confidence |", "|---|---|---|---|---|---|"]
    for r in fa.sort_values("confidence", key=lambda s: s.map({"high": 0, "medium": 1, "low": 2})).itertuples():
        lines.append(f"| {r.id} | {r.component} | {r.defect_type[:90]} | {r.max_deviation_mm:.1f} | {r.stations} | {r.confidence} |")
else:
    lines.append("- none")
lines += ["", "## Where each true defect is (from the answer key labels, BIM coordinates)", ""]
for t in truth:
    lines.append(f"- Defect {t['id']}: {len(t['pts']):,} labelled points, components {', '.join(t['comps'])}, "
                 f"x {t['lo'][0]:.1f} to {t['hi'][0]:.1f} m, y {t['lo'][1]:.1f} to {t['hi'][1]:.1f} m, "
                 f"z {t['lo'][2]:.1f} to {t['hi'][2]:.1f} m")
# ---- alignment error vs the true transform: the same physical points, put
# into BIM coordinates by the answer key's transform and by this pipeline ----
s2 = np.load(OUT_DIR / "step2_cleaned.npz")
is_bridge = np.load(OUT_DIR / "step3_connected_bridge.npz")["is_bridge"]
step1_idx = (s2["orig_index"] + tile_offset[s2["tile"]])[is_bridge]
true_xyz = xyz_bim[step1_idx]


def rigid_error(ours, true):
    """Best rigid transform taking true -> ours (Kabsch): rotation angle and translation."""
    ct, co = true.mean(axis=0), ours.mean(axis=0)
    U, _, Vt = np.linalg.svd((true - ct).T @ (ours - co))
    D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    ang = np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))
    yaw = np.degrees(np.arctan2(R[1, 0], R[0, 0]))
    return ang, yaw, co - R @ ct


lines += ["", "## Alignment error vs the true scan-to-BIM transform", "",
          "Same physical points (the 2.8M extracted bridge points), placed in BIM coordinates by the answer key's "
          "transform and by this pipeline. Error = pipeline position minus true position.", "",
          "| stage | median error | p95 | max | mean offset x / y / z (mm) | rotation error | at bridge ends (x<2 / x>58 m) |",
          "|---|---|---|---|---|---|---|"]
for label, f in [("step 4 alignment", "step4_aligned_final"), ("after orientation check (4b)", "step4b_oriented"),
                 ("final (7A robust re-alignment)", "step7a_realigned")]:
    ours = np.load(OUT_DIR / f"{f}.npz")["xyz_aligned"]
    err = np.linalg.norm(ours - true_xyz, axis=1) * 1000
    off = (ours - true_xyz).mean(axis=0) * 1000
    ang, yaw, _ = rigid_error(ours[::20], true_xyz[::20])
    ends = [np.median(err[true_xyz[:, 0] < 2]), np.median(err[true_xyz[:, 0] > 58])]
    lines.append(f"| {label} | {np.median(err):.1f} mm | {np.percentile(err, 95):.1f} mm | {err.max():.1f} mm | "
                 f"{off[0]:+.1f} / {off[1]:+.1f} / {off[2]:+.1f} | {ang:.4f} deg | "
                 f"{ends[0]:.1f} / {ends[1]:.1f} mm |")
lines.append("")
lines.append(f"(For scale: answer-key per-station registration error is "
             f"\"{gt.get('scanner_noise', 'n/a')}\".)")

(OUT_DIR / "scorecard.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
