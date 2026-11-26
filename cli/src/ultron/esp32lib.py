"""Arduino library artifact refresh — ``ultron esp32 lib``.

The XIAO firmware embeds a flatbuffer and a generated config header, so the library
(``devices/esp32/libraries/UltronVoice``) *is* the deployed model in source form. This
module keeps it in step with ``model/`` and versions it, so a flashed board can say which
library and which model it is running (``DBG boot fw=ultron_voice lib=0.1.1 …``).

Entry points (one implementation, three callers):

* ``ultron esp32 lib``            — the CLI command in :mod:`ultron.cli`
* ``uv run python scripts/gen_esp32_library.py``  — thin wrapper, used by docs/deploy
* ``ultron export tflite``        — refreshes automatically for ``--out build/``

Bump rules (deterministic and idempotent):

===============  ===================================================================
nothing changed  no bump, no writes (safe to run in a loop / in CI)
config or model  patch + 1
class order      minor + 1  (a consumer that hard-coded an index must notice)
===============  ===================================================================

The two generators stay in ``scripts/`` (they are repo tooling, also used directly when
regenerating headers); this module resolves the repository root the same way
:mod:`ultron.features` resolves ``dsp/`` for the ctypes MFCC binding.
"""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_DIR = REPO_ROOT / "devices" / "esp32" / "libraries" / "UltronVoice"
LIB_PROPERTIES = LIB_DIR / "library.properties"
LIB_SRC = LIB_DIR / "src"
SCRIPTS_DIR = REPO_ROOT / "scripts"

VERSION_RE = re.compile(r"^version=(\d+)\.(\d+)\.(\d+)\s*$", re.MULTILINE)


class Esp32LibError(RuntimeError):
    """Loud failure — the library is never left half-updated."""


def _generators():
    """Import the two header generators from the repo's scripts/ directory."""
    if not (SCRIPTS_DIR / "gen_esp32_config.py").is_file():
        raise Esp32LibError(
            f"repo tooling not found under {SCRIPTS_DIR} — run this from the ultron checkout"
        )
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import gen_esp32_config  # noqa: PLC0415 — resolved at call time (repo tooling)
    import gen_esp32_model  # noqa: PLC0415

    return gen_esp32_config, gen_esp32_model


def read_version(path: Path = LIB_PROPERTIES) -> str:
    if not path.is_file():
        raise Esp32LibError(f"library.properties not found: {path}")
    match = VERSION_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        raise Esp32LibError(f"{path}: no 'version=MAJOR.MINOR.PATCH' line")
    return ".".join(match.group(i) for i in (1, 2, 3))


def bump_version(version: str, level: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _write_version(version: str, path: Path = LIB_PROPERTIES) -> None:
    text = path.read_text(encoding="utf-8")
    new, count = VERSION_RE.subn(f"version={version}\n", text)
    if count != 1:
        raise Esp32LibError(f"{path}: expected exactly one version line, found {count}")
    path.write_text(new, encoding="utf-8")


@dataclass
class RefreshResult:
    version_before: str
    version_after: str
    bumped: bool
    level: str
    model_path: Path
    model_sha256: str
    placeholder: bool
    class_order: list[str]
    changed_files: list[str]

    def summary_lines(self) -> list[str]:
        lines = [
            f"UltronVoice v{self.version_before}"
            + (f" → v{self.version_after} ({self.level} bump)" if self.bumped else " (unchanged)"),
            f"  model        : {self.model_path}",
            f"  model sha256 : {self.model_sha256}",
            f"  placeholder  : {1 if self.placeholder else 0}",
            f"  class order  : {' '.join(self.class_order)}",
        ]
        if self.changed_files:
            lines.append(f"  wrote        : {', '.join(self.changed_files)}")
        else:
            lines.append("  wrote        : nothing (already current)")
        return lines


def _class_order_of(config_text: str, what: str = "config") -> list[str]:
    match = re.search(r"ULTRON_CLASS_NAMES\[[^\]]*\]\s*=\s*\{([^}]*)\}", config_text)
    names = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1)) if match else []
    if not names:
        raise Esp32LibError(f"{what}: no ULTRON_CLASS_NAMES to read the frozen order from")
    return names


