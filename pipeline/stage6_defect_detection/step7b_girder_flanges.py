"""
Step 7B, part 3b: girder bottom flanges, analysed on their own.

Pooled underside noise (~16 mm) mixes many different surfaces, the same
mixed-surface problem found on the road. Here each bottom flange (girder
components named *_bf, downward faces only) gets:
  1. its own noise, measured after the deck movement model (support line +
     sag) is subtracted -- the girders move with the deck;
  2. a local search on what's left: 0.25 m cells, threshold = 4x that
     flange's own noise, >= 2 stations, adjacent cells merged into patches;
  3. a per-metre loss check: of the points lying on the flange's EXPECTED
     underside (design + movement model), what fraction was rejected by the
     normal check or matched to some other component (web, neighbour, brace)?
     A bent or damaged flange could lose points this way;
  4. a re-rendered plan view of the residual, so local bumps aren't hidden in
     the sag colour.
"""
import json
import numpy as np
import pandas as pd
import trimesh
import yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
from matplotlib.ticker import MultipleLocator
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
from PIL import Image, ImageDraw
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402
VIEW_DIR = OUT_DIR / "inspection_views" / "girder"
VIEW_DIR.mkdir(parents=True, exist_ok=True)
cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
CELL, BIN_M, SCALE_MM, WRAP_M = 0.25, 1.0, 50, 15.0
MIN_ST = cfg["coverage"]["min_stations"]

d = np.load(OUT_DIR / "step7a_component_deviation.npz")
xyz, station, pc = d["xyz"], d["station"], d["point_component"]
nz = d["point_normal"][:, 2]
dz = d["point_dz_mm"]
is_under = d["is_horizontal"] & (nz < 0)
drp = np.load(OUT_DIR / "step7a_dropped_points.npz")
d_idx, d_comp = drp["dropped_idx"], drp["dropped_component"]
gm = np.load(OUT_DIR / "step7b_global_model.npz")
fc = np.load(OUT_DIR / "step7a_face_component.npz")["face_component"]
comp = pd.read_csv(BIM_COMPONENTS)
bim = trimesh.load(BIM_OBJ)


def movement(x):
    return (np.interp(x, gm["support_x"], gm["support_dz"])
            + np.interp(x, gm["sag_curve_x"], gm["sag_curve_y"], left=0.0, right=0.0))


def robust_std(v):
    return 1.4826 * np.median(np.abs(v - np.median(v)))


dropped_mask = np.zeros(len(xyz), bool)
dropped_mask[d_idx] = True
flanges = comp[(comp["element_type"] == "girder") & comp["component"].str.endswith("_bf")]
pooled = robust_std(d["point_signed_dev_mm"][is_under])
print(f"Pooled underside noise (all components): {pooled:.2f} mm\n")

