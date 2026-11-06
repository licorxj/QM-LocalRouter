#!/usr/bin/env python3
"""Generate all LocalRouter icon SVG variants from one shared glyph.

Run from this directory:  python generate_svgs.py
Outputs localrouter-icon-{a,b,c}.svg and their -compact counterparts.

Glyph: one inbound request hits a routing node and is dispatched to three
upstreams; the straight middle path is the preferred route.
"""
from pathlib import Path

# Shared glyph geometry on a 1024 grid.
BAR = "M196 512 H836"
BAR_CHEVRON = "M784 460 L838 512 L784 564"
CURVE_UP = "M572 512 C676 512 646 336 806 336"
CURVE_DOWN = "M572 512 C676 512 646 688 806 688"
CHEVRON_UP = "M780 290 L830 336 L780 382"
CHEVRON_DOWN = "M780 642 L830 688 L780 734"
NODE = 'M512 512 m0 0'  # node drawn as <circle> below

NODE_R = 62
MAIN_W = 58
SIDE_W = 50


def glyph_group(side_color: str, side_op: float, main_color: str,
                node_fill: str, side_w: int = SIDE_W) -> str:
    s = f'stroke="{side_color}" stroke-opacity="{side_op}" stroke-width="{side_w}"'
    m = f'stroke="{main_color}" stroke-width="{MAIN_W}"'
    return f"""  <g fill="none" stroke-linecap="round" stroke-linejoin="round">
    <path d="{CURVE_UP}" {s}/>
    <path d="{CURVE_DOWN}" {s}/>
    <path d="{CHEVRON_UP}" {s}/>
    <path d="{CHEVRON_DOWN}" {s}/>
    <path d="{BAR}" {m}/>
    <path d="{BAR_CHEVRON}" {m}/>
    <circle cx="512" cy="512" r="{NODE_R}" fill="{node_fill}"/>
  </g>"""


VARIANTS = {
    "a": {
        "title": "青蓝徽章",
        "defs": """  <defs>
    <linearGradient id="aBg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#4ECFFB"/>
      <stop offset="0.42" stop-color="#14A7EA"/>
      <stop offset="1" stop-color="#0768B5"/>
    </linearGradient>
    <radialGradient id="aGlow" cx="0.20" cy="0.14" r="0.72">
      <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.30"/>
      <stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="aVin" cx="0.86" cy="0.96" r="0.82">
      <stop offset="0" stop-color="#053057" stop-opacity="0.38"/>
      <stop offset="1" stop-color="#053057" stop-opacity="0"/>
    </radialGradient>
  </defs>""",
        "bg": """  <rect width="1024" height="1024" rx="232" fill="url(#aBg)"/>
  <rect width="1024" height="1024" rx="232" fill="url(#aGlow)"/>
  <rect width="1024" height="1024" rx="232" fill="url(#aVin)"/>""",
        "glyph": glyph_group("#FFFFFF", 0.68, "#FFFFFF", "#FFFFFF"),
        "compact_glyph": glyph_group("#FFFFFF", 0.78, "#FFFFFF", "#FFFFFF", side_w=54),
    },
    "b": {
        "title": "深空交换机",
        "defs": """  <defs>
    <linearGradient id="bBg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1A3A66"/>
      <stop offset="0.55" stop-color="#10233F"/>
      <stop offset="1" stop-color="#0A1526"/>
    </linearGradient>
    <radialGradient id="bGlow" cx="0.5" cy="0.5" r="0.62">
      <stop offset="0" stop-color="#27C0F5" stop-opacity="0.35"/>
      <stop offset="1" stop-color="#27C0F5" stop-opacity="0"/>
    </radialGradient>
  </defs>""",
        "bg": """  <rect width="1024" height="1024" rx="232" fill="url(#bBg)"/>
  <rect width="1024" height="1024" rx="232" fill="url(#bGlow)"/>
  <circle cx="512" cy="512" r="118" fill="#2FC4F7" fill-opacity="0.14"/>
  <rect x="6" y="6" width="1012" height="1012" rx="226" fill="none" stroke="#FFFFFF" stroke-opacity="0.09" stroke-width="6"/>""",
        "glyph": glyph_group("#7FD9FB", 0.85, "#FFFFFF", "#FFFFFF"),
        "compact_glyph": glyph_group("#7FD9FB", 0.9, "#FFFFFF", "#FFFFFF", side_w=54),
    },
    "c": {
        "title": "极简线稿",
        "defs": """  <defs>
    <linearGradient id="cGrad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#35BCF7"/>
      <stop offset="1" stop-color="#0773C4"/>
    </linearGradient>
  </defs>""",
        "bg": """  <rect width="1024" height="1024" rx="232" fill="#FAFCFE"/>
  <rect x="10" y="10" width="1004" height="1004" rx="222" fill="none" stroke="#DFE7EF" stroke-width="4"/>""",
        "glyph": glyph_group("url(#cGrad)", 0.55, "url(#cGrad)", "url(#cGrad)"),
        "compact_glyph": glyph_group("url(#cGrad)", 0.62, "url(#cGrad)", "url(#cGrad)", side_w=54),
    },
}


def svg(variant: dict, glyph: str, comment: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" '
            f'width="1024" height="1024">\n  <!-- {comment} -->\n'
            f'{variant["defs"]}\n{variant["bg"]}\n{glyph}\n</svg>\n')


def main() -> None:
    for key, v in VARIANTS.items():
        Path(f"localrouter-icon-{key}.svg").write_text(
            svg(v, v["glyph"], f"LocalRouter icon · Variant {key.upper()} “{v['title']}”"),
            encoding="utf-8")
        # Compact: same badge, glyph scaled up 1.24x, no pulse ring — reads at 16px.
        compact_glyph = v["compact_glyph"].replace(
            "<g ", '<g transform="translate(512 512) scale(1.24) translate(-512 -512)" ', 1)
        Path(f"localrouter-icon-{key}-compact.svg").write_text(
            svg(v, compact_glyph, f"LocalRouter icon · Variant {key.upper()} compact (16-32px)"),
            encoding="utf-8")
        print(f"OK variant {key} (+compact)")


if __name__ == "__main__":
    main()
