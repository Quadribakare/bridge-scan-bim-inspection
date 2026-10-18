"""
Step 7A, parts 2-4: assign every point to its real named BIM component by
FOOTPRINT (candidate components whose bounding box actually contains the
point, not just "nearest triangle anywhere"), measure deviation along that
specific component's own surface normal, classify surface type, and
measure noise separately per type.
"""
import numpy as np
import pandas as pd
import trimesh
import open3d as o3d
import yaml
from pathlib import Path
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402
cfg = yaml.safe_load((ROOT / "config.yaml").read_text())

realigned = np.load(OUT_DIR / "step7a_realigned.npz")
xyz = realigned["xyz_aligned"]
n = len(xyz)
print(f"Points: {n:,}")

# station: recover from the same mask chain as step3 (order-preserving)
s2 = np.load(OUT_DIR / "step2_cleaned.npz")
is_bridge = np.load(OUT_DIR / "step3_connected_bridge.npz")["is_bridge"]
station = s2["station"][is_bridge]
assert len(station) == n

comp = pd.read_csv(BIM_COMPONENTS)
bim = trimesh.load(BIM_OBJ)
centroids = bim.triangles_center
normals_all = bim.face_normals

max_margin = cfg["component_matching"]["bbox_margin_m"]
thickness_frac = cfg["component_matching"]["thickness_fraction"]
n_comp = len(comp)

# margin SCALED to each component's own thickness (its smallest bbox
# dimension), capped at max_margin -- a thin diagonal brace gets a tiny
# margin, a big flat deck gets the full margin. One fixed value for every
# component (regardless of size) was letting points from adjacent thin
# members cross-contaminate each other.
raw_dims = comp[["xmax", "ymax", "zmax"]].values - comp[["xmin", "ymin", "zmin"]].values
thickness = raw_dims.min(axis=1)
per_comp_margin = np.minimum(max_margin, thickness_frac * thickness)
print(f"Per-component margin: min={per_comp_margin.min():.3f}m, max={per_comp_margin.max():.3f}m, "
      f"median={np.median(per_comp_margin):.3f}m (capped at {max_margin}m)")

mins = comp[["xmin", "ymin", "zmin"]].values - per_comp_margin[:, None]
maxs = comp[["xmax", "ymax", "zmax"]].values + per_comp_margin[:, None]

# assign each FACE to its component (nearest bbox centre among containing
# boxes) -- used to give each component its own face subset
bbox_centres = (mins + maxs) / 2
face_component = np.full(len(centroids), -1, dtype=np.int64)
for i, c in enumerate(centroids):
    inside = np.where(np.all((c >= mins) & (c <= maxs), axis=1))[0]
    if len(inside) == 1:
        face_component[i] = inside[0]
    elif len(inside) > 1:
        d = np.linalg.norm(bbox_centres[inside] - c, axis=1)
        face_component[i] = inside[np.argmin(d)]
unresolved = face_component == -1
if unresolved.sum():
    d = np.linalg.norm(bbox_centres[None, :, :] - centroids[unresolved, None, :], axis=2)
    face_component[unresolved] = np.argmin(d, axis=1)
print(f"Faces assigned to components: {len(centroids):,}, unresolved fallback used for {unresolved.sum()}")

# --- hidden-face exclusion: a BIM face physically covered by another
# component (e.g. deck_slab's top sitting right under surfacing) can never
# receive real scan points on itself, no matter how many faces model it --
# a face-count/density rule punishes legitimately flat, well-modelled
# surfaces instead of the actual problem. Detect coverage directly: cast a
# ray outward from each face's own centroid along its own normal and see if
# another component's geometry is immediately in the way. ---
full_t = o3d.t.geometry.TriangleMesh.from_legacy(
    o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(bim.vertices),
                               o3d.utility.Vector3iVector(bim.faces)))
full_scene = o3d.t.geometry.RaycastingScene()
full_scene.add_triangles(full_t)

