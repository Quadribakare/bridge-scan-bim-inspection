"""
Step 8: final defect list, built ONLY from verified measurements (no AI).

Sources: 7B part 1 global findings (sag, support movement), 7B large-face
tilt table, 7B local candidates (deviation + rough surface), 7B girder flange
check. Numeric findings on the same component within 2 m are merged into one
defect.

Confidence (fixed rules, set before any scoring):
  high   - major (not thin) component, >= 3 stations, |deviation| >= 4x the
           component's own noise and >= 0.1 m2 affected; or a flange bent beyond
           its matching margin (mostly unmatched points, >= 3 stations)
  medium - major component, >= 2 stations, above 4x noise
  low    - thin members, rough patches at sharp BIM edges, point losses at
           flange ends / fittings, anything else
Global rows: confirmed sag = high; support movement = medium (needs control
points); wall tilt = high if top offset > 4x the plane-fit scatter with
>= 3 stations, else low.
"""
import json
import numpy as np
import pandas as pd
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402
cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
THIN_M = cfg["component_matching"]["bbox_margin_m"]
MIN_MOVE = cfg["support_movement"]["min_movement_mm"]

comp = pd.read_csv(BIM_COMPONENTS)
dims = comp[["xmax", "ymax", "zmax"]].values - comp[["xmin", "ymin", "zmin"]].values
thin = dict(zip(comp["component"], dims.min(axis=1) <= THIN_M))
etype = dict(zip(comp["component"], comp["element_type"]))
own_noise = {}
for js in (OUT_DIR / "inspection_views").glob("*/*.json"):
    s = json.loads(js.read_text())
    if "noise_mm" in s and "component" in s and not js.stem.endswith("_residual"):
        own_noise[s["component"]] = s["noise_mm"]

rows = []

# ---- global findings ----
gf = json.loads((OUT_DIR / "step7b_global_findings.json").read_text())
for s in gf["sag_per_span"]:
    if s["confirmed"]:
        rows.append(dict(component=f"deck span {s['span_start_m']:.0f}-{s['span_end_m']:.0f} m", element_type="deck (global)",
                         defect_type="in-span sag relative to its own supports", x=s["at_x_m"], y=0.0, z=np.nan,
                         area_m2=np.nan, max_deviation_mm=s["deepest_mm"], stations=s["n_stations"],
                         confidence="high", evidence="7B global movement model"))
for r in gf["support_movement"]["evidence"]["supports"]:
    if "appears" in r.get("verdict", ""):
        rows.append(dict(component=r["support"], element_type="pier (global)",
                         defect_type=f"support movement: {r['verdict']}", x=r["x_m"], y=0.0, z=np.nan, area_m2=np.nan,
                         max_deviation_mm=r["ref_as_aligned_mm"], stations=r["n_stations"], confidence="medium",
                         evidence=f"as aligned {r['ref_as_aligned_mm']:+.1f} mm, levelled to deck over abutments "
                                  f"{r['ref_deck_over_abutments_mm']:+.1f} mm; no control points"))
tilts = pd.read_csv(OUT_DIR / "step7b_wall_tilts_large.csv")
for _, t in tilts[tilts["top_offset_mm"].abs() > MIN_MOVE].iterrows():
    strong = abs(t["top_offset_mm"]) > 4 * t["fit_rms_mm"] and t["n_stations"] >= 3
    c = comp[comp["component"] == t["component"]].iloc[0]
    rows.append(dict(component=t["component"], element_type=t["element_type"],
                     defect_type=f"wall tilt: lean {t['lean_deg']:+.2f} deg, top displaced {t['top_offset_mm']:+.1f} mm "
                                 f"(+ = outward) over a {t['face_width_m']:.1f} x {t['face_height_m']:.1f} m face",
                     x=round((c.xmin + c.xmax) / 2, 2), y=round((c.ymin + c.ymax) / 2, 2), z=round((c.zmin + c.zmax) / 2, 2),
                     area_m2=round(t["face_width_m"] * t["face_height_m"], 2), max_deviation_mm=t["top_offset_mm"],
                     stations=t["n_stations"], confidence="high" if strong else "low",
                     evidence=f"robust plane fit, scatter {t['fit_rms_mm']} mm"))

# ---- local numeric findings ----
cand = pd.read_csv(OUT_DIR / "candidates.csv")
flange = pd.read_csv(OUT_DIR / "girder_flange_candidates.csv")
loss = pd.read_csv(OUT_DIR / "girder_flange_loss_per_m.csv")
local = []
for _, r in cand.iterrows():
    name = r["component"]
    if r["candidate_type"] == "rough surface":
        edge = bool(r.get("edge_like"))
        local.append(dict(component=name, defect_type="rough/irregular surface" + (" at a sharp BIM edge" if edge else ""),
                          x=r["x"], y=r["y"], z=r["z"], area_m2=r["area_m2"], dev=r["max_abs_dev_mm"],
                          stations=r["n_stations"], tier="low" if edge or thin.get(name) else "medium",
                          evidence="7B rough-surface patch (points rejected by the normal check)"))
    else:
        noise = own_noise.get(name, np.nan)
        mult = abs(r["max_abs_dev_mm"]) / noise if noise == noise and noise > 0 else 0
        if thin.get(name):
            tier = "low"
        elif r["n_stations"] >= 3 and mult >= 4 and r["area_m2"] >= 0.1:
            tier = "high"
        elif r["n_stations"] >= 2 and mult >= 4:
            tier = "medium"
        else:
            tier = "low"
        local.append(dict(component=name, defect_type=f"local deviation ({r['surface_type']} surface)",
                          x=r["x"], y=r["y"], z=r["z"], area_m2=r["area_m2"], dev=r["max_abs_dev_mm"],
                          stations=r["n_stations"], tier=tier,
                          evidence=f"7B local search, {mult:.1f}x the component's own noise ({noise} mm)"))
