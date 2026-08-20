# tests/test_dependencies.py — the test that would have caught the app not starting.
#
# The rest of the suite stubs streamlit, plotly, agent.tools and agent.llm
# (tests/test_app_rendering.py), which is the right call for those tests: they
# are about what the page *says*, and a real Streamlit runtime would only make
# them slow and flaky. The cost is that "tests green" stopped implying "the app
# starts". It was possible — and it happened — for the whole suite to pass in an
# environment with no streamlit, no plotly, no statsmodels and no scikit-learn.
#
# This module closes that gap from the other side. It does not stub anything.
# It walks the import graph from app.py with the AST (so it cannot drift from
# the code the way a hand-written list does), and then:
#
#   1. imports every third-party top-level module for real;
#   2. asserts every one of them is pinned in requirements.txt.
#
# (1) fails if the environment is incomplete. (2) fails if someone adds an import
# without declaring it — which is the failure mode that produced this file.
from __future__ import annotations

import ast
import importlib
import importlib.metadata as metadata
import re
import sys
import sysconfig
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO / "app.py"
REQUIREMENTS = REPO / "requirements.txt"

#: Modules whose *distribution* name differs from their *import* name. There is
#: no general mapping in the stdlib; each entry below is one the app actually
#: uses, so an unlisted mismatch shows up as a failure rather than a silent pass.
IMPORT_TO_DISTRIBUTION = {
    "sklearn": "scikit-learn",
    "dotenv": "python-dotenv",
}


# ─────────────────────────── the import graph ───────────────────────────

def _module_to_path(module: str) -> Path | None:
    """Resolve a first-party dotted module name to a file in this repo."""
    parts = module.split(".")
    candidates = [REPO.joinpath(*parts).with_suffix(".py"),
                  REPO.joinpath(*parts, "__init__.py")]
    return next((c for c in candidates if c.is_file()), None)


def _imports_in(path: Path) -> set[str]:
    """Every dotted module name imported by `path`, at any nesting depth.

    Walks the whole tree rather than only module-level statements: agent/llm.py
    imports `openai` inside a function and agent/lab_bridge.py imports
    `subprocess` inside one, and both are real dependencies of running the app.

    `from agent import tools as T` is recorded as both `agent` and
    `agent.tools`, because the imported name is a submodule, not an attribute —
    and missing that edge is exactly how statsmodels and scikit-learn drop out
    of the graph while app.py still needs them.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has no module name to resolve; this repo has none.
            if node.level == 0 and node.module:
                found.add(node.module)
                found.update(f"{node.module}.{a.name}" for a in node.names
                             if a.name != "*")
    return found


def reachable_imports(entrypoint: Path = ENTRYPOINT) -> set[str]:
    """Transitive closure of imports from `entrypoint`, following first-party ones."""
    seen_files: set[Path] = set()
    queue = [entrypoint]
    modules: set[str] = set()
    while queue:
        path = queue.pop()
        if path in seen_files:
            continue
        seen_files.add(path)
        for module in _imports_in(path):
            modules.add(module)
            local = _module_to_path(module)
            if local is not None:
                queue.append(local)
    return modules


def _top_level(module: str) -> str:
    return module.split(".")[0]


def _is_stdlib(name: str) -> bool:
    if name in sys.stdlib_module_names:
        return True
    # `__future__` is stdlib but excluded from stdlib_module_names on some builds.
    return name == "__future__"


def third_party_modules() -> list[str]:
    """Top-level third-party modules the app needs, sorted."""
    tops = {_top_level(m) for m in reachable_imports()}
    return sorted(
        name for name in tops
        if not _is_stdlib(name)
        and _module_to_path(name) is None      # not one of ours
        and not name.startswith("_")
    )


# ─────────────────────────── requirements.txt ───────────────────────────

_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s#]+)")


def pinned_requirements() -> dict[str, str]:
    """`{normalised distribution name: version}` from requirements.txt."""
    out: dict[str, str] = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        m = _PIN.match(line)
        if m:
            out[_normalise(m.group(1))] = m.group(2)
    return out


def _normalise(name: str) -> str:
    """PEP 503 name normalisation, so `scikit_learn` and `scikit-learn` match."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _distribution_for(module: str) -> str:
    return IMPORT_TO_DISTRIBUTION.get(module, module)


# ─────────────────────────── the tests ───────────────────────────

