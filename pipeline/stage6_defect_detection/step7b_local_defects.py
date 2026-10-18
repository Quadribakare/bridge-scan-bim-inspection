"""
Step 7B, part 3: local defects. Subtract the global movement model (support
line + in-span curve) from deck-top points, then do real local-defect
detection: 0.25m cells, median per cell, threshold = 4x that surface type's
own noise, >=2 stations must agree. Rough/irregular areas are evidence, not
filtered out (no normal-consistency exclusion here -- that's exactly what
real damage looks like). Also plane-fit every wall/vertical face and report
tilt vs the BIM's nominal orientation.
"""
import numpy as np
import pandas as pd
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402
cfg = yaml.safe_load((ROOT / "config.yaml").read_text())

d2 = np.load(OUT_DIR / "step7a_component_deviation.npz")
xyz = d2["xyz"]
station = d2["station"]
point_component = d2["point_component"]
point_normal = d2["point_normal"]
point_signed_dev_mm = d2["point_signed_dev_mm"]
point_dz_mm = d2["point_dz_mm"]
is_horizontal = d2["is_horizontal"]
is_vertical = d2["is_vertical"]

comp = pd.read_csv(BIM_COMPONENTS)


# --- surface-type noise, recomputed here for clarity ---
nz = point_normal[:, 2]
is_top = is_horizontal & (nz > 0)
is_underside = is_horizontal & (nz < 0)


def robust_std(vals):
    med = np.median(vals)
    return 1.4826 * np.median(np.abs(vals - med))


# --- residual: 7A already matched deck-carried components against design +
# deck movement, so point_resid_mm is the deviation left after the global
# movement for those (and the plain deviation for supports, which don't move
# with the deck). Local search and noise both work on this residual, so the
# sag can't inflate the noise or hide in the cells. ---
residual_mm = d2["point_resid_mm"].copy()

noise = {}
for name, mask in [("top", is_top), ("underside", is_underside), ("vertical", is_vertical)]:
    vals = residual_mm[mask]
    noise[name] = robust_std(vals) if len(vals) > 50 else np.nan
print("Surface-type noise on the residual (mm):", {k: round(v, 2) for k, v in noise.items()})

print(f"\nResidual stats (top only, after deck movement removed): "
      f"median={np.median(residual_mm[is_top]):.2f}mm, "
      f"p90={np.percentile(np.abs(residual_mm[is_top]),90):.2f}mm")

# --- exclude components 7A marked "not assessed" (too few points/stations)
# OR "sparse_mesh" (few large triangles for their footprint -- deck_slab,
# edge_upstand, surfacing, ballast walls, bank seats: still get a real
# median in 7A's table, but mesh coarseness can snap a whole LOCAL PATCH of
# points to the wrong part of a big triangle, producing a consistent,
# multi-station-agreeing but entirely fake "candidate" at the finer 0.25m
# cell granularity this pass works at). Deliberately NOT a separate
# normal-consistency filter here, since real damage can also look locally
# irregular -- that's the whole point of this pass, per instructions. ---
a7_table = pd.read_csv(OUT_DIR / "step7a_component_table.csv")
bad_names = set(a7_table[a7_table["coverage"].astype(str).str.startswith("insufficient coverage")
                          | a7_table["sparse_mesh"].astype(bool)]["component"])
bad_resolution_idx = set(comp[comp["component"].isin(bad_names)].index)
print(f"Excluding {len(bad_resolution_idx)} components (not-assessed or sparse-mesh) "
      f"from the local-defect search (data/matching problem, not evidence)")

# --- 0.25m cell analysis, NO normal-consistency filter (irregular = evidence) ---
CELL = 0.25
assigned = (point_component >= 0) & ~np.isin(point_component, list(bad_resolution_idx))


def surface_type_of(mask_idx):
    if is_top[mask_idx]:
        return "top"
    if is_underside[mask_idx]:
        return "underside"
    if is_vertical[mask_idx]:
        return "vertical"
    return "other"