for _, r in flange.iterrows():
    if r["candidate_type"].startswith("flange points lost"):
        lo, hi = r["x"] - r["n_cells"] / 2, r["x"] + r["n_cells"] / 2
        b = loss[(loss["component"] == r["component"]) & (loss["bin"] >= np.floor(lo)) & (loss["bin"] < hi)]
        beyond_margin = b["unmatched"].sum() > b["dropped"].sum()
        local.append(dict(component=r["component"],
                          defect_type=(f"flange deformed beyond its matching margin (surface {r['residual_mm']:+.0f} mm "
                                       "from expected)") if beyond_margin else "points rejected at flange end / fitting",
                          x=r["x"], y=r["y"], z=r["z"], area_m2=r["area_m2"], dev=r["residual_mm"], stations=r["n_stations"],
                          tier="high" if beyond_margin and r["n_stations"] >= 3 else "low",
                          evidence=f"7B flange loss check ({int(b['unmatched'].sum())} unmatched, "
                                   f"{int(b['dropped'].sum())} rejected points)"))
    else:
        noise = r["flange_noise_mm"]
        mult = abs(r["residual_mm"]) / noise
        tier = "high" if (r["n_stations"] >= 3 and mult >= 4 and r["area_m2"] >= 0.1) else (
            "medium" if r["n_stations"] >= 2 and mult >= 4 else "low")
        if abs(r["y"] - comp.set_index("component").loc[r["component"], "ymin"]) < 0.05 or \
                abs(r["y"] - comp.set_index("component").loc[r["component"], "ymax"]) < 0.05:
            tier = "low"          # on the flange edge
        local.append(dict(component=r["component"], defect_type=f"local flange deviation ({r['direction']})",
                          x=r["x"], y=r["y"], z=r["z"], area_m2=r["area_m2"], dev=r["residual_mm"],
                          stations=r["n_stations"], tier=tier,
                          evidence=f"7B flange check, {mult:.1f}x the flange's own noise ({noise} mm)"))

# merge local findings on the same component within 2 m
loc = pd.DataFrame(local)
loc["group"] = -1
gid = 0
rank = {"high": 0, "medium": 1, "low": 2}
for name, g in loc.groupby("component"):
    for i in g.index:
        if loc.at[i, "group"] >= 0:
            continue
        stack = [i]
        loc.at[i, "group"] = gid
        while stack:
            k = stack.pop()
            for j in g.index:
                if loc.at[j, "group"] < 0 and np.hypot(loc.at[k, "x"] - loc.at[j, "x"], loc.at[k, "y"] - loc.at[j, "y"]) <= 2.0:
                    loc.at[j, "group"] = gid
                    stack.append(j)
        gid += 1
for _, grp in loc.groupby("group"):
    best = grp.loc[grp["dev"].abs().idxmax()]
    tier = grp["tier"].map(rank).min()
    rows.append(dict(component=best["component"], element_type=etype.get(best["component"], ""),
                     defect_type="; ".join(dict.fromkeys(grp["defect_type"])),
                     x=round(float(grp["x"].mean()), 2), y=round(float(grp["y"].mean()), 2), z=round(float(grp["z"].mean()), 2),
                     area_m2=round(float(grp["area_m2"].sum()), 3), max_deviation_mm=float(best["dev"]),
                     stations=int(grp["stations"].max()), confidence={v: k for k, v in rank.items()}[tier],
                     evidence="; ".join(dict.fromkeys(grp["evidence"]))))

df = pd.DataFrame(rows)
df["_r"] = df["confidence"].map(rank)
df["_g"] = df["element_type"].str.contains("global")
df = df.sort_values(["_r", "_g"], ascending=[True, False]).drop(columns=["_r", "_g"]).reset_index(drop=True)
df.insert(0, "id", [f"D{i + 1:03d}" for i in range(len(df))])
df.to_csv(OUT_DIR / "defects.csv", index=False)
print(f"saved defects.csv: {len(df)} findings -- " + ", ".join(f"{k} {int((df['confidence'] == k).sum())}" for k in rank))
print(df[df["confidence"] != "low"][["id", "component", "defect_type", "x", "y", "z", "max_deviation_mm",
                                     "stations", "confidence"]].to_string(index=False))
