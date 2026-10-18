"""
Step 10: report.md, written by an LLM from the verified findings only.

The model gets a facts brief built from this run's outputs (inputs, orientation
decision, defects.csv, global findings, wall tilts, the list of images it may
use, and method_notes.md next to this script) and writes the whole report. It never decides
what is a defect, and the answer key is never sent: if a scorecard exists it is
appended verbatim after the model's text, labelled as computed.

Before the report is saved, code checks it: every number must come from the
facts brief, every image must be one of the listed ones, and the required
sections must be there. On failure the model gets one retry with the errors; if
that fails too, the step fails (the rejected draft is saved for inspection).
Costs are capped per run (config report_llm.max_cost_usd); responses are cached
and every request/response is logged to <out>/logs/report_llm.jsonl.

    python src/step10_report.py                 # normal use (run_pipeline.py calls this)
    python src/step10_report.py --validate F    # check a draft F offline, no API call
"""
import hashlib
import json
import os
import re
import sys
import time

import numpy as np
import pandas as pd
import yaml
from pathlib import Path

from paths import OUT_DIR, BIM_OBJ, SCANS, ROOT  # noqa: E402

cfg = yaml.safe_load((ROOT / "config.yaml").read_text())["report_llm"]
o = OUT_DIR
LOG_DIR = o / "logs"
CACHE_DIR = LOG_DIR / "report_llm_cache"
for p in (LOG_DIR, CACHE_DIR):
    p.mkdir(parents=True, exist_ok=True)

REQUIRED = ["What the system does", "Pipeline", "Findings", "Global movement", "Key images",
            "Lessons learned", "Limitations and next steps", "Reproduce"]
NUM = re.compile(r"(?<![\w.\-/])-?\d+(?:\.\d+)?(?![\w.])|(?<=[\-/])\d+(?:\.\d+)?(?![\w.])")


# ------------------------------------------------------------------ facts brief
def rel(p):
    return os.path.relpath(p, o).replace("\\", "/")


def build_facts():
    defects = pd.read_csv(o / "defects.csv")
    gf = json.loads((o / "step7b_global_findings.json").read_text())
    orient = json.loads((o / "orientation_check.json").read_text())
    s1 = np.load(o / "step1_loaded.npz")
    table = pd.read_csv(o / "step7a_component_table.csv")
    tilts = pd.read_csv(o / "step7b_wall_tilts_large.csv")
    n_bridge = int(np.load(o / "step3_connected_bridge.npz")["is_bridge"].sum())

    images = [(o / "step1_topdown.png", "BIM design model (left) and raw laser scan (right), top-down"),
              (o / "step3_connected_angled.png", "the extracted structure (ground, vegetation and vehicles removed)"),
              (o / "heatmap_damage_size.png", "3D deviation heatmap: distance from each scan point to the design"),
              (o / "heatmap_damage_size_topdown.png", "deviation heatmap, top-down"),
              (o / "step7b_global_movement.png", "deck profile along the bridge with the measured deck heights at "
                                                  "the supports; shaded bands = stations disagree more than usual")]
    for r in defects[defects["confidence"].isin(["high", "medium"])].itertuples():
        if "global" in str(r.element_type):
            continue
        resid = o / "inspection_views" / r.element_type / f"{r.component}_residual.png"
        view = resid if resid.exists() else o / "inspection_views" / r.element_type / f"{r.component}.png"
        images.append((view, f"inspection view of {r.component} for finding {r.id}: straight-on, -50..+50 mm, blue = "
                             "behind design, red = in front, white about 0, light grey = no scan data, dark grey = no "
                             "design surface, magenta = points rejected as irregular"
                             + ("; deck movement removed" if view == resid else "")))
    images = [(p, c) for p, c in images if p.exists()]
    sheets = sorted((o / "inspection_views").glob("contact_sheet_*.png"))

    low = defects[defects["confidence"] == "low"]

    def kind(t):
        t = str(t)
        if "sharp BIM edge" in t and "deviation" not in t:
            return "rough/irregular surface at a sharp BIM edge"
        if "flange end / fitting" in t:
            return "points rejected at a flange end or fitting"
        if "wall tilt" in t:
            return "small wall tilt"
        return "small local deviation"

    lines = ["# FACTS BRIEF", "",
             "## Inputs",
             f"- BIM file: {BIM_OBJ.name}; scan tiles: {', '.join(s.name for s in SCANS)}",
             f"- scan points: {len(s1['xyz'])}; scanner stations: {len(np.unique(s1['station']))}; "
             f"points kept as the structure: {n_bridge}",
             f"- BIM components: {len(pd.read_csv(BIM_OBJ.with_name(BIM_OBJ.stem + '_components.csv')))} in the model; "
             f"{len(table)} received scan points; {int((table['coverage'] == 'assessed').sum())} assessed (enough data)",
             "", "## Orientation check",
             f"- chosen: {orient['chosen']}; decided by: {orient['decided_by']}",
             f"- fit of the two candidates (median distance to BIM): as aligned "
             f"{orient['candidates']['as_aligned']['median_mm']} mm, rotated 180 degrees "
             f"{orient['candidates']['rotated_180']['median_mm']} mm; tied: {orient['tied']}",
             "", "## Findings (defects.csv, verified by measurement)",
             f"- total {len(defects)}: high {int((defects['confidence'] == 'high').sum())}, medium "
             f"{int((defects['confidence'] == 'medium').sum())}, low {len(low)}",
             "- low-confidence items by type: " + "; ".join(f"{k}: {v}" for k, v in low["defect_type"].map(kind).value_counts().items()),
             "- coordinates: x along the bridge, y across, z height, metres; max_deviation_mm is signed",
             "", defects.drop(columns=["evidence"]).to_csv(index=False),
             "## Global findings",
             json.dumps({"sag_per_span": gf["sag_per_span"],
                         "support_movement": {k: v for k, v in gf["support_movement"].items() if k != "evidence"},
                         "support_readings": gf["support_movement"]["evidence"]["supports"],
                         "end_span_slopes_mm_per_m": gf["support_movement"]["evidence"]["end_span_slopes"]}, indent=1),
             "", "## Large planar faces: plane-fit tilt", tilts.to_csv(index=False),
             "## Images you may use (path relative to the report: what it shows)"]
    lines += [f"- {rel(p)}: {c}" for p, c in images]
    lines += [f"- {rel(s)}: contact sheet, all {s.stem.replace('contact_sheet_', '')} components at a glance (link only)"
              for s in sheets]
    lines += ["", "## How to run", "python run_pipeline.py --bim <bim.obj> --scan <tile1.laz> <tile2.laz> --out <folder>",
              "", (Path(__file__).with_name("method_notes.md")).read_text(encoding="utf-8")]
    allowed_images = {rel(p) for p, _ in images} | {rel(s) for s in sheets}
    return "\n".join(lines), allowed_images


