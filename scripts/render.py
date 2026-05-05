"""Wrapper Jinja2 + validation (taille, pas de CDN). Voir PRD §S2."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from scripts.french_quality import polish_briefing
from scripts.models import Briefing

SIZE_BUDGET_BYTES = 50_000
OPTIONAL_SECTION_IDS = frozenset({"gouvernance", "cybersec", "leadership", "futur-travail"})
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


def _sections_for_render(
    briefing: Briefing,
    sections_config: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Filtre les sections visibles pour le lecteur.

    Les sections secondaires vides sont masquées. Les sections principales vides
    peuvent afficher une seule note compacte au total pour éviter une suite de
    placeholders qui donne une impression de pipeline cassé.
    """
    visible: list[dict[str, Any]] = []
    empty_placeholder_used = False

    for section in sections_config:
        section_id = section["id"]
        items = briefing.sections.get(section_id, [])
        is_optional = section_id in OPTIONAL_SECTION_IDS

        if items:
            visible.append({**section, "show_empty_placeholder": False})
            continue

        if is_optional:
            continue

        if not empty_placeholder_used:
            visible.append({**section, "show_empty_placeholder": True})
            empty_placeholder_used = True

    return visible


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

    briefing = polish_briefing(briefing)
    visible_sections = _sections_for_render(briefing, sections_config)

    html = template.render(briefing=briefing, sections_in_order=visible_sections)

    warnings: list[str] = []
    size = len(html.encode("utf-8"))
    if size > SIZE_BUDGET_BYTES:
        warnings.append(f"HTML size {size} bytes > budget {SIZE_BUDGET_BYTES}")

    if _CDN_RE.search(html):
        match = _CDN_RE.search(html)
        raise RenderError(f"CDN détecté dans le HTML rendu : {match.group(0) if match else '?'}")

    return html, warnings