eps = cfg["hidden_face_detection"]["raycast_eps_m"]
origins = centroids + normals_all * eps
rays = o3d.core.Tensor(np.concatenate([origins, normals_all], axis=1).astype(np.float32))
hit = full_scene.cast_rays(rays)
t_hit = hit["t_hit"].numpy()
hit_prim = hit["primitive_ids"].numpy().astype(np.int64)

valid_hit = np.isfinite(t_hit)
hit_component = np.full(len(centroids), -1, dtype=np.int64)
hit_component[valid_hit] = face_component[hit_prim[valid_hit]]
own_margin = per_comp_margin[face_component]
# "covered" = something belonging to a DIFFERENT component sits within this
# face's own component's own margin distance outward -- reuses the already
# thickness-derived margin rather than inventing a new tuned constant
is_hidden_face = valid_hit & (hit_component != face_component) & (t_hit < own_margin)
print(f"Hidden faces (covered by another component): {is_hidden_face.sum():,} / {len(centroids):,}")

# NOTE: tried an additional per-face "oversized triangle" candidacy/match
# exclusion here (flagging faces far bigger than the mesh's own median area).
# Reverted: most of this bridge's real structure (girder webs, columns,
# walls) is legitimately modelled with just a couple of huge flat triangles
# -- a flat plane needs no subdivision -- so a size-based rule caught 78% of
# all points, not just the genuinely-coarse components. Face size alone
# can't tell "big and correctly flat" apart from "big and hiding real
# complexity". See CHANGES.md for the component-level fix that replaced it.

point_component = np.full(n, -1, dtype=np.int64)
point_normal = np.zeros((n, 3))
point_signed_dev_mm = np.full(n, np.nan)

# --- movement compensation for components carried by the deck. A thin member
# (e.g. a 4 cm girder flange) gets a matching margin of only ~2 cm, so where
# the deck sags more than that, its points fall outside its own matching box
# and are silently lost -- the few that remain are a biased leftover. Matching
# these components against design + the deck movement model (from 7B part 1,
# if it exists) keeps them inside their boxes. The absolute deviation is still
# what gets stored; the residual relative to deck movement is stored too. ---
carried_comp = comp["element_type"].isin(cfg["movement_compensation"]["carried_by_deck"]).values
gm_path = OUT_DIR / "step7b_global_model.npz"
if gm_path.exists():
    gmod = np.load(gm_path)
    move_mm = (np.interp(xyz[:, 0], gmod["support_x"], gmod["support_dz"])
               + np.interp(xyz[:, 0], gmod["sag_curve_x"], gmod["sag_curve_y"], left=0.0, right=0.0))
    print(f"Movement compensation for deck-carried components: deck movement model from 7B part 1 "
          f"({move_mm.min():.1f} to {move_mm.max():.1f} mm)")
else:
    move_mm = np.zeros(n)
    print("Movement compensation: no deck movement model yet (first run) -- matching against design only")
xyz_comp = xyz.copy()
xyz_comp[:, 2] -= move_mm / 1000
point_move_mm = np.zeros(n)

h_thresh = cfg["surface_types"]["horizontal_normal_z"]
v_thresh = cfg["surface_types"]["vertical_normal_z"]

print("\nAssigning points to components: collecting ALL candidate-component "
      "distances first, true argmin at the end (no sequential overwrite) ...")
# per-point best-so-far, built from a full candidate pass per component --
# but resolved as a genuine global minimum, not an order-dependent overwrite
best_dist_mm = np.full(n, np.inf)

