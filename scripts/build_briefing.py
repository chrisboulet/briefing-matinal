"""CLI orchestrateur du briefing. Voir PRD §S3 + PLAN Phase 1/3."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.config import ConfigError, config_hash, load_config
from scripts.dedup import dedupe
from scripts.fixture_loader import load_fixture
from scripts.models import Briefing, Item
from scripts.render import RenderError, render
from scripts.select import (
    apply_engagement_filter,
    select_by_section,
    select_dont_miss,
    select_sixty_seconds,
)
from scripts.window import briefing_id as compute_briefing_id
from scripts.window import compute_window

# Bumper en cas de modification sémantique des prompts dans prompts/.
# Le hash est injecté en footer du HTML rendu (traçabilité audit).
PROMPTS_VERSION = "prompts-v1.0"


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


def _source_via_xai(
    config: dict,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[Item], list[str]]:
    """
    Charge des items via xAI Responses API (mode live, Phase 3).
    Lazy import pour éviter dépendance httpx en mode fixture pur.
    """
    from scripts.sourcing import source_briefing
    from scripts.xai_client import XAIClient

    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise ConfigError(
            "Live mode requires XAI_API_KEY env var. "
            "Use --fixture for offline mode."
        )

    model = os.environ.get("XAI_MODEL", "grok-4-1-fast-latest")
    timeout_s = float(os.environ.get("XAI_TIMEOUT_S", "30"))

    with XAIClient(api_key=api_key, model=model, timeout_s=timeout_s) as client:
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

    fixture_meta: dict | None = None
    if fixtures:
        # Mode offline (Phase 1) — fixtures fournies
        all_items, source_warnings, fixture_meta = _source_via_fixtures(fixtures)
    else:
        # Mode live (Phase 3) — appels xAI réels
        # On a besoin de la fenêtre AVANT le sourcing
        now_for_window = _parse_now(now_override, None)
        window_start_pre, window_end_pre = compute_window(moment, now_for_window)  # type: ignore[arg-type]
        all_items, source_warnings = _source_via_xai(config, window_start_pre, window_end_pre)

    now = _parse_now(now_override, fixture_meta)
    window_start, window_end = compute_window(moment, now)  # type: ignore[arg-type]
    bid = compute_briefing_id(moment, now)  # type: ignore[arg-type]

    filtered = apply_engagement_filter(all_items, config["engagement_min"])
    filtered = [it for it in filtered if window_start <= it.published_at <= window_end]
    deduped = dedupe(filtered)

    sections = select_by_section(deduped, sections_cfg)
    sixty_sec = select_sixty_seconds(sections, n=3)
    dont_miss = select_dont_miss(deduped, sections)

    warnings: list[str] = list(source_warnings)
    for sec in sections_cfg:
        if not sections.get(sec["id"]):
            warnings.append(f"section '{sec['id']}' vide")

    briefing = Briefing(
        briefing_id=bid,
        moment=moment,  # type: ignore[arg-type]
        generated_at=datetime.now(tz=UTC),
        window_start=window_start,
        window_end=window_end,
        sixty_seconds=sixty_sec,
        sections=sections,
        dont_miss=dont_miss,
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
