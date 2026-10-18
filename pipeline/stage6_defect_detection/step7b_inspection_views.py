"""
Step 7B, part 2: inspection views. One straight-on deviation map per assessed
component, oriented by its dominant surface:
  - horizontal, up-facing     -> plan view from above (x, y)
  - horizontal, down-facing   -> plan view of the underside (x, y)
  - vertical face             -> front view (along-face distance, height)
  - round column              -> unrolled (arc length around column, height)
Fixed diverging scale -50..+50 mm for every view, 1 m grid with labels,
grey = design surface with no scan data, white = no design surface there.
Each view also gets a JSON fact sheet and a pixel->points map, plus one
contact sheet per element type.
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
VIEW_DIR = OUT_DIR / "inspection_views"
SCALE_MM = 50
NO_DATA_GREY = np.array([0.82, 0.82, 0.82])      # design surface, no scan data
NO_SURFACE_GREY = np.array([0.35, 0.35, 0.35])   # no design surface here
REJECTED_MAGENTA = np.array([1.0, 0.0, 1.0])     # points rejected by the normal check
H_NZ, V_NZ = 0.7, 0.3  # same surface-type cut-offs as step 7A (config surface_types)
WRAP_M = 15.0          # max metres per row before a long view is wrapped
MAIN_MEMBER_M = 5.0    # contact sheets: parts at least this long go on the "main members" sheet

cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
CARRIED = set(cfg["movement_compensation"]["carried_by_deck"])

d = np.load(OUT_DIR / "step7a_component_deviation.npz")
xyz, station = d["xyz"], d["station"]
point_component, point_normal = d["point_component"], d["point_normal"]
dev = d["point_signed_dev_mm"]
# deck-carried members are shown as the residual after deck movement, so the
# global sag doesn't paint the whole view and hide local bumps
resid = d["point_resid_mm"]
is_horizontal, is_vertical = d["is_horizontal"], d["is_vertical"]
fc = np.load(OUT_DIR / "step7a_face_component.npz")
face_component, is_hidden_face = fc["face_component"], fc["is_hidden_face"]

bim = trimesh.load(BIM_OBJ)
tris, fnorm = bim.triangles, bim.face_normals
comp = pd.read_csv(BIM_COMPONENTS)
table = pd.read_csv(OUT_DIR / "step7a_component_table.csv").set_index("component")

nz = point_normal[:, 2]
cls = np.full(len(xyz), -1)
cls[is_horizontal & (nz > 0)] = 0   # top
cls[is_horizontal & (nz < 0)] = 1   # underside
cls[is_vertical] = 2                # vertical


def robust_std(v):
    return 1.4826 * np.median(np.abs(v - np.median(v)))


noise = {k: robust_std(dev[cls == c]) for k, c in [("top", 0), ("underside", 1), ("vertical", 2)]}
print("Surface-type noise (mm):", {k: round(v, 2) for k, v in noise.items()})

order = np.argsort(point_component, kind="stable")
starts = np.searchsorted(point_component[order], np.arange(len(comp) + 1))

drp = np.load(OUT_DIR / "step7a_dropped_points.npz")
dropped_idx, dropped_component, dropped_normal = drp["dropped_idx"], drp["dropped_component"], drp["dropped_normal"]
drop_order = np.argsort(dropped_component, kind="stable")
drop_starts = np.searchsorted(dropped_component[drop_order], np.arange(len(comp) + 1))


def points_of(ci):
    return order[starts[ci]:starts[ci + 1]]


def is_round(ci):
    fm = (face_component == ci) & (np.abs(fnorm[:, 2]) < V_NZ)
    if fm.sum() < 24:
        return False
    az = np.degrees(np.arctan2(fnorm[fm, 1], fnorm[fm, 0])) % 360
    return len(np.unique((az // 30).astype(int))) >= 9


def vertical_face(pidx):
    """Dominant vertical face of a component: the dominant horizontal axis of
    the normals (doubled-angle mean, so opposite faces of a plate don't
    cancel), then the side with more points -- one face, never front and back
    superimposed. Returns (points on that face, facing direction)."""
    vert = pidx[cls[pidx] == 2]
    if len(vert) == 0:
        return vert, None
    th = np.arctan2(point_normal[vert, 1], point_normal[vert, 0])
    a = 0.5 * np.arctan2(np.sin(2 * th).mean(), np.cos(2 * th).mean())
    axis = np.array([np.cos(a), np.sin(a), 0.0])
    side = np.sign(point_normal[vert, :2] @ axis[:2])
    facing = axis if (side > 0).sum() >= (side < 0).sum() else -axis
    return vert[point_normal[vert, :2] @ facing[:2] > 0.5], facing


def build_view(ci):
    """Pick the view for a component; return (view_type, kept point idx,
    u, v for points, design-face triangles in (u,v), axis info, project),
    where project(points, matched_normals) -> (in_view mask, u, v) puts any
    other point set (e.g. the rejected points) into the same view."""
    pidx = points_of(ci)
    faces = np.where((face_component == ci) & ~is_hidden_face)[0]
    row = comp.iloc[ci]

    if is_round(ci):
        cx, cy = (row.xmin + row.xmax) / 2, (row.ymin + row.ymax) / 2
        r = ((row.xmax - row.xmin) + (row.ymax - row.ymin)) / 4

        def project(p, nrm):
            sel = np.abs(nrm[:, 2]) < V_NZ
            return sel, (np.arctan2(p[:, 1] - cy, p[:, 0] - cx) % (2 * np.pi)) * r, p[:, 2]

        keep = pidx[cls[pidx] == 2]
        _, u, v = project(xyz[keep], point_normal[keep])
        fsel = faces[np.abs(fnorm[faces, 2]) < V_NZ]
        tri_uv = []
        for f in fsel:
            a = np.arctan2(tris[f][:, 1] - cy, tris[f][:, 0] - cx) % (2 * np.pi)
            if a.max() - a.min() > np.pi:
                a = np.where(a < np.pi, a + 2 * np.pi, a)
                tri_uv.append(np.c_[(a - 2 * np.pi) * r, tris[f][:, 2]])
            tri_uv.append(np.c_[a * r, tris[f][:, 2]])
        info = dict(view_type="round column (unrolled)",
                    u_axis="arc length around column, counter-clockwise from +x (m)",
                    v_axis="height z (m)", centre_xy=[float(cx), float(cy)], radius_m=float(r),
                    u_range=(0.0, 2 * np.pi * r))
        return "round", keep, u, v, tri_uv, info, project

    counts = [(cls[pidx] == c).sum() for c in (0, 1, 2)]
    c = int(np.argmax(counts))
    if c in (0, 1):
        sgn = 1 if c == 0 else -1

        def project(p, nrm):
            return sgn * nrm[:, 2] > H_NZ, p[:, 0], p[:, 1]

        keep = pidx[cls[pidx] == c]
        _, u, v = project(xyz[keep], point_normal[keep])
        fsel = faces[fnorm[faces, 2] > H_NZ] if c == 0 else faces[fnorm[faces, 2] < -H_NZ]
        tri_uv = [tris[f][:, :2] for f in fsel]
        if c == 0:
            info = dict(view_type="top (from above)", u_axis="x along bridge (m)", v_axis="y across bridge (m)")
        else:
            info = dict(view_type="underside (plan, looking down through the structure)",
                        u_axis="x along bridge (m)", v_axis="y across bridge (m)")
        return ("top" if c == 0 else "underside"), keep, u, v, tri_uv, info, project

    keep, facing = vertical_face(pidx)
    right = np.cross(-facing, [0.0, 0.0, 1.0])
    fsel = faces[(np.abs(fnorm[faces, 2]) < V_NZ) & (fnorm[faces, :2] @ facing[:2] > 0.5)]
    origin_u = min([(tris[f] @ right).min() for f in fsel] + ([(xyz[keep] @ right).min()] if len(keep) else []))

    def project(p, nrm):
        sel = (np.abs(nrm[:, 2]) < V_NZ) & (nrm[:, :2] @ facing[:2] > 0.5)
        return sel, p @ right - origin_u, p[:, 2]

    _, u, v = project(xyz[keep], point_normal[keep])
    tri_uv = [np.c_[tris[f] @ right - origin_u, tris[f][:, 2]] for f in fsel]
    info = dict(view_type="vertical face (front view)",
                u_axis="distance along the face, left to right as seen from the front (m)",
                v_axis="height z (m)", facing_direction=[round(float(x), 3) for x in facing],
                u_direction=[round(float(x), 3) for x in right], u_origin_offset_m=float(origin_u))
    return "vertical", keep, u, v, tri_uv, info, project


def render(ci, rec_out, overlays=None, out_path=None):
    """Render one component's view. With out_path, only an annotated copy is
    written (overlays = list of dicts u0, u1, v0, v1, colour, label drawn as
    rectangles in view coordinates) -- no map, fact sheet or record."""
    name = comp.iloc[ci]["component"]
    etype = comp.iloc[ci]["element_type"]
    vtype, keep, u, v, tri_uv, info, project = build_view(ci)
    if len(keep) == 0:
        return None
    is_resid = etype in CARRIED
    val = (resid if is_resid else dev)[keep]
    # rejected points (7A per-point normal check) on the same surface of this
    # component -- drawn in magenta, not hidden: rough or broken surfaces
    # scatter normals too
    didx = drop_order[drop_starts[ci]:drop_starts[ci + 1]]
    dsel, du, dv = project(xyz[dropped_idx[didx]], dropped_normal[didx])
    du, dv = du[dsel], dv[dsel]

    all_u = np.concatenate([u] + [t[:, 0] for t in tri_uv]) if tri_uv else u
    all_v = np.concatenate([v] + [t[:, 1] for t in tri_uv]) if tri_uv else v
    if "u_range" in info:
        u0, u1 = info.pop("u_range")
    else:
        u0, u1 = all_u.min() - 0.1, all_u.max() + 0.1
    v0, v1 = all_v.min() - 0.1, all_v.max() + 0.1
    # pixel size from the component's own point density (~8 points per
    # pixel), so pixels don't fall between scan lines and show fake gaps;
    # capped so the longest side stays under ~600 pixels
    pts_area = max((u.max() - u.min()) * (v.max() - v.min()), 1e-4)
    px = float(np.clip(np.sqrt(pts_area / len(keep) * 8), 0.03, 0.25))
    px = max(px, max(u1 - u0, v1 - v0) / 600)
    W, H = int(np.ceil((u1 - u0) / px)), int(np.ceil((v1 - v0) / px))

    # design-surface mask: rasterise this component's own (non-hidden) faces
    mask = np.zeros((H, W), bool)
    for t in tri_uv:
        c0 = max(int((t[:, 0].min() - u0) / px), 0); c1 = min(int((t[:, 0].max() - u0) / px) + 1, W)
        r0 = max(int((t[:, 1].min() - v0) / px), 0); r1 = min(int((t[:, 1].max() - v0) / px) + 1, H)
        if c1 <= c0 or r1 <= r0:
            continue
        cc, rr = np.meshgrid(np.arange(c0, c1), np.arange(r0, r1))
        centres = np.c_[u0 + (cc.ravel() + 0.5) * px, v0 + (rr.ravel() + 0.5) * px]
        inside = MplPath(t).contains_points(centres, radius=px * 0.5)
        mask[rr.ravel()[inside], cc.ravel()[inside]] = True

    col = np.clip(((u - u0) / px).astype(int), 0, W - 1)
    row = np.clip(((v - v0) / px).astype(int), 0, H - 1)
    df = pd.DataFrame({"r": row, "c": col, "dev": val, "st": station[keep]})
    g = df.groupby(["r", "c"]).agg(med=("dev", "median"), nst=("st", "nunique"))
    img_dev = np.full((H, W), np.nan)
    img_nst = np.zeros((H, W), int)
    rr_, cc_ = g.index.get_level_values(0).values, g.index.get_level_values(1).values
    img_dev[rr_, cc_] = g["med"].values
    img_nst[rr_, cc_] = g["nst"].values
    has_data = ~np.isnan(img_dev)

    img_kept = np.zeros((H, W), int)
    np.add.at(img_kept, (row, col), 1)
    img_drop = np.zeros((H, W), int)
    din = (du >= u0) & (du < u0 + W * px) & (dv >= v0) & (dv < v0 + H * px)
    np.add.at(img_drop, (((dv[din] - v0) / px).astype(int), ((du[din] - u0) / px).astype(int)), 1)
    rejected = (img_drop > 0) & (img_drop >= img_kept)

    # dark grey = no design surface, light grey = design surface but no scan
    # data, magenta = mostly rejected points, colour = deviation (white ~0 mm)
    cmap = plt.get_cmap("RdBu_r")
    norm = Normalize(-SCALE_MM, SCALE_MM)
    rgb = np.tile(NO_SURFACE_GREY, (H, W, 1))
    rgb[mask & ~has_data] = NO_DATA_GREY
    rgb[has_data] = cmap(norm(img_dev[has_data]))[:, :3]
    rgb[rejected] = REJECTED_MAGENTA

    span_u, span_v = u1 - u0, v1 - v0
    # long, thin views (e.g. a 60 m girder web) are wrapped into stacked
    # rows of at most WRAP_M along the horizontal axis, so they stay readable
    n_rows = int(np.ceil(span_u / WRAP_M)) if (span_u > WRAP_M and span_u / max(span_v, 1e-6) > 6) else 1
    row_len = span_u / n_rows
    fig_w = 7.0
    row_h = float(np.clip(fig_w * span_v / max(row_len, 1e-6), 0.6, 6.0))
    fig, axes = plt.subplots(n_rows, 1, figsize=(fig_w, row_h * n_rows + 1.2), dpi=110, squeeze=False)
    axes = axes[:, 0]
    for k, ax in enumerate(axes):
        ax.imshow(rgb, origin="lower", extent=[u0, u0 + W * px, v0, v0 + H * px],
                  interpolation="nearest", aspect="equal")
        ax.set_xlim(u0 + k * row_len, u0 + (k + 1) * row_len)
        ax.set_ylim(v0, v1)
        label_step = 1 if max(row_len, span_v) <= WRAP_M else 5
        for axis in (ax.xaxis, ax.yaxis):
            axis.set_major_locator(MultipleLocator(label_step))
            axis.set_minor_locator(MultipleLocator(1))
        ax.grid(which="both", color="black", alpha=0.25, linewidth=0.5)
        ax.tick_params(labelsize=7)
        ax.set_ylabel(info["v_axis"] if k == n_rows // 2 else "", fontsize=8)
    ax = axes[0]
    axes[-1].set_xlabel(info["u_axis"], fontsize=8)
    if vtype == "round":
        r_ = info["radius_m"]
        sec = ax.secondary_xaxis("top", functions=(lambda s: np.degrees(s / r_), lambda d_: np.radians(d_) * r_))
        sec.set_xlabel("angle (deg)", fontsize=7); sec.tick_params(labelsize=6)
    ax.set_title(f"{name} ({etype}) - {info['view_type']}"
                 + (" - deck movement removed" if is_resid else "")
                 + (f" [wrapped into {n_rows} rows]" if n_rows > 1 else ""), fontsize=9)
    for o in overlays or []:
        for a in axes:
            a.add_patch(plt.Rectangle((o["u0"], o["v0"]), o["u1"] - o["u0"], o["v1"] - o["v0"],
                                      fill=False, edgecolor=o["colour"], linewidth=1.6))
            a.text(o["u0"], o["v1"], o["label"], color=o["colour"], fontsize=6, va="bottom",
                   clip_on=True, fontweight="bold")
    cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=list(axes), fraction=0.04, pad=0.02)
    cb.set_label(("residual after deck movement (mm)" if is_resid else "deviation from design (mm)")
                 + "\nblue = behind, red = in front", fontsize=7)
    cb.ax.tick_params(labelsize=7)
    fig.text(0.01, 0.0, "white ~ 0 mm | light grey = no scan data | dark grey = no design surface | "
                        "magenta = points rejected by the normal check (rough/irregular)",
             fontsize=6.5, va="top")
    if out_path is not None:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, bbox_inches="tight")
        plt.close(fig)
        return None
    out_dir = VIEW_DIR / etype
    out_dir.mkdir(parents=True, exist_ok=True)
    img_path = out_dir / f"{name}.png"
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)

    # pixel->points map, with each point's exact view coordinates (u, v) so a
    # region named in grid metres can be mapped straight back to points;
    # rejected points are included too
    np.savez_compressed(out_dir / f"{name}_map.npz", point_idx=keep, row=row, col=col,
                        u=u.astype(np.float32), v=v.astype(np.float32),
                        dropped_point_idx=dropped_idx[didx][dsel],
                        dropped_u=du.astype(np.float32), dropped_v=dv.astype(np.float32),
                        u0=u0, v0=v0, pixel_size_m=px, shape=np.array([H, W]),
                        values_are_residual=is_resid)

    # noise = this component's own robust scatter in the view (not pooled
    # across surface types -- pooling mixes different surfaces)
    surf_noise = robust_std(val)
    thr = 4 * surf_noise
    beyond = has_data & (np.abs(np.nan_to_num(img_dev)) > thr)
    vals = val
    t7a = table.loc[name]
    sheet = {
        "component": name, "element_type": etype, "view_type": info["view_type"],
        "values": ("residual after deck movement (deck-carried member)" if is_resid
                   else "absolute deviation from design"),
        "axes": {k: val for k, val in info.items() if k != "view_type"},
        "pixel_size_m": round(px, 3),
        "grid_cell_convention": "1 m x 1 m squares of the drawn grid; cell 'u{a}_v{b}' covers "
                                "horizontal-axis values [a, a+1) m and vertical-axis values [b, b+1) m",
        "n_points_in_view": int(len(keep)),
        "n_points_component_total": int(len(points_of(ci))),
        "n_stations": int(len(np.unique(station[keep]))),
        "coverage_7a": t7a["coverage"], "sparse_mesh": bool(t7a["sparse_mesh"]),
        "median_dev_mm": round(float(np.median(vals)), 2),
        "max_dev_mm": round(float(vals.max()), 1), "min_dev_mm": round(float(vals.min()), 1),
        "p1_p99_dev_mm": [round(float(np.percentile(vals, 1)), 1), round(float(np.percentile(vals, 99)), 1)],
        "noise_mm": round(float(surf_noise), 2), "threshold_mm": round(float(thr), 1),
        "pooled_surface_type_noise_mm": round(float(noise["top" if vtype == "top" else "underside"
                                                          if vtype == "underside" else "vertical"]), 2),
        "area_beyond_threshold_m2": round(float(beyond.sum() * px * px), 3),
        "area_beyond_threshold_2plus_stations_m2": round(float((beyond & (img_nst >= 2)).sum() * px * px), 3),
        "design_surface_area_in_view_m2": round(float(mask.sum() * px * px), 3),
        "data_coverage_fraction": round(float((has_data & mask).sum() / max(mask.sum(), 1)), 3),
        "dropped_point_fraction_component": float(t7a["dropped_frac"]),
        "n_rejected_points_in_view": int(len(du)),
        "rejected_fraction_in_view": round(len(du) / (len(du) + len(keep)), 3),
        "rejected_area_m2": round(float(rejected.sum() * px * px), 3),
        "image": str(img_path.relative_to(OUT_DIR)),
        "pixel_map": str((out_dir / f"{name}_map.npz").relative_to(OUT_DIR)),
    }
    (out_dir / f"{name}.json").write_text(json.dumps(sheet, indent=2))
    row_ = comp.iloc[ci]
    longest = max(row_.xmax - row_.xmin, row_.ymax - row_.ymin, row_.zmax - row_.zmin)
    rec_out.append(dict(component=name, element_type=etype, n_points=len(keep), image=img_path,
                        main_member=bool(longest >= MAIN_MEMBER_M)))
    return sheet


assessed = [n for n in table.index if table.loc[n, "coverage"] == "assessed"]
name_to_ci = {n: i for i, n in enumerate(comp["component"])}
if __name__ == "__main__":
    records = []
    for k, name in enumerate(assessed):
        render(name_to_ci[name], records)
        if (k + 1) % 50 == 0:
            print(f"  rendered {k + 1}/{len(assessed)}")
    print(f"rendered {len(records)} inspection views -> {VIEW_DIR}")

    # --- wall tilt, LARGE planar faces only: the dominant vertical face of each
    # assessed (non-round) component, robust plane fit (fit, drop residuals beyond
    # 3x MAD, refit), kept only if both in-plane extents exceed 1 m. Lean = change
    # in the face's out-of-plumb angle vs the BIM face; mm at top = lean over the
    # face height (+ = top displaced towards the front / outward). Twist = rotation
    # about the vertical axis. ---
    MIN_FACE_M = 1.0
    tilt_rows = []
    for name in assessed:
        ci = name_to_ci[name]
        if is_round(ci):
            continue
        pts_i, facing = vertical_face(points_of(ci))
        if len(pts_i) < 100:
            continue
        P = xyz[pts_i]
        keep_fit = np.ones(len(P), bool)
        for _ in range(2):
            c0 = P[keep_fit].mean(axis=0)
            w, vecs = np.linalg.eigh(np.cov((P[keep_fit] - c0).T))
            n_fit = vecs[:, 0]
            res = (P - c0) @ n_fit
            keep_fit = np.abs(res - np.median(res)) <= 3 * max(robust_std(res), 1e-4)
        ext = [np.ptp((P[keep_fit] - c0) @ vecs[:, k]) for k in (1, 2)]
        if min(ext) <= MIN_FACE_M:
            continue
        n_nom = point_normal[pts_i].mean(axis=0)
        n_nom /= np.linalg.norm(n_nom)
        if n_fit @ n_nom < 0:
            n_fit = -n_fit
        lean = np.degrees(np.arcsin(np.clip(n_fit[2], -1, 1)) - np.arcsin(np.clip(n_nom[2], -1, 1)))
        twist = np.degrees(np.arctan2(n_fit[1], n_fit[0]) - np.arctan2(n_nom[1], n_nom[0]))
        twist = (twist + 180) % 360 - 180
        height = np.ptp(P[keep_fit, 2])
        tilt_rows.append(dict(component=name, element_type=comp.iloc[ci]["element_type"],
                              n_points=int(keep_fit.sum()), n_stations=int(len(np.unique(station[pts_i[keep_fit]]))),
                              face_width_m=round(float(max(ext)), 2), face_height_m=round(float(height), 2),
                              lean_deg=round(float(lean), 3), top_offset_mm=round(float(-height * np.tan(np.radians(lean)) * 1000), 1),
                              twist_deg=round(float(twist), 3),
                              fit_rms_mm=round(float(robust_std(res[keep_fit]) * 1000), 1)))
    tilt_cols = ["component", "element_type", "n_points", "n_stations", "face_width_m", "face_height_m",
                 "lean_deg", "top_offset_mm", "twist_deg", "fit_rms_mm"]
    tilt_df = pd.DataFrame(tilt_rows, columns=tilt_cols).sort_values("top_offset_mm", key=np.abs, ascending=False)
    tilt_df.to_csv(OUT_DIR / "step7b_wall_tilts_large.csv", index=False)
    print(f"\n--- Wall tilt, large planar faces only (both in-plane dimensions > {MIN_FACE_M} m) ---")
    print(tilt_df.to_string(index=False))
    print(f"saved step7b_wall_tilts_large.csv ({len(tilt_df)} faces)")

    # --- contact sheets, one per element type, biggest components first ---
    rec = pd.DataFrame(records)


    def contact_sheet(g, title, out, thumb_w, thumb_h, cols):
        g = g.sort_values("n_points", ascending=False)
        cols = min(len(g), cols)
        rows = int(np.ceil(len(g) / cols))
        header = 40
        sheet = Image.new("RGB", (cols * thumb_w, rows * thumb_h + header), "white")
        ImageDraw.Draw(sheet).text((10, 12), title, fill="black")
        for i, path in enumerate(g["image"]):
            im = Image.open(path).convert("RGB")
            im.thumbnail((thumb_w - 8, thumb_h - 8))
            x = (i % cols) * thumb_w + (thumb_w - im.width) // 2
            y = header + (i // cols) * thumb_h + (thumb_h - im.height) // 2
            sheet.paste(im, (x, y))
        sheet.save(out)
        print(f"saved {out.relative_to(OUT_DIR)} ({len(g)} views)")


    # one sheet per element type; types with many parts get the main members
    # (>= MAIN_MEMBER_M long) on their own sheet so they aren't buried
    for etype, g in rec.groupby("element_type"):
        main, small = g[g["main_member"]], g[~g["main_member"]]
        scale = "scale -50..+50 mm, largest first"
        if len(g) > 12 and len(main) and len(small):
            contact_sheet(main, f"{etype}: {len(main)} main members ({scale})",
                          VIEW_DIR / f"contact_sheet_{etype}.png", 1000, 760, 2)
            contact_sheet(small, f"{etype}: {len(small)} small parts ({scale})",
                          VIEW_DIR / f"contact_sheet_{etype}_small_parts.png", 560, 400,
                          max(3, int(np.ceil(np.sqrt(len(small))))))
        else:
            contact_sheet(g, f"{etype}: {len(g)} assessed components ({scale})",
                          VIEW_DIR / f"contact_sheet_{etype}.png", 560, 400,
                          max(3, int(np.ceil(np.sqrt(len(g))))))
