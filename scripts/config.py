"""Chargement + validation de sources/comptes.json contre le JSON Schema."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema


class ConfigError(Exception):
    """Levée si la config est invalide ou inconsistante."""


def load_config(
    config_path: Path = Path("sources/comptes.json"),
    schema_path: Path = Path("sources/comptes.schema.json"),
) -> dict[str, Any]:
    """Charge la config, valide contre le schema, vérifie cohérence FK."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    try:
        jsonschema.validate(config, schema)
    except jsonschema.ValidationError as e:
        raise ConfigError(f"comptes.json invalide vs schema : {e.message}") from e

    section_ids = {s["id"] for s in config["sections"]}
    for r in config["recherches_thematiques"]:
        if r["section_id"] not in section_ids:
            raise ConfigError(
                f"recherche '{r['theme']}' référence section_id inconnue : {r['section_id']}"
            )

    return config


def config_hash(config_path: Path = Path("sources/comptes.json")) -> str:
    """SHA-256 du contenu de comptes.json (pour traçabilité dans le briefing)."""
    return hashlib.sha256(config_path.read_bytes()).hexdigest()