for ci in range(n_comp):
    xlo, ylo, zlo = mins[ci]
    xhi, yhi, zhi = maxs[ci]
    P = xyz_comp if carried_comp[ci] else xyz
    cand = ((P[:, 0] >= xlo) & (P[:, 0] <= xhi) &
            (P[:, 1] >= ylo) & (P[:, 1] <= yhi) &
            (P[:, 2] >= zlo) & (P[:, 2] <= zhi))
    if cand.sum() == 0:
        continue
    face_mask = (face_component == ci) & ~is_hidden_face
    if face_mask.sum() == 0:
        continue
    sub_mesh = bim.submesh([np.where(face_mask)[0]], append=True)
    sub_t = o3d.t.geometry.TriangleMesh.from_legacy(
        o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(sub_mesh.vertices),
                                   o3d.utility.Vector3iVector(sub_mesh.faces)))
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(sub_t)
    query = o3d.core.Tensor(P[cand].astype(np.float32))
    ans = scene.compute_closest_points(query)
    closest = ans["points"].numpy().astype(np.float64)
    local_normals = ans["primitive_normals"].numpy().astype(np.float64)
    dist_mm = np.linalg.norm(P[cand] - closest, axis=1) * 1000

    cand_idx = np.where(cand)[0]
    # this is purely a running minimum (same mathematical result as a true
    # argmin over all candidates per point, since every candidate component
    # for a point gets exactly one evaluation here) -- not an early-accept
    is_new_best = dist_mm < best_dist_mm[cand_idx]
    win_idx = cand_idx[is_new_best]
    win_local = np.where(is_new_best)[0]
    if len(win_idx) == 0:
        continue
    diff = P[win_idx] - closest[win_local]
    sign = np.sign(np.einsum("ij,ij->i", diff, local_normals[win_local]))
    sign[sign == 0] = 1
    point_signed_dev_mm[win_idx] = sign * dist_mm[win_local]
    point_normal[win_idx] = local_normals[win_local]
    point_component[win_idx] = ci
    point_move_mm[win_idx] = move_mm[win_idx] if carried_comp[ci] else 0.0
    best_dist_mm[win_idx] = dist_mm[win_local]

# what was measured above is the residual relative to deck movement (for
# deck-carried components); the absolute deviation from design adds the
# movement back along the face normal
point_resid_mm = point_signed_dev_mm.copy()
point_signed_dev_mm = point_resid_mm + point_move_mm * point_normal[:, 2]

assigned = point_component >= 0
print(f"\nPoints assigned to a real named component: {assigned.sum():,} / {n:,} "
      f"({assigned.sum()/n*100:.1f}%)")

# --- per-point normal consistency check: does the matched BIM face normal
# agree with what the SCAN data itself looks like locally? A column/box seen
# from multiple sides legitimately has points with different matched normals
# -- averaging them per component (the old check) incorrectly flagged that
# whole legitimate situation as "inconsistent". The real, general check is
# per POINT: estimate each point's own local surface normal from its scan
# neighbours (independent of which BIM face it got matched to), and drop
# only the points where that disagrees badly with the matched face -- those
# are the ones that likely latched onto the wrong face. ---
print("\nEstimating scan-side normals for per-point match consistency check...")
scan_pcd = o3d.geometry.PointCloud()
scan_pcd.points = o3d.utility.Vector3dVector(xyz)
scan_pcd.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(knn=cfg["component_matching"]["scan_normal_knn"]))
scan_normal = np.asarray(scan_pcd.normals)

max_disagree = cfg["component_matching"]["max_point_normal_disagree_deg"]
cos_ang = np.abs(np.einsum("ij,ij->i", scan_normal[assigned], point_normal[assigned]))
angle_deg = np.degrees(np.arccos(np.clip(cos_ang, -1, 1)))
bad_match = np.zeros(n, dtype=bool)
bad_match[assigned] = angle_deg > max_disagree
print(f"Dropping {bad_match.sum():,} / {assigned.sum():,} assigned points "
      f"({bad_match.sum()/max(assigned.sum(),1)*100:.1f}%) whose local scan "
      f"normal disagrees >{max_disagree} deg from their matched BIM face "
      f"(a wrong-face match) -- dropping the point, not the component")

# keep a record of the dropped points (component, matched normal, deviation,
# angle): rough or broken surfaces also have scattered normals, so a dense
# patch of dropped points is itself a damage candidate, not just noise
angle_full = np.full(n, np.nan)
angle_full[assigned] = angle_deg
dropped_idx = np.where(bad_match)[0]
np.savez_compressed(OUT_DIR / "step7a_dropped_points.npz",
                    dropped_idx=dropped_idx, dropped_component=point_component[dropped_idx],
                    dropped_normal=point_normal[dropped_idx],
                    dropped_dev_mm=point_signed_dev_mm[dropped_idx],
                    dropped_angle_deg=angle_full[dropped_idx])