all_cells = []
for surf_name, surf_mask in [("top", is_top), ("underside", is_underside), ("vertical", is_vertical)]:
    m = surf_mask & assigned
    if m.sum() == 0:
        continue
    pts = xyz[m]
    vals = residual_mm[m]
    comps = point_component[m]
    stns = station[m]
    if surf_name == "vertical":
        ca = np.floor(pts[:, 0] / CELL).astype(int)
        cb = np.floor(pts[:, 2] / CELL).astype(int)
    else:
        ca = np.floor(pts[:, 0] / CELL).astype(int)
        cb = np.floor(pts[:, 1] / CELL).astype(int)
    df = pd.DataFrame({"ca": ca, "cb": cb, "comp": comps, "val": vals, "x": pts[:, 0],
                        "y": pts[:, 1], "z": pts[:, 2], "station": stns})
    grouped = df.groupby(["comp", "ca", "cb"])
    thresh = 4 * noise.get(surf_name, 10.0) if not np.isnan(noise.get(surf_name, np.nan)) else 20.0
    for key, g in grouped:
        if len(g) < 5:
            continue
        med = g["val"].median()
        nst = g["station"].nunique()
        if abs(med) > thresh and nst >= cfg["coverage"]["min_stations"]:
            all_cells.append({
                "surface_type": surf_name, "component_idx": key[0],
                "component": comp.iloc[key[0]]["component"] if key[0] >= 0 else "unknown",
                "n": len(g), "n_stations": nst, "median_mm": med,
                "x": g["x"].mean(), "y": g["y"].mean(), "z": g["z"].mean(),
            })

print(f"\nFlagged cells (local defect candidates): {len(all_cells)}")
cells_df = pd.DataFrame(all_cells)
if len(cells_df):
    print(cells_df.groupby("surface_type").size())

# --- merge adjacent flagged cells (same component) into candidate defects ---
candidates = []
if len(cells_df):
    for (surf, ci), g in cells_df.groupby(["surface_type", "component_idx"]):
        if len(g) == 0:
            continue
        area = len(g) * CELL * CELL
        candidates.append({
            "component": g["component"].iloc[0], "surface_type": surf,
            "n_cells": len(g), "area_m2": round(area, 3),
            "x": round(g["x"].mean(), 2), "y": round(g["y"].mean(), 2), "z": round(g["z"].mean(), 2),
            "max_abs_dev_mm": round(g["median_mm"].abs().max(), 1),
            "n_stations": int(g["n_stations"].max()),
            "candidate_type": "deviation",
        })

# --- rough-surface candidates: dense patches of points that 7A's per-point
# normal check rejected. A broken or rough surface also scatters normals, so
# these are evidence, not noise. Per component, 3D voxels where most points
# were rejected (by >=2 stations) are merged into connected patches. Long thin
# patches are marked edge-like: at a sharp edge the scan normal mixes two
# faces, which is the most likely artefact. ---
rcfg = cfg["rough_surface"]
VOX = rcfg["voxel_m"]
drp = np.load(OUT_DIR / "step7a_dropped_points.npz")
d_idx, d_comp, d_dev = drp["dropped_idx"], drp["dropped_component"], drp["dropped_dev_mm"]
not_assessed_idx = set(comp[comp["component"].isin(set(
    a7_table[a7_table["coverage"].astype(str).str.startswith("insufficient coverage")]["component"]))].index)
sparse_names = set(a7_table[a7_table["sparse_mesh"].astype(bool)]["component"])

kept = point_component >= 0
kv = np.floor(xyz[kept] / VOX).astype(np.int64)
kept_counts = pd.DataFrame({"ci": point_component[kept], "vx": kv[:, 0], "vy": kv[:, 1], "vz": kv[:, 2]}) \
    .groupby(["ci", "vx", "vy", "vz"]).size().rename("n_kept")
dv = np.floor(xyz[d_idx] / VOX).astype(np.int64)
vox = pd.DataFrame({"ci": d_comp, "vx": dv[:, 0], "vy": dv[:, 1], "vz": dv[:, 2],
                    "st": station[d_idx], "absdev": np.abs(d_dev),
                    "x": xyz[d_idx, 0], "y": xyz[d_idx, 1], "z": xyz[d_idx, 2]}) \
    .groupby(["ci", "vx", "vy", "vz"]).agg(n_drop=("st", "size"), n_st=("st", "nunique"),
                                            absdev=("absdev", "median"),
                                            x=("x", "mean"), y=("y", "mean"), z=("z", "mean"))
vox = vox.join(kept_counts, how="left").fillna({"n_kept": 0})
vox["total"] = vox["n_drop"] + vox["n_kept"]
vox["frac"] = vox["n_drop"] / vox["total"]
flag = ((vox["total"] >= cfg["coverage"]["min_points"]) & (vox["frac"] >= rcfg["min_dropped_fraction"])
        & (vox["n_st"] >= cfg["coverage"]["min_stations"]))
