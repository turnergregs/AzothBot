"""The vendored-asset sync, and the assets it deliberately cannot sync.

`assets/card_art/` holds two kinds of thing, restored two different ways:

  borders/ symbols/ fonts/   copied out of an azoth checkout by this script
  backgrounds/               SHADER OUTPUT, exported from a running Godot by
                             tools/BackgroundExportTool.tscn in the azoth repo

Nothing checked that second group. `fate_layout` pointed at sync_assets for them
and every render command's error path said "run sync_assets" -- a script that has
no way to produce a file the game only renders at runtime.
"""
from pathlib import Path

import pytest

from tools import sync_assets


def test_expected_backgrounds_cover_every_rite_variant():
    """`fate_layout.rite_variant()` can return any of four; each needs a baked
    PNG and a recolourable mask, or a rite silently fails to render."""
    from azoth_logic import fate_layout as F

    for variant in set(F.RITE_BACKGROUND_BY_NAME.values()) | {F.RITE_DEFAULT_BACKGROUND}:
        assert f"rite_background_{variant}.png" in sync_assets.EXPECTED_BACKGROUNDS
        assert f"rite_background_{variant}_mask.png" in sync_assets.EXPECTED_BACKGROUNDS


def test_the_animated_variant_is_expected():
    """Only `attribute` exports animated -- all 21 live rites resolve to it."""
    assert "rite_background_attribute_mask_anim.webp" in sync_assets.EXPECTED_BACKGROUNDS


def test_the_aspect_background_is_expected():
    from azoth_logic import fate_layout as F
    assert F.ASPECT_BACKGROUND_FILE in sync_assets.EXPECTED_BACKGROUNDS


def test_every_expected_background_is_actually_vendored():
    """The repo has to be able to render aspects and rites out of the box."""
    absent = [n for n in sync_assets.EXPECTED_BACKGROUNDS
              if not (sync_assets.BOT_ROOT / sync_assets.DEST_BACKGROUNDS / n).is_file()]
    assert not absent, (
        f"missing vendored background(s): {absent}\nRe-export: {sync_assets.EXPORT_COMMAND}")


def test_a_missing_background_is_reported_and_fails(tmp_path, monkeypatch, capsys):
    """It cannot copy them, but it must not stay silent about them either.

    A clean exit on a missing background is how the renderer ends up throwing
    FileNotFoundError in Discord instead of at sync time.
    """
    monkeypatch.setattr(sync_assets, "BOT_ROOT", tmp_path)
    monkeypatch.setattr(sync_assets, "EXPECTED_BACKGROUNDS", ["rite_background_rest.png"])

    azoth = tmp_path / "azoth"
    (azoth / "scripts/autoloads").mkdir(parents=True)
    (azoth / "scripts/autoloads/utils.gd").write_text("var replace_dict = {\n}\n")

    code = sync_assets.sync(azoth, dry_run=True)
    err = capsys.readouterr().err

    assert code == 1, "a missing background must be a non-zero exit"
    assert "rite_background_rest.png" in err
    assert "BackgroundExportTool" in err


def test_a_complete_set_reports_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sync_assets, "BOT_ROOT", tmp_path)
    monkeypatch.setattr(sync_assets, "EXPECTED_BACKGROUNDS", ["only.png"])
    dest = tmp_path / sync_assets.DEST_BACKGROUNDS
    dest.mkdir(parents=True)
    (dest / "only.png").write_bytes(b"x")

    # A complete fake checkout: the background, the font and the Bessel table
    # the script copies, and the two files it parses.
    azoth = tmp_path / "azoth"
    (azoth / "scripts/autoloads").mkdir(parents=True)
    (azoth / "scripts/autoloads/utils.gd").write_text("var replace_dict = {\n}\n")
    for rel in (sync_assets.BACKGROUND, sync_assets.FONT, sync_assets.BESSEL_TABLE):
        src = azoth / rel
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"x")
    blobs = azoth / sync_assets.BORDER_BLOBS
    blobs.parent.mkdir(parents=True, exist_ok=True)
    blobs.write_text(FAKE_BLOBS)
    translations = azoth / sync_assets.TRANSLATIONS
    translations.parent.mkdir(parents=True, exist_ok=True)
    translations.write_text("keys,en\nWEAPON_ESCALATES_PER_HELD,per card held\n")

    assert sync_assets.sync(azoth, dry_run=True) == 0
    assert "not managed by this script" in capsys.readouterr().out


FAKE_BLOBS = """const vec2 BORDER_PX = vec2(600.0, 816.0);

float border_blob_0(vec2 p) {
	float v0 = dot(p, vec2(0.995542, 0.094319)) - 146.544790;
	float v1 = dot(p, vec2(0.039147, -0.999233)) - -110.280764;
	float m = max(v0, v1);
	const float K = 12.547462;
	return m + K * log(exp((v0 - m) / K) + exp((v1 - m) / K));
}
"""