summary, candidates, loss_rows, sheets = [], [], [], []
for ci, row in flanges.iterrows():
    name = row["component"]
    m = (pc == ci) & is_under
    x, y = xyz[m, 0], xyz[m, 1]
    resid = dz[m] - movement(x)            # + = higher than design + deck movement
    noise_raw, noise = robust_std(dz[m]), robust_std(resid)
    thr = 4 * noise

    # --- 2. local search on the residual ---
    cdf = pd.DataFrame({"ca": np.floor(x / CELL).astype(int), "cb": np.floor(y / CELL).astype(int),
                        "r": resid, "st": station[m], "x": x, "y": y})
    cells = cdf.groupby(["ca", "cb"]).agg(n=("r", "size"), med=("r", "median"), nst=("st", "nunique"),
                                          x=("x", "mean"), y=("y", "mean")).reset_index()
    hit = cells[(cells["n"] >= 5) & (cells["med"].abs() > thr) & (cells["nst"] >= MIN_ST)]
    keys = {(a, b): i for i, (a, b) in enumerate(zip(hit["ca"], hit["cb"]))}
    seen = set()
    for k0 in keys:
        if k0 in seen:
            continue
        stack, members = [k0], []
        seen.add(k0)
        while stack:
            k = stack.pop()
            members.append(keys[k])
            for da in (-1, 0, 1):
                for db in (-1, 0, 1):
                    nb = (k[0] + da, k[1] + db)
                    if nb in keys and nb not in seen:
                        seen.add(nb); stack.append(nb)
        p = hit.iloc[members]
        worst = p.loc[p["med"].abs().idxmax()]
        candidates.append(dict(component=name, element_type="girder", candidate_type="flange residual",
                               n_cells=len(p), area_m2=round(len(p) * CELL * CELL, 3),
                               x=round(float(p["x"].mean()), 2), y=round(float(p["y"].mean()), 2),
                               z=round(float(row["zmin"]), 2),
                               residual_mm=round(float(worst["med"]), 1),
                               direction="higher than deck movement" if worst["med"] > 0 else "lower than deck movement",
                               n_stations=int(p["nst"].max()), flange_noise_mm=round(noise, 2),
                               threshold_mm=round(thr, 1)))

    # --- 3. loss check along the flange, following the EXPECTED underside ---
    # window = the matching-margin cap, not a few mm: a flange bent further
    # than its own ~2 cm margin falls out of matching entirely, and those
    # lost points (sitting well above or below the expected surface) are
    # exactly what this check has to see
    band = cfg["component_matching"]["bbox_margin_m"]
    z_expect = row["zmin"] + movement(xyz[:, 0]) / 1000
    on = ((xyz[:, 0] >= row["xmin"]) & (xyz[:, 0] <= row["xmax"]) &
          (xyz[:, 1] >= row["ymin"]) & (xyz[:, 1] <= row["ymax"]) &
          (np.abs(xyz[:, 2] - z_expect) <= band))
    on_idx = np.where(on)[0]
    other = (pc[on_idx] >= 0) & (pc[on_idx] != ci)
    dropped_here = np.isin(on_idx, d_idx[d_comp == ci])
    dropped_other = dropped_mask[on_idx] & ~dropped_here
    unmatched = (pc[on_idx] < 0) & ~dropped_mask[on_idx]
    # points matched to OTHER components (mostly the web, which legitimately
    # stands on the flange) are reported but not counted as lost
    lost = dropped_here | dropped_other | unmatched
    h_mm = (xyz[on_idx, 2] - z_expect[on_idx]) * 1000      # + = above the expected underside
    ldf = pd.DataFrame({"bin": np.floor(xyz[on_idx, 0] / BIN_M).astype(int),
                        "kept": (pc[on_idx] == ci) & ~dropped_mask[on_idx], "lost": lost,
                        "dropped": dropped_here | dropped_other, "unmatched": unmatched, "other": other,
                        "h_lost": np.where(lost, h_mm, np.nan), "st_lost": np.where(lost, station[on_idx].astype(np.int64), -1)})
    bins = ldf.groupby("bin").agg(n_flange=("kept", "sum"), n_lost=("lost", "sum"),
                                  dropped=("dropped", "sum"), unmatched=("unmatched", "sum"),
                                  other=("other", "sum"), h_lost_mm=("h_lost", "median"),
                                  st_lost=("st_lost", lambda s: len(set(s) - {-1}))).reset_index()
    bins["n"] = bins["n_flange"] + bins["n_lost"]
    bins["lost_frac"] = (bins["n_lost"] / bins["n"].clip(lower=1)).round(3)
    bins["component"] = name
    med_loss = bins.loc[bins["n"] >= 30, "lost_frac"].median()
    spread = robust_std(bins.loc[bins["n"] >= 30, "lost_frac"].values)
    bins["flagged"] = (bins["n"] >= 30) & (bins["lost_frac"] > max(med_loss + 3 * spread, 2 * med_loss)) \
        & (bins["st_lost"] >= MIN_ST)
    loss_rows.append(bins)
    # consecutive flagged bins -> one "points lost" candidate, with the median
    # height of the lost points (how far the surface has moved from where the
    # flange should be)
    fb = bins[bins["flagged"]].sort_values("bin")
    if len(fb):
        run_id = np.concatenate([[0], np.cumsum(np.diff(fb["bin"].values) > 1)])
        for _, g in fb.groupby(run_id):
            lost_pts = ldf[ldf["lost"] & ldf["bin"].isin(g["bin"])]
            candidates.append(dict(component=name, element_type="girder",
                                   candidate_type="flange points lost (beyond matching margin)",
                                   n_cells=len(g), area_m2=round(len(g) * BIN_M * (row["ymax"] - row["ymin"]), 3),
                                   x=round(float((g["bin"].min() + g["bin"].max() + 1) / 2 * BIN_M), 2),
                                   y=round(float((row["ymin"] + row["ymax"]) / 2), 2), z=round(float(row["zmin"]), 2),
                                   residual_mm=round(float(np.nanmedian(lost_pts["h_lost"])), 1),
                                   direction="lost points sit above the expected underside"
                                   if np.nanmedian(lost_pts["h_lost"]) > 0 else "lost points sit below the expected underside",
                                   n_stations=int(lost_pts["st_lost"].nunique()), flange_noise_mm=round(noise, 2),
                                   threshold_mm=round(float(g["lost_frac"].max()), 3)))

    summary.append(dict(component=name, n_points=int(m.sum()), n_stations=int(len(np.unique(station[m]))),
                        noise_raw_mm=round(noise_raw, 2), noise_after_movement_mm=round(noise, 2),
                        threshold_mm=round(thr, 1), n_candidate_patches=int(sum(c["component"] == name for c in candidates)),
                        median_lost_frac_per_m=round(float(med_loss), 3),
                        n_flagged_bins=int(bins["flagged"].sum()),
                        matched_to_other_components=int(bins["other"].sum()),
                        lost_unmatched=int(bins["unmatched"].sum()), lost_dropped=int(bins["dropped"].sum())))

    # --- 4. residual plan view (same colour rules as the inspection views;
    # value expressed along the outward normal like the other views: for an
    # underside, red = in front = LOWER than deck movement, blue = higher) ---
    px = 0.05
    u0, u1, v0, v1 = row["xmin"] - 0.1, row["xmax"] + 0.1, row["ymin"] - 0.1, row["ymax"] + 0.1
    W, H = int(np.ceil((u1 - u0) / px)), int(np.ceil((v1 - v0) / px))
    mask = np.zeros((H, W), bool)
    faces = np.where((fc == ci) & (bim.face_normals[:, 2] < -0.7))[0]
    for f in faces:
        t = bim.triangles[f][:, :2]
        c0, c1 = max(int((t[:, 0].min() - u0) / px), 0), min(int((t[:, 0].max() - u0) / px) + 1, W)
        r0, r1 = max(int((t[:, 1].min() - v0) / px), 0), min(int((t[:, 1].max() - v0) / px) + 1, H)
        cc, rr = np.meshgrid(np.arange(c0, c1), np.arange(r0, r1))
        ctr = np.c_[u0 + (cc.ravel() + 0.5) * px, v0 + (rr.ravel() + 0.5) * px]
        ins = MplPath(t).contains_points(ctr, radius=px * 0.5)
        mask[rr.ravel()[ins], cc.ravel()[ins]] = True
    col = np.clip(((x - u0) / px).astype(int), 0, W - 1)
    rw = np.clip(((y - v0) / px).astype(int), 0, H - 1)
    g = pd.DataFrame({"r": rw, "c": col, "v": -resid}).groupby(["r", "c"])["v"].median()
    img = np.full((H, W), np.nan)
    img[g.index.get_level_values(0), g.index.get_level_values(1)] = g.values
    kept_img = np.zeros((H, W), int); np.add.at(kept_img, (rw, col), 1)
    dsel = d_idx[d_comp == ci]
    dcol = ((xyz[dsel, 0] - u0) / px).astype(int); drw = ((xyz[dsel, 1] - v0) / px).astype(int)
    ok = (dcol >= 0) & (dcol < W) & (drw >= 0) & (drw < H)
    drop_img = np.zeros((H, W), int); np.add.at(drop_img, (drw[ok], dcol[ok]), 1)
    has = ~np.isnan(img)
    cmap, norm = plt.get_cmap("RdBu_r"), Normalize(-SCALE_MM, SCALE_MM)
    rgb = np.tile([0.35, 0.35, 0.35], (H, W, 1))
    rgb[mask & ~has] = [0.82, 0.82, 0.82]
    rgb[has] = cmap(norm(img[has]))[:, :3]
    rgb[(drop_img > 0) & (drop_img >= kept_img)] = [1.0, 0.0, 1.0]
    n_rows = int(np.ceil((u1 - u0) / WRAP_M))
    row_len = (u1 - u0) / n_rows
    fig, axes = plt.subplots(n_rows, 1, figsize=(7, 0.9 * n_rows + 1.4), dpi=110, squeeze=False)
    for k, ax in enumerate(axes[:, 0]):
        ax.imshow(rgb, origin="lower", extent=[u0, u0 + W * px, v0, v0 + H * px], interpolation="nearest", aspect="equal")
        for c in candidates:
            if c["component"] == name:
                ax.add_patch(plt.Circle((c["x"], c["y"]), 0.35, fill=False, color="black", lw=1.2))
        ax.set_xlim(u0 + k * row_len, u0 + (k + 1) * row_len); ax.set_ylim(v0, v1)
        ax.xaxis.set_major_locator(MultipleLocator(1)); ax.yaxis.set_major_locator(MultipleLocator(1))
        ax.grid(which="both", color="black", alpha=0.25, linewidth=0.5); ax.tick_params(labelsize=6)
    axes[-1, 0].set_xlabel("x along bridge (m)", fontsize=8)
    axes[0, 0].set_title(f"{name} (girder) - underside, deck movement subtracted "
                         f"(flange noise {noise:.1f} mm, threshold {thr:.1f} mm)", fontsize=8)
    cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=list(axes[:, 0]), fraction=0.04, pad=0.02)
    cb.set_label("residual after deck movement (mm)\nblue = behind (higher), red = in front (lower)", fontsize=7)
    fig.text(0.01, 0.0, "white ~ 0 mm | light grey = no scan data | dark grey = no design surface | "
                        "magenta = rejected points | circles = candidate patches", fontsize=6.5, va="top")
    img_path = VIEW_DIR / f"{name}_residual.png"
    fig.savefig(img_path, bbox_inches="tight"); plt.close(fig)
    sheets.append(img_path)
    (VIEW_DIR / f"{name}_residual.json").write_text(json.dumps(
        {**summary[-1], "view": "underside plan, deck movement model subtracted",
         "value_convention": "along outward normal: red = lower than deck movement, blue = higher",
         "image": str(img_path.relative_to(OUT_DIR))}, indent=2))

