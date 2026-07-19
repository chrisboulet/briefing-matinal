"""CLI orchestrateur du briefing. Voir PRD §S3 + PLAN Phase 1/3."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.config import ConfigError, config_hash, load_config
from scripts.dedup import dedupe
from scripts.fixture_loader import load_fixture
from scripts.models import Briefing, Item
from scripts.render import RenderError, render
from scripts.scoring import rescore_items
from scripts.select import (
    DEFAULT_ITEMS_MAX,
    DEFAULT_ITEMS_MIN,
    DEFAULT_MAX_ITEMS_PER_AUTHOR,
    DEFAULT_TOP_SIGNALS_MAX,
    apply_engagement_filter,
    assemble_selection,
    soften_engagement_min,
)
from scripts.window import briefing_id as compute_briefing_id
from scripts.window import compute_window

if TYPE_CHECKING:
    from scripts.xai_client import XAIClient

# Bumper en cas de modification sémantique des prompts dans prompts/.
# Le hash est injecté en footer du HTML rendu (traçabilité audit).
# v1.3 : French quality gate + titres FR par défaut + masquage sections vides.
# v1.4 : hot multi-camp / origin-first patterns (issue #38, whathappened-inspired).
PROMPTS_VERSION = "prompts-v1.4"

# Kill switch pour l'enrichissement 2e passe (issue #25).
# Exporter `BRIEFING_ENRICH=0` pour désactiver. Défaut "1" = actif en mode live.
_ENRICH_ENV_VAR = "BRIEFING_ENRICH"


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, check=True, timeout=2,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return "unknown"


def _parse_now(now_arg: str | None, fixture_meta: dict | None) -> datetime:
    if now_arg:
        return datetime.fromisoformat(now_arg)
    if fixture_meta and "now" in fixture_meta:
        return datetime.fromisoformat(fixture_meta["now"])
    return datetime.now(tz=UTC)


def _source_via_fixtures(
    fixtures: list[Path],
) -> tuple[list[Item], list[str], dict | None]:
    """Charge des items depuis fixtures JSON (mode offline, Phase 1)."""
    all_items: list[Item] = []
    fixture_meta: dict | None = None
    for fx in fixtures:
        items, meta = load_fixture(fx)
        all_items.extend(items)
        if meta and fixture_meta is None:
            fixture_meta = meta
    return all_items, [], fixture_meta


def _make_xai_client() -> XAIClient:
    """
    Instancie un `XAIClient` depuis l'env. Lazy import pour éviter la
    dépendance httpx en mode fixture pur.

    Le caller est responsable du `with client:` (context manager).
    Lève `ConfigError` si `XAI_API_KEY` manque.
    """
    from scripts.xai_client import XAIClient

    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise ConfigError(
            "Live mode requires XAI_API_KEY env var. "
            "Use --fixture for offline mode."
        )

    model = os.environ.get("XAI_MODEL", "grok-4.5")
    # Issue #44 : défaut 90s (agentic x_search dépasse souvent 30s sous charge).
    raw_timeout = os.environ.get("XAI_TIMEOUT_S", "90")
    try:
        timeout_s = float(raw_timeout)
    except ValueError:
        timeout_s = 90.0
    timeout_s = max(15.0, min(timeout_s, 180.0))

    return XAIClient(api_key=api_key, model=model, timeout_s=timeout_s)


def _source_via_xai(
    client: XAIClient,
    config: dict,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[Item], list[str]]:
    """
    Charge des items via xAI Responses API (mode live, Phase 3).

    Le `client` est passé par le caller (qui gère le context manager) pour
    qu'il puisse être réutilisé par l'enrichissement 2e passe (issue #25).
    """
    from scripts.sourcing import source_briefing

    result = source_briefing(client, config, window_start, window_end)

    sys.stderr.write(json.dumps({
        "event": "sourcing_total",
        "items_raw": len(result.items),
        "warnings_count": len(result.warnings),
        "tokens_in": result.total_usage.input_tokens,
        "tokens_out": result.total_usage.output_tokens,
        "tool_calls": result.total_usage.tool_calls,
        "cost_usd": round(result.total_usage.cost_usd, 4),
    }) + "\n")

    return result.items, result.warnings


def _enrich_live(
    client: XAIClient,
    sections: dict[str, list[Item]],
    top_signals: list[Item],
) -> tuple[dict[str, list[Item]], list[Item], list[str]]:
    """
    Appelle l'enrichissement 2e passe (issue #25).

    Skippé silencieusement si `BRIEFING_ENRICH=0`. Retourne les sections
    et `top_signals` potentiellement enrichis, plus la liste des warnings.
    """
    if os.environ.get(_ENRICH_ENV_VAR, "1") == "0":
        sys.stderr.write(json.dumps({
            "event": "enrichment_skipped",
            "reason": f"{_ENRICH_ENV_VAR}=0",
        }) + "\n")
        return sections, top_signals, []

    from scripts.enrichment import enrich_selected

    result = enrich_selected(client, sections, top_signals=top_signals)
    return result.sections, result.top_signals, result.warnings


def build(
    moment: str,
    config_path: Path,
    fixtures: list[Path],
    output_dir: Path,
    dry_run: bool,
    now_override: str | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    sections_cfg = config["sections"]
    cfg_hash = config_hash(config_path)
    max_per_author = int(
        config.get("max_items_per_author", DEFAULT_MAX_ITEMS_PER_AUTHOR)
    )
    top_n = int(config.get("top_signals_max", DEFAULT_TOP_SIGNALS_MAX))
    items_min = int(config.get("items_min", DEFAULT_ITEMS_MIN))
    items_max = int(config.get("items_max", DEFAULT_ITEMS_MAX))

    fixture_meta: dict | None = None
    source_warnings: list[str]
    enrich_warnings: list[str] = []
    budget_warnings: list[str] = []

    def _pipeline(all_items: list[Item], window_start: datetime, window_end: datetime):
        """Filter → rescore → dedupe → assemble budget [min,max]."""
        nonlocal budget_warnings
        windowed = [
            it for it in all_items if window_start <= it.published_at <= window_end
        ]
        engagement = dict(config["engagement_min"])
        filtered = apply_engagement_filter(windowed, engagement)
        # Si trop peu de matière pour le min, assouplir l'engagement une fois.
        if len(filtered) < items_min:
            soft = soften_engagement_min(engagement)
            softer = apply_engagement_filter(windowed, soft)
            if len(softer) > len(filtered):
                budget_warnings.append(
                    "engagement_softened: "
                    f"likes>={soft['likes']} OR reposts>={soft['reposts']} "
                    f"(avait {len(filtered)}, maintenant {len(softer)})"
                )
                filtered = softer
        rescored = rescore_items(filtered, window_start, window_end)
        deduped = dedupe(rescored)
        top_signals, sections, sel_warnings = assemble_selection(
            deduped,
            sections_cfg,
            top_signals_max=top_n,
            items_min=items_min,
            items_max=items_max,
            max_items_per_author=max_per_author,
        )
        budget_warnings.extend(sel_warnings)
        dont_miss = top_signals[0] if top_signals else None
        return sections, dont_miss, top_signals

    if fixtures:
        # Mode offline (Phase 1) — fixtures fournies, pas d'enrichissement
        # (pas de vrai client xAI disponible).
        all_items, source_warnings, fixture_meta = _source_via_fixtures(fixtures)

        now = _parse_now(now_override, fixture_meta)
        window_start, window_end = compute_window(moment, now)  # type: ignore[arg-type]
        bid = compute_briefing_id(moment, now)  # type: ignore[arg-type]

        sections, dont_miss, top_signals = _pipeline(all_items, window_start, window_end)
    else:
        # Mode live (Phase 3+Issue #25) — sourcing + enrichissement 2e passe,
        # tous deux sous le même `XAIClient` (1 httpx.Client réutilisé).
        now_for_window = _parse_now(now_override, None)
        window_start_pre, window_end_pre = compute_window(moment, now_for_window)  # type: ignore[arg-type]

        with _make_xai_client() as client:
            all_items, source_warnings = _source_via_xai(
                client, config, window_start_pre, window_end_pre,
            )

            now = _parse_now(now_override, None)
            window_start, window_end = compute_window(moment, now)  # type: ignore[arg-type]
            bid = compute_briefing_id(moment, now)  # type: ignore[arg-type]

            sections, dont_miss, top_signals = _pipeline(
                all_items, window_start, window_end
            )

            # Enrichissement 2e passe (issue #25) : hooké ICI, après sélection,
            # avant la construction du Briefing. Kill switch via BRIEFING_ENRICH=0.
            sections, top_signals, enrich_warnings = _enrich_live(
                client, sections, top_signals,
            )
            dont_miss = top_signals[0] if top_signals else None

    warnings: list[str] = (
        list(source_warnings) + list(enrich_warnings) + list(budget_warnings)
    )
    for sec in sections_cfg:
        if not sections.get(sec["id"]):
            warnings.append(f"section '{sec['id']}' vide")

    briefing = Briefing(
        briefing_id=bid,
        moment=moment,  # type: ignore[arg-type]
        generated_at=datetime.now(tz=UTC),
        window_start=window_start,
        window_end=window_end,
        sections=sections,
        dont_miss=dont_miss,
        top_signals=top_signals,
        config_hash=cfg_hash,
        prompts_version=PROMPTS_VERSION,
        git_commit=_git_commit(),
        warnings=warnings,
    )

    html, render_warnings = render(briefing, sections_cfg)
    warnings.extend(render_warnings)

    output_path = output_dir / f"{bid}.html"
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

    return {
        "status": "ok",
        "path": str(output_path),
        "briefing_id": bid,
        "items_count": briefing.items_count,
        "warnings": warnings,
        "size_bytes": len(html.encode("utf-8")),
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_briefing", description="Génère un briefing.")
    parser.add_argument("--moment", choices=["matin", "soir"], required=True)
    parser.add_argument("--fixture", type=Path, action="append", default=[],
                        help="Fixture offline (requis en Phase 1).")
    parser.add_argument("--config", type=Path, default=Path("sources/comptes.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--dry-run", action="store_true",
                        help="N'écrit pas le HTML sur disque, print taille seulement.")
    parser.add_argument("--now", type=str, default=None,
                        help="Override 'now' pour reproductibilité (ISO 8601).")
    args = parser.parse_args(argv)

    try:
        result = build(
            moment=args.moment,
            config_path=args.config,
            fixtures=args.fixture,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
            now_override=args.now,
        )
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except RenderError as e:
        print(f"render error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
