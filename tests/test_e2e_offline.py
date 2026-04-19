"""Test end-to-end offline : pipeline complet via fixture, sans appel externe."""

from __future__ import annotations

from pathlib import Path

from scripts.build_briefing import build


def test_e2e_matin_with_fixture(tmp_path: Path):
    result = build(
        moment="matin",
        config_path=Path("sources/comptes.json"),
        fixtures=[Path("tests/fixtures/sample_matin.json")],
        output_dir=tmp_path,
        dry_run=False,
    )
    assert result["status"] == "ok"
    assert result["items_count"] > 0
    html_path = Path(result["path"])
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "BRIEFING MATIN" in html
    assert "EN 60 SECONDES" not in html  # retiré via issue #22


def test_e2e_idempotence(tmp_path: Path):
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    args = dict(
        moment="matin",
        config_path=Path("sources/comptes.json"),
        fixtures=[Path("tests/fixtures/sample_matin.json")],
        dry_run=False,
    )
    build(output_dir=out1, **args)  # type: ignore[arg-type]
    build(output_dir=out2, **args)  # type: ignore[arg-type]

    files1 = sorted(out1.rglob("*.html"))
    files2 = sorted(out2.rglob("*.html"))
    assert len(files1) == 1 and len(files2) == 1

    a = files1[0].read_text(encoding="utf-8")
    b = files2[0].read_text(encoding="utf-8")

    # generated_at diffère entre runs : on retire la ligne meta pour comparer le contenu stable
    def strip_meta(s: str) -> str:
        return "\n".join(line for line in s.splitlines() if "généré:" not in line)

    assert strip_meta(a) == strip_meta(b), "rendu non-idempotent"


def test_e2e_dry_run_skips_disk_write(tmp_path: Path):
    result = build(
        moment="matin",
        config_path=Path("sources/comptes.json"),
        fixtures=[Path("tests/fixtures/sample_matin.json")],
        output_dir=tmp_path,
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert not list(tmp_path.iterdir())