sdf = pd.DataFrame(summary)
print("--- Girder bottom flanges: own noise, before and after removing deck movement ---")
print(sdf.to_string(index=False))

cand = pd.DataFrame(candidates)
cand.to_csv(OUT_DIR / "girder_flange_candidates.csv", index=False)
print(f"\n--- Flange candidates (residual > 4x own noise, >= {MIN_ST} stations): {len(cand)} ---")
print(cand.to_string(index=False) if len(cand) else "  (none)")

loss = pd.concat(loss_rows)
loss.to_csv(OUT_DIR / "girder_flange_loss_per_m.csv", index=False)
print("\n--- Flange 1 m bins where points are lost unusually often (dropped, matched elsewhere or unmatched) ---")
fl = loss[loss["flagged"]]
print(fl[["component", "bin", "n", "n_flange", "n_lost", "dropped", "unmatched", "other",
          "lost_frac", "h_lost_mm", "st_lost"]].to_string(index=False)
      if len(fl) else "  (none)")

# contact sheet of the five residual views
ims = [Image.open(p).convert("RGB") for p in sheets]
w = max(i.width for i in ims)
sheet = Image.new("RGB", (w, sum(i.height for i in ims) + 40), "white")
ImageDraw.Draw(sheet).text((10, 12), "girder bottom flanges: residual after deck movement (scale -50..+50 mm)", fill="black")
yy = 40
for i in ims:
    sheet.paste(i, (0, yy)); yy += i.height
sheet.save(OUT_DIR / "inspection_views" / "contact_sheet_girder_bottom_flanges_residual.png")
print("\nsaved girder_flange_candidates.csv, girder_flange_loss_per_m.csv, "
      "inspection_views/contact_sheet_girder_bottom_flanges_residual.png")