flagged = vox[flag].reset_index()
flagged = flagged[~flagged["ci"].isin(not_assessed_idx)]
print(f"\nRough-surface voxels (>= {rcfg['min_dropped_fraction']:.0%} of points rejected by the "
      f"normal check, >= {cfg['coverage']['min_stations']} stations): {len(flagged)}")

# sharp BIM edges: mesh edges where adjacent faces meet at > 45 deg, plus
# open (boundary) edges; sampled every 2 cm into a KD-tree
import trimesh
from scipy.spatial import cKDTree
bim = trimesh.load(BIM_OBJ)
crease = bim.face_adjacency_edges[bim.face_adjacency_angles > np.radians(45)]
edge_count = pd.Series(map(tuple, np.sort(bim.edges, axis=1))).value_counts()
boundary = np.array([e for e, k in edge_count.items() if k == 1], dtype=np.int64).reshape(-1, 2)
segs = bim.vertices[np.vstack([crease, boundary])]
samples = [a + (b - a) * t[:, None] for a, b in segs
           for t in [np.linspace(0, 1, max(2, int(np.linalg.norm(b - a) / 0.02) + 1))]]
edge_tree = cKDTree(np.vstack(samples))
print(f"Sharp BIM edges: {len(crease)} creases + {len(boundary)} open edges")

n_rough = 0
for ci, g in flagged.groupby("ci"):
    keys = {tuple(k): i for i, k in enumerate(g[["vx", "vy", "vz"]].values)}
    seen = set()
    for start in keys:
        if start in seen:
            continue
        stack, members = [start], []
        seen.add(start)
        while stack:
            k = stack.pop()
            members.append(keys[k])
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        nb = (k[0] + dx, k[1] + dy, k[2] + dz)
                        if nb in keys and nb not in seen:
                            seen.add(nb)
                            stack.append(nb)
        p = g.iloc[members]
        centres = p[["vx", "vy", "vz"]].values * VOX
        if len(p) >= 3:
            ev = np.sort(np.linalg.eigvalsh(np.cov(centres.T)))[::-1]
            elong = float(np.sqrt(ev[0] / max(ev[1], VOX ** 2 / 12)))
        else:
            elong = 1.0
        name = comp.iloc[ci]["component"]
        w = p["n_drop"].values
        edge_dist = float(np.median(edge_tree.query(p[["x", "y", "z"]].values)[0]))
        candidates.append({
            "component": name, "surface_type": "rough (normals rejected)",
            "n_cells": len(p), "area_m2": round(len(p) * VOX * VOX, 3),
            "x": round(float(np.average(p["x"], weights=w)), 2),
            "y": round(float(np.average(p["y"], weights=w)), 2),
            "z": round(float(np.average(p["z"], weights=w)), 2),
            "max_abs_dev_mm": round(float(p["absdev"].max()), 1),
            "n_stations": int(p["n_st"].max()),
            "candidate_type": "rough surface",
            "dropped_frac": round(float(p["n_drop"].sum() / p["total"].sum()), 2),
            "elongation": round(elong, 1),
            "edge_dist_m": round(edge_dist, 3),
            "edge_like": bool(elong > rcfg["edge_elongation"] or edge_dist < rcfg["edge_distance_m"]),
            "sparse_mesh": name in sparse_names,
        })
        n_rough += 1
print(f"Rough-surface patches: {n_rough}")

cand_df = pd.DataFrame(candidates) if candidates else pd.DataFrame()
if len(cand_df):
    # thin-member flag: reuses the SAME thickness concept already
    # established for component matching (bbox_margin_m, 0.15m) rather than
    # a new tuned number or a hardcoded name list -- a component thinner
    # than that margin cap is exactly what this project already treats as
    # "thin" everywhere else.
    raw_dims = comp[["xmax", "ymax", "zmax"]].values - comp[["xmin", "ymin", "zmin"]].values
    thickness_by_comp = raw_dims.min(axis=1)
    thin_cap = cfg["component_matching"]["bbox_margin_m"]
    comp_name_to_thickness = dict(zip(comp["component"], thickness_by_comp))
    comp_name_to_etype = dict(zip(comp["component"], comp["element_type"]))
    cand_df["element_type"] = cand_df["component"].map(comp_name_to_etype)
    cand_df["thin_member"] = cand_df["component"].map(comp_name_to_thickness) <= thin_cap
    # thin members sort below everything else; within each group, worst first
    cand_df = cand_df.sort_values(["thin_member", "max_abs_dev_mm"], ascending=[True, False])
