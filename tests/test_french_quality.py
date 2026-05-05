"""Tests de garde-fou FR-QC avant rendu final."""

from __future__ import annotations

from dataclasses import replace

from scripts.french_quality import RIGHT_SINGLE_QUOTE, polish_item_text


def test_polish_item_text_removes_observed_fr_en_mixes(make_item):
    item = replace(
        make_item(
            "Ingénieur Google démontre entreprise gérée solely par agents IA",
            "https://example.com/google-agent-company",
            section_id="ai-tech",
        ),
        summary=(
            "La U.S. Space Force signe un contrat. Scott Turner, secrétaire à la Logement "
            "et au Développement urbain, commente. Le détaillant de videogames veut "
            "présente à événement Anthropic comment opérer compagnie avec 1 humain, "
            "des workflows agentiques, un ramp-up rapide et des outputs mesurables."
        ),
    )

    polished = polish_item_text(item)
    combined = f"{polished.title}\n{polished.summary}"

    assert "solely" not in combined
    assert "videogames" not in combined
    assert "secrétaire à la Logement" not in combined
    assert "La U.S. Space Force" not in combined
    assert "opérer compagnie" not in combined
    assert "workflows agentiques" not in combined
    assert "ramp-up" not in combined
    assert "outputs" not in combined

    assert "seulement" in combined
    assert "jeux vidéo" in combined
    assert "secrétaire au Logement" in combined
    assert f"L{RIGHT_SINGLE_QUOTE}U.S. Space Force" in combined
    assert "gérer une entreprise" in combined
    assert "flux de travail agentiques" in combined


def test_polish_item_text_is_non_destructive_for_clean_french(make_item):
    item = replace(
        make_item(
            "Tesla lance un rappel logiciel ciblé",
            "https://example.com/tesla",
            section_id="tesla",
        ),
        summary="Tesla publie une mise à jour logicielle ciblée pour corriger un problème précis.",
    )

    polished = polish_item_text(item)

    assert polished == replace(item)