INSTRUCTIONS = """You are writing the final inspection report for a bridge, in Markdown, for an engineer and a
non-specialist manager. Write it ONLY from the facts brief below.

Rules:
- Use only facts from the brief. Every number you write must appear in the brief (you may round it). Do not
  compute new numbers, do not add causes, findings or recommendations that are not in the brief.
- Keep each finding's confidence level exactly as given. Report support movement with its caveat wording.
- Plain English, short paragraphs, no hype.
- Use these sections as level-2 headings, in this order: What the system does; Pipeline; Findings; Global
  movement; Key images; Lessons learned; Limitations and next steps; Reproduce.
- Pipeline: include a mermaid flowchart (```mermaid) of the steps.
- Findings: a table of the high and medium confidence findings (id, component, finding, location, size,
  stations, confidence), then the low-confidence items as counts by type.
- Images: use only paths from the "Images you may use" list, as HTML: <img src="PATH" alt="..." width="800">,
  each with a one-line caption saying what it shows. Contact sheets as Markdown links only.
- Do not write a scorecard or compare against any answer key; that is added separately.
- Reproduce: the run command in a code block.
Output the report Markdown only, starting with a level-1 title."""


# ------------------------------------------------------------------ validation
def numbers(text):
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)                    # 7,876,846 -> 7876846
    text = re.sub(r"```.*?```", " ", text, flags=re.S)                 # code blocks (diagram, command)
    text = re.sub(r"<img[^>]*>", " ", text)                           # image tags
    text = re.sub(r"\]\([^)]*\)", "]", text)                          # link targets
    return [m.group(0) for m in NUM.finditer(text)]


def allowed_values(facts):
    vals = set()
    for s in numbers(facts) + re.findall(r"\d+(?:\.\d+)?", facts):
        v = float(s)
        vals.update({v, abs(v)})
    return vals