n_dropped_by_comp = np.bincount(point_component[dropped_idx], minlength=len(comp))

point_component[bad_match] = -1
point_normal[bad_match] = 0.0
point_signed_dev_mm[bad_match] = np.nan
point_resid_mm[bad_match] = np.nan

assigned = point_component >= 0
print(f"Points assigned after per-point normal filtering: {assigned.sum():,} / {n:,} "
      f"({assigned.sum()/n*100:.1f}%)")

# vertical dz for horizontal surfaces specifically
nz = point_normal[:, 2]
is_horizontal = assigned & (np.abs(nz) > h_thresh)
is_vertical = assigned & (np.abs(nz) < v_thresh)
is_other = assigned & ~is_horizontal & ~is_vertical

point_dz_mm = np.full(n, np.nan)
point_dz_mm[is_horizontal] = point_signed_dev_mm[is_horizontal] * np.sign(nz[is_horizontal])

print(f"Horizontal: {is_horizontal.sum():,}, Vertical: {is_vertical.sum():,}, Other/sloped: {is_other.sum():,}")

np.savez_compressed(
    OUT_DIR / "step7a_component_deviation.npz",
    xyz=xyz, station=station, point_component=point_component,
    point_normal=point_normal, point_signed_dev_mm=point_signed_dev_mm,
    point_dz_mm=point_dz_mm, is_horizontal=is_horizontal, is_vertical=is_vertical,
    point_resid_mm=point_resid_mm, point_move_mm=point_move_mm,
)
print("saved step7a_component_deviation.npz")
np.savez_compressed(OUT_DIR / "step7a_face_component.npz",
                    face_component=face_component, is_hidden_face=is_hidden_face)
print("saved step7a_face_component.npz")

# --- surface-type noise (robust MAD) ---
print("\n--- Noise per surface type (MAD-based robust std) ---")
noise_by_type = {}
is_underside = is_horizontal & (nz < 0)
is_top = is_horizontal & (nz > 0)
for name, mask in [("top (horizontal, up)", is_top), ("underside (horizontal, down)", is_underside),
                    ("vertical", is_vertical)]:
    vals = point_signed_dev_mm[mask]
    if len(vals) > 50:
        med = np.median(vals)
        mad = np.median(np.abs(vals - med))
        std = 1.4826 * mad
        noise_by_type[name] = std
        print(f"  {name}: n={mask.sum():,}, noise={std:.2f}mm")
    else:
        print(f"  {name}: n={mask.sum()} -- too few points")

# --- per-component hidden-face count, informational only (being hidden is a
# property of the FACE and is already handled at matching time above; a
# component can legitimately have zero hidden faces, or some hidden and some
# exposed -- this is not a disqualifier) ---
n_faces_by_comp = np.array([(face_component == ci).sum() for ci in range(n_comp)])
n_hidden_by_comp = np.array([((face_component == ci) & is_hidden_face).sum() for ci in range(n_comp)])

