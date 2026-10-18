"""
Where the pipeline reads its inputs and writes its outputs. run_pipeline.py
sets these through environment variables; run on their own, the steps fall
back to the repository's data/ and outputs/ folders.

  LASER_BIM    BIM mesh (.obj); its component table is <stem>_components.csv next to it
  LASER_SCANS  scan tiles (.laz), separated by os.pathsep
  LASER_OUT    output folder
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
OUT_DIR = Path(os.environ.get("LASER_OUT", ROOT / "outputs"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
BIM_OBJ = Path(os.environ.get("LASER_BIM", ROOT / "data" / "bim" / "bridge_BIM.obj"))
BIM_COMPONENTS = BIM_OBJ.with_name(BIM_OBJ.stem + "_components.csv")
SCANS = [Path(p) for p in os.environ.get("LASER_SCANS", "").split(os.pathsep) if p] or [
    ROOT / "data" / "scan" / "bridge_scan_west.laz", ROOT / "data" / "scan" / "bridge_scan_east.laz"]


def tile_name(path):
    """Orientation metadata from the tile file name: 'west' / 'east' if present."""
    stem = Path(path).stem.lower()
    for side in ("west", "east"):
        if side in stem:
            return side
    return stem
