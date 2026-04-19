"""Wrapper Jinja2 + validation (taille, pas de CDN). Voir PRD §S2."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from scripts.models import Briefing

SIZE_BUDGET_BYTES = 50_000
_CDN_RE = re.compile(
    r"https?://[^\s'\"]+\.(googleapis|cloudflare|jsdelivr|gstatic|googleusercontent)\.",
    re.IGNORECASE,
)


class RenderError(Exception):
    """Levée si le rendu viole une contrainte dure (CDN, HTML invalide)."""


def make_env(templates_dir: Path = Path("templates")) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render(
    briefing: Briefing,
    sections_config: list[dict[str, Any]],
    templates_dir: Path = Path("templates"),
    template_name: str = "briefing.html",
) -> tuple[str, list[str]]:
    """
    Rend le briefing en HTML. Retourne (html, warnings).
    Warnings non bloquants : taille > budget. Bloquants : CDN détecté.
    """
    env = make_env(templates_dir)
    template = env.get_template(template_name)

    html = template.render(briefing=briefing, sections_in_order=sections_config)

    warnings: list[str] = []
    size = len(html.encode("utf-8"))
    if size > SIZE_BUDGET_BYTES:
        warnings.append(f"HTML size {size} bytes > budget {SIZE_BUDGET_BYTES}")

    if _CDN_RE.search(html):
        match = _CDN_RE.search(html)
        raise RenderError(f"CDN détecté dans le HTML rendu : {match.group(0) if match else '?'}")

    return html, warnings
