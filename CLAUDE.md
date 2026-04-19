# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## État du projet

Repo en amorçage : il ne contient pour l'instant que le PRD, un README, et la config sources (`sources/comptes.json`). Les répertoires `scripts/`, `templates/`, et `output/` annoncés dans le README n'existent pas encore — à créer au premier commit de code. Aucune toolchain Python n'est en place (pas de `pyproject.toml`, `requirements.txt`, ni tests). Le runtime cible est Python 3.11+.

## Architecture cible

Pipeline quotidien exécuté par **Hermes Agent** (hôte externe à ce repo) via le skill `briefing-matinal`. Ce repo fournit la config + les scripts ; Hermes fournit le cron, les secrets, l'envoi Telegram, et le NAS.

Flux : `sources/comptes.json` → `scripts/build-briefing.py` (interroge xAI Grok `x_search` via `/v1/responses` + `web_search` Hermes en parallèle) → rendu via `templates/briefing.html` → fichier HTML standalone dans `output/YYYY-MM-DD.html` → Hermes envoie sur Telegram à 6h45 AM (America/Toronto), fallback NAS `/mnt/nas/commun/briefing-matinal/`.

Les sections du briefing (AI/Tech, Tesla, SpaceX, Santé, Politique, Business + "En 60 secondes" et "À ne pas manquer") sont **data-driven** depuis `comptes.json` (clé `sections`, avec `max_items` par section). Toute modification de portée (comptes X, thèmes de recherche, sources web) passe par `comptes.json`, pas par le code.

## Contraintes dures (non négociables)

- **HTML standalone** : inline CSS uniquement, pas de CDN, pas de JS, pas de Google Fonts — polices système.
- **Taille max 50 KB** (limite document Telegram).
- **Mobile-first**, lisible en dark ET light.
- **Latence < 3 min** pour la génération complète.
- **Budget** ~0.10-0.15 $/jour (3-5 appels Grok max).
- **Output gitignored** (`output/`), comme `.env` et `__pycache__/`.

## Non-goals explicites (voir PRD §Non-goals)

Pas de posting X, pas de résumé vidéo YouTube, pas d'analyse de sentiment, pas de dashboard web, pas de multi-utilisateur. Ne pas fusionner avec le skill `bst-veille-hebdo` (veille pro, séparée).

## Références

- **PRD.md** — source de vérité pour les specs fonctionnelles (S1–S5), les contraintes techniques, et les critères de succès.
- **sources/comptes.json** — config runtime (15 comptes X, 8 recherches thématiques, 3 sources web, 6 sections).
