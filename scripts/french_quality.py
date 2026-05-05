"""Garde-fou de qualité FR-QC appliqué juste avant le rendu.

Ce module reste volontairement déterministe : il ne remplace pas une vraie passe
éditoriale LLM, mais il bloque les anglicismes / traductions littérales déjà
observés dans les briefings live. Les prompts restent la première ligne de
défense; ce garde-fou évite qu'un mauvais titre ou résumé se rende tel quel.
"""

from __future__ import annotations

import re
from dataclasses import replace

from scripts.models import Briefing, Item

RIGHT_SINGLE_QUOTE = "\N{RIGHT SINGLE QUOTATION MARK}"


def polish_briefing(briefing: Briefing) -> Briefing:
    """Retourne une copie du briefing avec titres/résumés normalisés FR-QC."""
    return replace(
        briefing,
        sections={
            section_id: [polish_item_text(item) for item in items]
            for section_id, items in briefing.sections.items()
        },
        dont_miss=polish_item_text(briefing.dont_miss) if briefing.dont_miss else None,
    )


def polish_item_text(item: Item) -> Item:
    """Normalise le texte lecteur d'un item sans toucher aux métadonnées."""
    title = polish_french_text(item.title)
    summary = polish_french_text(item.summary)
    if title == item.title and summary == item.summary:
        return item
    return replace(item, title=title, summary=summary)


def polish_french_text(text: str) -> str:
    """Corrige les anglicismes et calques les plus fréquents/observés."""
    if not text:
        return text

    polished = text

    # Exemples observés dans les sorties live.
    apostrophe = RIGHT_SINGLE_QUOTE
    replacements: tuple[tuple[str, str], ...] = (
        ("La U.S. Space Force", f"L{apostrophe}U.S. Space Force"),
        ("la U.S. Space Force", f"l{apostrophe}U.S. Space Force"),
        ("secrétaire à la Logement", "secrétaire au Logement"),
        ("secrétaire à le Logement", "secrétaire au Logement"),
        ("videogames", "jeux vidéo"),
        ("video games", "jeux vidéo"),
        ("workflows agentiques", "flux de travail agentiques"),
        ("un ramp-up", "une montée en charge"),
        ("ramp-up", "montée en charge"),
        ("outputs", "résultats"),
        ("solely", "seulement"),
        ("opérer compagnie", "gérer une entreprise"),
        ("opérer une compagnie", "gérer une entreprise"),
        ("opérer entreprise", "gérer une entreprise"),
        ("opérer une entreprise", "gérer une entreprise"),
        (
            "présente à événement Anthropic",
            f"présente lors d{apostrophe}un événement Anthropic",
        ),
    )
    for old, new in replacements:
        polished = polished.replace(old, new)

    # Titre live observé : « Ingénieur Google démontre entreprise gérée ... »
    polished = re.sub(
        r"\bIngénieur Google démontre entreprise gérée\b",
        "Un ingénieur de Google présente une entreprise gérée",
        polished,
    )

    # Calques fréquents autour de « compagnie » en contexte entreprise.
    polished = re.sub(
        r"\b(entreprise|compagnie) avec 1 humain\b",
        "entreprise avec une seule personne",
        polished,
    )
    polished = re.sub(
        r"\b(entreprise|compagnie) avec un humain\b",
        "entreprise avec une seule personne",
        polished,
    )

    # Ponctuation FR lisible : espaces insécables HTML non nécessaires ici; garder texte brut.
    polished = re.sub(r"\s+([,.;:!?])", r"\1", polished)
    polished = re.sub(r"\s{2,}", " ", polished).strip()

    return polished
