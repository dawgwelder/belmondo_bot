"""Guard the dependency and transaction boundaries of the decomposed Spy game."""

import ast
from graphlib import TopologicalSorter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def modules(directory):
    return {
        ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(
            ".__init__"
        ): path
        for path in (ROOT / directory).rglob("*.py")
    }


def imports(module, path):
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = package.split(".")
            prefix = parts[: len(parts) - node.level + 1] if node.level else []
            target = ".".join(prefix + ([node.module] if node.module else []))
            yield target
            yield from (f"{target}.{alias.name}" for alias in node.names)


@pytest.mark.parametrize(
    ("directory", "forbidden"),
    [
        (
            "spy_game/persistence",
            (
                "handlers",
                "telegram",
                "aiohttp",
                "spy_game.service",
                "spy_game.use_cases",
                "spy_game.webapp",
                "spy_game.database",
            ),
        ),
        (
            "spy_game/use_cases",
            ("handlers", "telegram", "aiohttp", "spy_game.webapp"),
        ),
    ],
)
def test_lower_layers_do_not_import_adapters(directory, forbidden):
    for module, path in modules(directory).items():
        for dependency in imports(module, path):
            assert not dependency.startswith(forbidden), (module, dependency)


def test_spy_modules_have_no_explicit_import_cycles():
    sources = {**modules("spy_game"), **modules("handlers/spy")}
    # Package re-exports are entry points, not implementation dependencies.
    sources = {
        module: path for module, path in sources.items() if path.name != "__init__.py"
    }
    sources["handlers.spy_game"] = ROOT / "handlers/spy_game.py"
    graph = {
        module: {
            dependency
            for dependency in imports(module, path)
            if dependency in sources and dependency != module
        }
        for module, path in sources.items()
    }
    # Raises CycleError with the dependency path if a cycle is introduced.
    assert set(TopologicalSorter(graph).static_order()) == set(sources)


def test_scenario_repositories_do_not_own_transaction_lifecycle():
    sources = list(modules("spy_game/persistence").values())
    sources.append(ROOT / "spy_game/death_mission_repository.py")
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = ast.unparse(node.func.value)
                if target in {"connection", "sqlite3"} or target.endswith(".database"):
                    assert node.func.attr not in {
                        "connect",
                        "commit",
                        "rollback",
                        "executescript",
                        "transaction",
                    }, (path.name, node.lineno, node.func.attr)
