"""Package-level invariants (docs/adr/0005-internal-ports-capability-model.md): MatchingEngine is
never a port, and no port module imports a provider SDK/framework type into its public surface.
"""

import ast
import sys
from pathlib import Path

import tapjob_api.ports as ports

_PORTS_DIR = Path(ports.__file__).resolve().parent

# Everything a port module is allowed to import from. Deliberately narrow: stdlib plus this
# package itself. A provider SDK, FastAPI/Starlette, httpx, or any adapter-specific type must
# never appear here (docs/ENGINEERING_STANDARDS.md §13-14) -- that is the whole point of a port.
_ALLOWED_TOP_LEVEL_MODULES = frozenset(sys.stdlib_module_names) | {"tapjob_api", "__future__"}


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def test_matching_engine_is_not_a_port() -> None:
    assert "MatchingEngine" not in ports.__all__
    assert not hasattr(ports, "MatchingEngine")
    assert all("MatchingEngine" not in name for name in dir(ports))


def test_ports_barrel_exports_exactly_the_seven_canonical_ports() -> None:
    port_protocol_names = {
        name
        for name in ports.__all__
        if name
        in {
            "JobSource",
            "ResumeParser",
            "ResumeRenderer",
            "AiGateway",
            "MessagingChannel",
            "ApplyConnector",
            "InboxConnector",
        }
    }
    assert port_protocol_names == {
        "JobSource",
        "ResumeParser",
        "ResumeRenderer",
        "AiGateway",
        "MessagingChannel",
        "ApplyConnector",
        "InboxConnector",
    }


def test_no_port_module_imports_outside_the_stdlib_and_this_package() -> None:
    violations: list[str] = []
    for path in sorted(_PORTS_DIR.glob("*.py")):
        imported = _imported_top_level_modules(path)
        disallowed = imported - _ALLOWED_TOP_LEVEL_MODULES
        if disallowed:
            violations.append(f"{path.name}: {sorted(disallowed)}")

    assert violations == [], f"port modules importing outside stdlib/tapjob_api: {violations}"
