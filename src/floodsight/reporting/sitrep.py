"""One-page situation report generator (English and Nepali).

Built ONLY from the results store with templates (works with no LLM and no
API key, AGENT.md 8.8). Every number in the output is verified against the
store by reporting/validator.py before anything is written; the generation
raises if an ungrounded number would appear.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import REPO_ROOT
from .results_store import ResultsStore
from .validator import validate_numbers_in_text

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class SitrepError(Exception):
    """Situation report generation failed (includes ungrounded numbers)."""


def _context_from_results(results: dict[str, Any]) -> dict[str, Any]:
    """Map the results store onto the template placeholders."""
    m = results["meta"]
    inputs = results["inputs"]
    b = results["damage"]["buildings"]
    r = results["damage"]["roads"]
    br = results["damage"]["bridges"]
    c = results["cutoff"]
    pre = inputs.get("sentinel1_pre") or {}
    post = inputs.get("sentinel1_post") or {}

    def _label(item: dict) -> str:
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            return name
        osm_id = item.get("osm_id")
        return f"bridge {osm_id}" if osm_id is not None else "unnamed bridge"

    bridges_list = ", ".join(
        _label(item) for item in br.get("affected_list", [])[:5]
    ) or "none detected"
    cutoff_names = ", ".join((c.get("likely_cut") or [])[:10]) or "none detected"
    bbox = m.get("bbox") or [0, 0, 0, 0]
    method = (results["flood"].get("confidence_summary") or {}).get("method")
    invalid_share = round(
        (1 - (results["flood"].get("confidence_summary") or {}).get(
            "valid_pixel_share", 0)) * 100
    ) if (results["flood"].get("confidence_summary") or {}).get(
        "valid_pixel_share") is not None else "unknown"

    return {
        "region_label": f"area lon {bbox[0]}..{bbox[2]}, lat {bbox[1]}..{bbox[3]}",
        "flood_date": m.get("flood_date"),
        "orbit": f"{pre.get('relative_orbit')} {pre.get('orbit_direction')}",
        "osm_snapshot_date": inputs.get("osm_snapshot_date"),
        "bbox_label": f"({bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]})",
        "flood_area_km2": results["flood"].get("area_km2_new_water"),
        "debris_area_km2": results["flood"].get("area_km2_probable_debris"),
        "buildings_likely": b.get("likely_hit"),
        "buildings_possible": b.get("possibly_hit"),
        "buildings_total": b.get("total_in_area"),
        "roads_km": r.get("total_length_km"),
        "bridges_affected": br.get("affected"),
        "bridges_total": br.get("total_in_area"),
        "bridges_list": bridges_list,
        "cutoff_likely": len(c.get("likely_cut") or []),
        "cutoff_possible": len(c.get("possibly_cut") or []),
        "cutoff_connected": c.get("still_connected_count"),
        "cutoff_already": len(c.get("already_isolated") or []),
        "cutoff_names": cutoff_names,
        "s1_pre": pre.get("name"),
        "s1_pre_date": str(pre.get("sensing_time", ""))[:10],
        "s1_post": post.get("name"),
        "s1_post_date": str(post.get("sensing_time", ""))[:10],
        "method": method,
        "invalid_share": invalid_share,
    }


def generate_sitrep(run_dir: Path, langs: tuple[str, ...] = ("en", "ne"),
                   validate: bool = True) -> dict[str, Path]:
    """Generate markdown + PDF sitreps for the run; returns written paths."""
    run_dir = Path(run_dir)
    results = ResultsStore(run_dir).read()
    context = _context_from_results(results)
    outputs: dict[str, Path] = {}
    for lang in langs:
        template_path = TEMPLATES_DIR / f"sitrep_{lang}.md.tmpl"
        if not template_path.is_file():
            raise SitrepError(f"no template for language {lang!r}")
        raw = template_path.read_text(encoding="utf-8")
        body_lines = []
        skipping_comments = True
        for ln in raw.splitlines():
            if skipping_comments and (ln.startswith("# ") or not ln.strip()):
                continue
            skipping_comments = False
            body_lines.append(ln)
        text = "\n".join(body_lines).format(**context)
        if validate:
            ok, ungrounded = validate_numbers_in_text(text, results)
            if not ok:
                raise SitrepError(
                    f"report for {lang!r} contains ungrounded numbers "
                    f"{ungrounded}; refusing to write (AGENT.md 2.1)"
                )
        md_path = run_dir / f"sitrep_{lang}.md"
        md_path.write_text(text, encoding="utf-8")
        outputs[lang] = md_path
        pdf_path = _render_pdf(text, run_dir / f"sitrep_{lang}.pdf", lang)
        outputs[f"{lang}_pdf"] = pdf_path
    return outputs


def _render_pdf(markdown_text: str, out_path: Path, lang: str) -> Path:
    """Render the markdown text to a one-page A4 PDF with fpdf2.

    Nepali uses Noto Sans Devanagari (installed via fonts-noto-core) and
    always carries the native-speaker review note from the template.
    """
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    # Unicode fonts everywhere: OSM feature names may be in Devanagari even
    # in the English report, so each primary font carries the other fallback.
    pdf.add_font("dejavu", style="", fname=(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    pdf.add_font("dejavu", style="B", fname=(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
    pdf.add_font("deva", style="", fname=(
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"))
    pdf.add_font("deva", style="B", fname=(
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"))
    if lang == "ne":
        family = "deva"
        pdf.set_fallback_fonts(["dejavu"])
    else:
        family = "dejavu"
        pdf.set_fallback_fonts(["deva"])
    for line in markdown_text.splitlines():
        line = line.rstrip()
        if not line:
            pdf.ln(2)
            continue
        if line.startswith("# "):
            pdf.set_font(family, style="B", size=15)
            pdf.multi_cell(w=0, h=6, text=line[2:], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        elif line.startswith("## "):
            pdf.set_font(family, style="B", size=11)
            pdf.multi_cell(w=0, h=5, text=line[3:], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        elif line.startswith("- "):
            pdf.set_font(family, style="", size=9)
            pdf.multi_cell(w=0, h=4.5, text="- " + line[2:], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        elif line.startswith("**") and line.endswith("**"):
            pdf.set_font(family, style="B", size=9)
            pdf.multi_cell(w=0, h=4.5, text=line.strip("*"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            pdf.set_font(family, style="", size=9)
            pdf.multi_cell(w=0, h=4.5, text=line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.output(str(out_path))
    return out_path


if __name__ == "__main__":
    import sys

    run_dir = sys.argv[1] if len(sys.argv) > 1 else str(
        max((REPO_ROOT / "runs").glob("*"), key=lambda p: p.stat().st_mtime)
    )
    paths = generate_sitrep(Path(run_dir))
    for key, path in paths.items():
        print(f"{key}: {path}")