def _current(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _model_path(model: Path | None, placeholder: bool) -> Path:
    gen_cfg, gen_model = _generators()
    if model is not None:
        return model
    return gen_model.DEFAULT_PLACEHOLDER_MODEL if placeholder else gen_model.DEFAULT_MODEL


def _render_config(labels: Path | None) -> str:
    gen_cfg, _ = _generators()
    try:
        return gen_cfg.generate(
            out_path=LIB_SRC / "ultron_config.h",
            thresholds_path=gen_cfg.DEFAULT_THRESHOLDS,
            registry_path=gen_cfg.DEFAULT_REGISTRY,
            labels_path=labels if labels is not None else gen_cfg.DEFAULT_LABELS,
            library_version=read_version(),
        )
    except gen_cfg.ConfigError as exc:  # type: ignore[attr-defined]
        raise Esp32LibError(str(exc)) from exc


def _render_model(model_path: Path, placeholder: bool) -> str:
    _, gen_model = _generators()
    try:
        return gen_model.generate(
            model_path,
            LIB_SRC / "model_data.h",
            LIB_SRC / "ultron_config.h",
            placeholder=placeholder,
            provenance=None,
            check=False,
        )
    except gen_model.ModelDataError as exc:  # type: ignore[attr-defined]
        raise Esp32LibError(str(exc)) from exc


def check(*, model: Path | None = None, placeholder: bool = False,
          labels: Path | None = None) -> tuple[bool, list[str], list[str]]:
    """Verify the committed library against ``model/`` without writing.

    Returns ``(ok, problems, notes)``. When the flatbuffer is absent (a clean clone —
    ``*.tflite`` is gitignored) the model header is verified for internal consistency:
    its class count must match the config's, which is the mismatch that breaks a flash.
    """
    problems: list[str] = []
    notes: list[str] = []
    gen_cfg, _ = _generators()
    config_text = _render_config(labels)

    if _current(LIB_SRC / "ultron_config.h") != config_text:
        problems.append("ultron_config.h")

    for name in ("mfcc.c", "mfcc.h"):
        master, copy = REPO_ROOT / "dsp" / name, LIB_SRC / name
        if not copy.is_file() or copy.read_bytes() != master.read_bytes():
            problems.append(name)

    model_path = _model_path(model, placeholder)
    if model_path.is_file():
        if _current(LIB_SRC / "model_data.h") != _render_model(model_path, placeholder):
            problems.append("model_data.h")
    else:
        header = _current(LIB_SRC / "model_data.h")
        match = re.search(r"^#define ULTRON_MODEL_CLASS_COUNT (\d+)$", header, re.MULTILINE)
        n_config = len(_class_order_of(config_text))
        if match is None:
            problems.append("model_data.h (missing ULTRON_MODEL_CLASS_COUNT)")
        elif int(match.group(1)) != n_config:
            problems.append(
                f"model_data.h ({match.group(1)} classes) vs ultron_config.h ({n_config})"
            )
        else:
            notes.append(
                f"{model_path.name} absent — verified the model header's class count "
                "against the config instead of its bytes"
            )
    return (not problems), problems, notes


def refresh(
    *,
    model: Path | None = None,
    placeholder: bool = False,
    labels: Path | None = None,
    bump: bool = True,
) -> RefreshResult:
    """Rewrite the library from ``model/`` and bump ``library.properties`` if needed."""
    _, gen_model = _generators()
    import hashlib

    model_path = _model_path(model, placeholder)
    if not model_path.is_file():
        raise Esp32LibError(
            f"model not found: {model_path} — build the placeholder "
            "(`uv run python scripts/gen_esp32_placeholder.py --placeholder`) or run "
            "`uv run ultron export tflite`"
        )

    version = read_version()
    config_text = _render_config(labels)
    model_text = _render_model(model_path, placeholder)

    config_stale = _current(LIB_SRC / "ultron_config.h") != config_text
    model_stale = _current(LIB_SRC / "model_data.h") != model_text
    mfcc_stale = any(
        not (LIB_SRC / name).is_file()
        or (LIB_SRC / name).read_bytes() != (REPO_ROOT / "dsp" / name).read_bytes()
        for name in ("mfcc.c", "mfcc.h")
    )

    result = RefreshResult(
        version_before=version,
        version_after=version,
        bumped=False,
        level="patch",
        model_path=model_path,
        model_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
        placeholder=placeholder,
        class_order=_class_order_of(config_text),
        changed_files=[],
    )
    if not (config_stale or model_stale or mfcc_stale):
        return result

    level = "patch"
    if config_stale:
        old_order = _class_order_of(_current(LIB_SRC / "ultron_config.h"), "ultron_config.h")
        if old_order != result.class_order:
            level = "minor"
    result.level = level

    if bump:
        _write_version(bump_version(version, level))
        result.version_after = read_version()
        result.bumped = True
        config_text = _render_config(labels)  # the header embeds the new version
        model_text = _render_model(model_path, placeholder)

    for name in ("mfcc.c", "mfcc.h"):
        shutil.copyfile(REPO_ROOT / "dsp" / name, LIB_SRC / name)
    (LIB_SRC / "ultron_config.h").write_text(config_text, encoding="utf-8")
    (LIB_SRC / "model_data.h").write_text(model_text, encoding="utf-8")
    result.changed_files = ["mfcc.c", "mfcc.h", "ultron_config.h", "model_data.h"]
    if result.bumped:
        result.changed_files.append("library.properties")
    return result