def test_the_import_graph_is_walkable_and_not_empty():
    """A guard on the guard: an AST walk that silently found nothing would
    make every test below pass vacuously."""
    modules = third_party_modules()
    assert modules, "no third-party imports found — the AST walk is broken"
    # These four are the ones that were missing when the app would not start.
    for expected in ("streamlit", "plotly", "statsmodels", "sklearn"):
        assert expected in modules, f"{expected} vanished from app.py's import graph"


def _module_level_imports(path: Path) -> set[str]:
    """Only the imports executed when the module is first loaded."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in tree.body:                      # direct children only
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names
                         if a.name != "*")
    return found


def test_the_demo_toolbox_is_not_imported_at_startup():
    """`agent.tools` drags in statsmodels and scikit-learn — around 150 MB and
    several seconds of import time — for the local demo forecaster. Lab mode
    never calls any of it, so the import lives inside the demo-mode branch.

    Both halves matter. Import it at module level and every lab-mode start
    pays for a code path it will not run; drop it from the graph entirely and
    the two packages become undeclared dependencies of demo mode.
    """
    # `agent` itself is expected here — lab_bridge, plain and contract are all
    # imported at module level. `agent.tools` is the submodule under test, and
    # `from agent import tools` records it by name (see `_imports_in`).
    top_level = _module_level_imports(ENTRYPOINT)
    assert "agent.tools" not in top_level, (
        "agent.tools is imported at app.py's module level again; lab mode "
        "pays ~150 MB of statsmodels + scikit-learn for code it never calls")
    assert "agent.tools" in reachable_imports(), (
        "agent.tools left app.py's import graph entirely — statsmodels and "
        "scikit-learn would stop being declared for demo mode")
    for still_needed in ("statsmodels", "sklearn"):
        assert still_needed in third_party_modules()


@pytest.mark.parametrize("module", third_party_modules())
def test_every_module_app_needs_actually_imports(module):
    """No stubs. If this fails, `streamlit run app.py` fails the same way."""
    try:
        importlib.import_module(module)
    except ImportError as exc:
        pytest.fail(
            f"app.py needs `{module}`, but importing it failed: {exc}\n"
            f"Install the declared set: pip install -r requirements-dev.txt")


@pytest.mark.parametrize("module", third_party_modules())
def test_every_module_app_needs_is_pinned_in_requirements(module):
    """Declared, and declared at an exact version.

    `>=` ranges are what let this repo claim streamlit as a dependency while
    nothing in CI ever resolved one.
    """
    dist = _normalise(_distribution_for(module))
    pins = pinned_requirements()
    assert dist in pins, (
        f"`{module}` is imported on a path reachable from app.py but is not "
        f"pinned in requirements.txt (expected distribution `{dist}`). "
        f"Add it, or add an entry to IMPORT_TO_DISTRIBUTION if the import name "
        f"and the distribution name differ.")


@pytest.mark.parametrize("module", third_party_modules())
def test_the_installed_version_is_the_pinned_version(module):
    """The environment matches the file, so a passing suite describes a real build."""
    dist = _distribution_for(module)
    pins = pinned_requirements()
    try:
        installed = metadata.version(dist)
    except metadata.PackageNotFoundError:
        pytest.fail(f"`{dist}` is not installed; requirements.txt pins "
                    f"{pins.get(_normalise(dist))}")
    assert installed == pins[_normalise(dist)], (
        f"{dist}: installed {installed}, requirements.txt pins "
        f"{pins[_normalise(dist)]}. Reinstall, or update the pin deliberately.")


def test_requirements_pins_nothing_the_app_cannot_reach():
    """The other direction: a pin with no importer is dead weight that gets
    copied forward. `pydantic`, `rich` and `joblib` sat here for months
    unimported while `certifi` — which app.py genuinely needs — did not."""
    needed = {_normalise(_distribution_for(m)) for m in third_party_modules()}
    extra = sorted(set(pinned_requirements()) - needed)
    assert not extra, (
        f"requirements.txt pins {extra}, which nothing reachable from app.py "
        f"imports. Remove them, or move them to requirements-dev.txt with a "
        f"comment saying who uses them.")


def test_the_test_suite_itself_is_installable():
    """requirements-dev.txt exists and layers on the runtime set."""
    dev = REPO / "requirements-dev.txt"
    assert dev.is_file(), "requirements-dev.txt is missing"
    text = dev.read_text(encoding="utf-8")
    assert "-r requirements.txt" in text
    assert "pytest==" in text, "pytest must be pinned too"