def validate(report, facts, allowed_images):
    errs = []
    for h in REQUIRED:
        if not re.search(rf"^#{{2,3}}\s+{re.escape(h)}\b", report, flags=re.M | re.I):
            errs.append(f"missing section heading: '## {h}'")
    if "```mermaid" not in report:
        errs.append("the Pipeline section needs a ```mermaid flowchart")
    srcs = re.findall(r'<img[^>]*src="([^"]+)"', report) + re.findall(r"!\[[^\]]*\]\(([^)]+)\)", report)
    errs += [f"image not in the allowed list: {s}" for s in srcs if s not in allowed_images]
    errs += [f"linked file not in the allowed list: {s}" for s in re.findall(r"\]\(([^)]+\.png)\)", report)
             if s not in allowed_images]
    vals = allowed_values(facts)
    bad = []
    for s in numbers(report):
        v = float(s)
        if v.is_integer() and 0 <= abs(v) <= 20:
            continue                                                  # list numbers, small counts
        dec = len(s.split(".")[1]) if "." in s else 0
        tol = 0.5 * 10 ** (-dec) + 1e-9
        if not any(abs(abs(v) - a) <= tol for a in vals):
            bad.append(s)
    if bad:
        errs.append("numbers not found in the facts brief (do not compute or invent numbers): "
                    + ", ".join(sorted(set(bad), key=bad.index)[:30]))
    return errs


# ------------------------------------------------------------------ LLM call
def api_key():
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("OPENROUTER_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("step 10 needs an LLM: set OPENROUTER_API_KEY (environment or .env)")


def call(messages):
    from openai import OpenAI
    key = hashlib.sha256((cfg["model"] + json.dumps(messages)).encode()).hexdigest()
    cache = CACHE_DIR / f"{key}.json"
    if cache.exists():
        c = json.loads(cache.read_text())
        return c["text"], 0.0, True
    client = OpenAI(base_url=cfg["base_url"], api_key=api_key())
    try:
        r = client.chat.completions.create(model=cfg["model"], temperature=cfg["temperature"],
                                           max_tokens=cfg["max_tokens"], messages=messages,
                                           extra_body={"usage": {"include": True}})
    except Exception as e:      # no credit, bad key, network, provider error
        sys.exit(f"the report LLM call failed ({type(e).__name__}): {str(e)[:300]}\n"
                 "defects.csv and all measurement outputs are complete; fix the API issue and rerun this step only.")
    text = r.choices[0].message.content or ""
    usage = r.usage.model_dump() if r.usage else {}
    cache.write_text(json.dumps({"text": text, "usage": usage}))
    with (LOG_DIR / "report_llm.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": time.time(), "model": cfg["model"], "messages": messages,
                            "response": text, "usage": usage}) + "\n")
    return text, float(usage.get("cost") or 0), False


def main():
    facts, allowed_images = build_facts()
    if "--validate" in sys.argv:
        draft = open(sys.argv[sys.argv.index("--validate") + 1], encoding="utf-8").read()
        errs = validate(draft, facts, allowed_images)
        print("\n".join(errs) if errs else "draft passes all checks")
        sys.exit(1 if errs else 0)

    messages = [{"role": "user", "content": INSTRUCTIONS + "\n\n" + facts}]
    spent, report, errs = 0.0, None, []
    for attempt in range(1, cfg["max_attempts"] + 1):
        if spent >= cfg["max_cost_usd"]:
            sys.exit(f"report LLM cost cap reached (${spent:.2f} of ${cfg['max_cost_usd']:.2f})")
        text, cost, cached = call(messages)
        spent += cost
        text = re.sub(r"^```(?:markdown)?\s*|\s*```\s*$", "", text.strip())
        errs = validate(text, facts, allowed_images)
        print(f"attempt {attempt}: {'cached, ' if cached else ''}cost ${cost:.3f}, "
              f"{'passes all checks' if not errs else f'{len(errs)} problem(s)'}")
        if not errs:
            report = text
            break
        for e in errs:
            print("   - " + e)
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Your report failed these automatic checks. Fix them and output the "
                                                 "full corrected report only:\n- " + "\n- ".join(errs)}]
    if report is None:
        (o / "report_rejected.md").write_text(text, encoding="utf-8")
        sys.exit(f"the LLM report failed validation twice -- draft saved to {o / 'report_rejected.md'}")

    footer = [f"\n\n---\n*Report text written by `{cfg['model']}` from this run's verified findings; every number "
              "and image in it was checked automatically against the source data. Defects are found by measurement, "
              "not by the AI.*"]
    sc = o / "scorecard.md"
    if sc.exists():
        body = sc.read_text(encoding="utf-8").split("\n", 1)[1]
        footer += ["\n\n## Scorecard (computed against the answer key; not written by the AI)\n",
                   body.replace("\n## ", "\n### ")]
    (o / "report.md").write_text(report + "".join(footer) + "\n", encoding="utf-8")
    print(f"saved {o / 'report.md'} (LLM cost this run: ${spent:.3f})")


if __name__ == "__main__":
    main()