cand_df.to_csv(OUT_DIR / "candidates.csv", index=False)
print(f"\nsaved candidates.csv ({len(cand_df)} candidate defects, thin members ranked "
      f"below major components)")
if len(cand_df):
    not_thin = cand_df[~cand_df["thin_member"] & (cand_df["candidate_type"] == "deviation")]
    print(f"\n--- Top deviation candidates on MAJOR components (not thin members) ---")
    print(not_thin.head(15).to_string(index=False) if len(not_thin) else "  (none)")

    show = ["component", "candidate_type", "surface_type", "area_m2", "x", "y", "z",
            "max_abs_dev_mm", "n_stations", "thin_member", "dropped_frac", "edge_dist_m", "edge_like"]
    rough = cand_df[cand_df["candidate_type"] == "rough surface"].sort_values(
        ["thin_member", "edge_like", "area_m2"], ascending=[True, True, False])
    print(f"\n--- Rough-surface candidates: {len(rough)} patches "
          f"({int((~rough['edge_like'].astype(bool)).sum())} not edge-like), major components first ---")
    print(rough[show].head(20).to_string(index=False) if len(rough) else "  (none)")

    print(f"\n--- Top 5 candidates per element type (deviation, then rough surface) ---")
    for etype in ["pier", "abutment", "girder", "deck", "surfacing", "parapet", "crossframe"]:
        dev_sub = cand_df[(cand_df["element_type"] == etype) & (cand_df["candidate_type"] == "deviation")].head(5)
        rough_sub = rough[rough["element_type"] == etype].head(5)
        sub = pd.concat([dev_sub, rough_sub])
        if len(sub) == 0:
            print(f"\n  {etype}: no candidates")
            continue
        print(f"\n  {etype}:")
        print("  " + sub[show].to_string(index=False).replace("\n", "\n  "))

# --- plane-fit every vertical wall/face, report tilt vs BIM nominal ---
print("\n--- Plane-fit tilt per vertical component ---")
tilt_rows = []
for ci in comp.index:
    if ci in bad_resolution_idx:
        continue
    m = (point_component == ci) & is_vertical
    if m.sum() < 30:
        continue
    pts = xyz[m]
    centroid = pts.mean(axis=0)
    cov = np.cov((pts - centroid).T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    # eigh returns ascending order: eigvals[0] <= eigvals[1] <= eigvals[2]
    flatness_ratio = eigvals[0] / max(eigvals[1], 1e-12)
    if flatness_ratio > cfg["plane_fit"]["max_flatness_ratio"]:
        # point cloud isn't clearly plate-shaped (more line-like/elongated than
        # planar) -- the smallest-eigenvalue direction isn't a trustworthy
        # surface normal here, so don't report a fake tilt number
        continue
    fitted_normal = eigvecs[:, 0]  # smallest eigenvalue direction = plane normal
    nominal_normal = point_normal[m].mean(axis=0)
    nominal_normal /= max(np.linalg.norm(nominal_normal), 1e-9)
    cos_angle = np.clip(abs(np.dot(fitted_normal, nominal_normal)), -1, 1)
    tilt_deg = np.degrees(np.arccos(cos_angle))
    # a large disagreement between the PCA-fitted plane and the BIM's own
    # matched-face normal is evidence the points landed on the wrong face of
    # their own component (a matching problem), not real physical tilt
    reliable = tilt_deg <= cfg["plane_fit"]["max_tilt_disagreement_deg"]
    tilt_rows.append({"component": comp.iloc[ci]["component"], "n_points": int(m.sum()),
                       "flatness_ratio": round(flatness_ratio, 3), "tilt_deg": round(tilt_deg, 3),
                       "reliable": reliable})

tilt_df = pd.DataFrame(tilt_rows).sort_values("tilt_deg", ascending=False)
tilt_df.to_csv(OUT_DIR / "step7b_wall_tilts.csv", index=False)
n_unreliable = int((~tilt_df["reliable"]).sum()) if len(tilt_df) else 0
print(tilt_df[tilt_df["reliable"]].head(15).to_string(index=False))
print(f"\nsaved step7b_wall_tilts.csv ({len(tilt_df)} walls/faces checked; "
      f"{n_unreliable} marked unreliable -- fitted plane disagrees >"
      f"{cfg['plane_fit']['max_tilt_disagreement_deg']}deg from BIM nominal, "
      f"a face-matching mismatch rather than real tilt)")