def test_border_blobs_parse_negative_offsets(tmp_path):
    """`- -110.28` is a plane whose offset is negative, not a typo to skip."""
    inc = tmp_path / "blobs.gdshaderinc"
    inc.write_text(FAKE_BLOBS)
    parsed = sync_assets.parse_border_blobs(inc)
    assert parsed["size"] == [600.0, 816.0]
    (blob,) = parsed["blobs"]
    assert blob["planes"] == [[0.995542, 0.094319, 146.54479], [0.039147, -0.999233, -110.280764]]
    assert blob["k"] == 12.547462


def test_border_blobs_parse_from_the_real_game():
    azoth = Path(__file__).resolve().parent.parent.parent / "azoth"
    inc = azoth / sync_assets.BORDER_BLOBS
    if not inc.is_file():
        pytest.skip("no azoth checkout beside this repo")
    parsed = sync_assets.parse_border_blobs(inc)
    assert len(parsed["blobs"]) == 4, "card_render and the typesetter read blobs 1, 2 and 3"


def test_weapon_text_parses_from_the_real_game():
    azoth = Path(__file__).resolve().parent.parent.parent / "azoth"
    csv = azoth / sync_assets.TRANSLATIONS
    if not csv.is_file():
        pytest.skip("no azoth checkout beside this repo")
    strings = sync_assets.parse_weapon_text(csv)
    assert all(k.startswith("WEAPON_ESCALATES_") for k in strings)
    assert any("{ESCALATE}" in v for v in strings.values())


def test_a_non_azoth_path_is_refused(tmp_path):
    """Pointing it at the wrong directory should say so, not half-run."""
    assert sync_assets.sync(tmp_path, dry_run=True) == 2


def test_symbol_tokens_parse_from_the_real_game(request):
    """The token map is generated, not transcribed. If `replace_dict`'s shape
    changes, every card symbol silently disappears from rendered text."""
    azoth = Path(__file__).resolve().parent.parent.parent / "azoth"
    utils = azoth / "scripts/autoloads/utils.gd"
    if not utils.is_file():
        pytest.skip("no azoth checkout beside this repo")

    tokens = sync_assets.parse_symbol_tokens(utils)
    assert len(tokens) > 100, "replace_dict parsed to almost nothing -- shape changed?"
    assert "[1life]" in tokens
    assert tokens["[1life]"]["file"].endswith(".png")


# ---------------------------------------------------------------------------
# The vendored art has to actually reach the repo
# ---------------------------------------------------------------------------

def test_vendored_art_is_not_gitignored():
    """`.gitignore` carries a blanket `*.png` for generated output.

    Without an explicit negation it also swallows `assets/card_art/` — 157 of its
    159 files — and the bot silently ships a clone that cannot draw a single
    card. It fails at RENDER time, on someone else's machine, with a
    FileNotFoundError naming a file that is present in the working tree of
    whoever committed it.

    Recovery would need a Godot checkout for the borders and symbols, and Godot
    ITSELF for the shader-exported backgrounds — so this is much worse than a
    re-run of `sync_assets`.
    """
    import subprocess

    root = Path(__file__).resolve().parent.parent
    files = sorted(p for p in (root / "assets/card_art").rglob("*") if p.is_file())
    assert files, "no vendored art found at all"

    rel = [str(p.relative_to(root)) for p in files]
    result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"],
                            cwd=root, input="\n".join(rel), capture_output=True, text=True)
    ignored = [ln for ln in result.stdout.splitlines() if ln.strip()]

    assert not ignored, (
        f"{len(ignored)} of {len(rel)} vendored render assets are gitignored and "
        f"would not reach a fresh clone, e.g. {ignored[:3]}. "
        f"Check the `!assets/card_art/**/*.png` negation in .gitignore.")


def test_generated_output_is_still_ignored():
    """The inverse: the negation must not punch a hole in the blanket rule.

    `assets/renders/`, `assets/downloaded_images/`, `output/` and `combinations/`
    hold art the bot produced. None of it is source.
    """
    import subprocess

    root = Path(__file__).resolve().parent.parent
    generated = ["assets/renders/cards/x.png", "assets/downloaded_images/x.png",
                 "output/x.png", "combinations/x.png"]
    result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"],
                            cwd=root, input="\n".join(generated),
                            capture_output=True, text=True)
    still_ignored = set(ln.strip() for ln in result.stdout.splitlines() if ln.strip())

    missing = [p for p in generated if p not in still_ignored]
    assert not missing, f"generated output is no longer ignored: {missing}"