# --- mesh-density flag, INFORMATIONAL ONLY here (does not gate "assessed" --
# see below for why). Faces per m^2 of the component's own footprint vs the
# dataset's own median: components modelled as a handful of huge flat
# triangles (deck_slab, edge_upstand, surfacing, ballast walls, bank seats,
# crossheads) all read as "sparse" by this measure, because the dataset's
# median is pulled way up by hundreds of small, intricate parts (crossframes,
# stiffeners). Checked directly: deck_slab, edge_upstand, surfacing_L/R,
# pier_1_crosshead, abutment ballast_wall/bank_seat are ALL well below 10% of
# the median density -- so this signal alone can't tell "genuinely too
# coarse to trust" apart from "legitimately large and flat", the same
# ambiguity the per-face-size and self-referential-outlier attempts hit.
# What DOES distinguish them in practice: candidates.csv's own per-CELL,
# per-station mechanism already only flags the ones that are actually
# locally broken (edge_upstand, deck_slab, ballast_wall, bank_seat, surfacing
# all show up; crosshead/columns do not). So sparse-mesh components still
# get a real number in THIS table (their median is usually fine -- it's only
# specific patches that go wrong), but are excluded from the finer-grained
# local-defect CANDIDATE search in step 7B, where mesh coarseness can
# masquerade as a defect. See step7b_local_defects.py.
footprint_area = (comp["xmax"] - comp["xmin"]).values * np.sqrt(
    (comp["ymax"] - comp["ymin"]).values ** 2 + (comp["zmax"] - comp["zmin"]).values ** 2
).clip(min=1e-6)
face_density = n_faces_by_comp / footprint_area
median_density = np.median(face_density[n_faces_by_comp > 0])
sparse_mesh_floor = median_density * 0.1
is_sparse_mesh = face_density < sparse_mesh_floor
print(f"\nMedian face density: {median_density:.2f} faces/m^2 (sparse-mesh floor: "
      f"{sparse_mesh_floor:.2f}) -- {is_sparse_mesh.sum()} / {n_comp} components below it "
      f"(informational; excluded from local-defect candidates in 7B, not from this table)")

# --- coverage rule (scan side) ---
ccfg = cfg["coverage"]

# --- per-component table ---
print("\n--- Per-component table ---")
rows = []
for ci in range(n_comp):
    m = point_component == ci
    name = comp.iloc[ci]["component"]
    etype = comp.iloc[ci]["element_type"]
    n_drop = int(n_dropped_by_comp[ci])
    if m.sum() == 0 and n_drop == 0:
        continue
    vals = point_signed_dev_mm[m]
    stns = len(np.unique(station[m]))
    npts = int(m.sum())

    med = np.median(vals) if npts else np.nan

    reasons = []
    if npts < ccfg["min_points"]:
        reasons.append("too few points")
    if stns < ccfg["min_stations"]:
        reasons.append("too few stations")
    coverage = "insufficient coverage - not assessed: " + ", ".join(reasons) if reasons else "assessed"

    rows.append({
        "component": name, "element_type": etype,
        "n_points": npts, "n_stations": stns,
        "n_faces": int(n_faces_by_comp[ci]), "n_hidden_faces": int(n_hidden_by_comp[ci]),
        "sparse_mesh": bool(is_sparse_mesh[ci]),
        "n_dropped_normal": n_drop,
        "dropped_frac": round(n_drop / (npts + n_drop), 3),
        "median_dev_mm": round(float(med), 2),
        "max_abs_dev_mm": round(float(np.max(np.abs(vals))), 2) if npts else np.nan,
        "coverage": coverage,
    })
table = pd.DataFrame(rows).sort_values("component")
table.to_csv(OUT_DIR / "step7a_component_table.csv", index=False)
print(table.to_string(index=False))

print("\n--- Points dropped by the per-point normal check, by element type ---")
by_type = table.groupby("element_type")[["n_points", "n_dropped_normal"]].sum()
by_type["dropped_frac"] = (by_type["n_dropped_normal"] /
                           (by_type["n_points"] + by_type["n_dropped_normal"])).round(3)
print(by_type.to_string())
print("\n--- Components with the highest dropped fraction (>= 200 points) ---")
big = table[(table["n_points"] + table["n_dropped_normal"]) >= 200]
print(big.sort_values("dropped_frac", ascending=False)
      [["component", "element_type", "n_points", "n_dropped_normal", "dropped_frac"]]
      .head(20).to_string(index=False))
n_assessed = (table["coverage"] == "assessed").sum()
print(f"\nsaved step7a_component_table.csv ({len(table)} components total, "
      f"{n_assessed} assessed, {len(table)-n_assessed} marked insufficient coverage)")

print("\n--- Summary by element type ---")
summary = table.groupby("element_type").agg(
    n_components=("component", "count"),
    n_assessed=("coverage", lambda s: (s == "assessed").sum()),
).reset_index()
summary["n_insufficient"] = summary["n_components"] - summary["n_assessed"]
print(summary.to_string(index=False))
