import ast
from pathlib import Path

from reopsai.infrastructure.persistence.base import Base
from reopsai.infrastructure.persistence import models  # noqa: F401 - registers ORM models


ROOT = Path(__file__).resolve().parents[1]

SCAN_PATHS = [
    ROOT / "app.py",
    ROOT / "asgi.py",
    ROOT / "reopsai",
    ROOT / "scripts",
    ROOT / "alembic",
    ROOT / "tests",
]

ALLOWLIST = {
    ROOT / "tests" / "test_b2b_access_helper.py",
    ROOT / "tests" / "test_dev_evaluator_service.py",
    ROOT / "tests" / "test_persistence_refactor_guards.py",
    ROOT / "tests" / "test_shared_helper_absorption.py",
    ROOT / "tests" / "test_usage_metering.py",
}

FORBIDDEN_EXACT_IMPORTS = {
    "db",
    "db.base",
    "db.engine",
    "db.models.core",
    "db.repositories",
    "services",
    "utils",
}

FORBIDDEN_PREFIX_IMPORTS = (
    "db.repositories.",
    "services.",
    "utils.",
)

REQUIRED_TABLES = {
    "users",
    "projects",
    "studies",
    "artifacts",
    "llm_usage_events",
    "llm_usage_daily_aggregates",
}


def _iter_python_files():
    for path in SCAN_PATHS:
        if path.is_file():
            yield path
            continue
        if path.is_dir():
            yield from path.rglob("*.py")


def _is_forbidden_import(module_name):
    return module_name in FORBIDDEN_EXACT_IMPORTS or module_name.startswith(FORBIDDEN_PREFIX_IMPORTS)


def _format_violation(path, lineno, value, reason):
    relative_path = path.relative_to(ROOT)
    return f"{relative_path}:{lineno}: {reason}: {value}"


def _legacy_from_import_targets(node):
    module = node.module or ""
    names = [alias.name for alias in node.names]

    if module == "db":
        for name in names:
            if name in {"base", "engine"}:
                yield f"db.{name}"
        return

    if module == "db.models":
        for name in names:
            if name == "core":
                yield "db.models.core"
        return

    if module in {"services", "utils"}:
        for name in names:
            yield f"{module}.{name}"
        return

    yield module


def test_runtime_code_does_not_import_legacy_root_wrappers():
    violations = []

    for path in sorted(_iter_python_files()):
        if path in ALLOWLIST:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden_import(alias.name):
                        violations.append(
                            _format_violation(path, node.lineno, alias.name, "legacy import")
                        )
            elif isinstance(node, ast.ImportFrom):
                for target in _legacy_from_import_targets(node):
                    if _is_forbidden_import(target):
                        violations.append(
                            _format_violation(path, node.lineno, target, "legacy from-import")
                        )
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _is_forbidden_import(node.value):
                    violations.append(
                        _format_violation(path, node.lineno, node.value, "legacy dynamic import string")
                    )

    assert not violations, "Runtime code must use reopsai.* source-of-truth imports:\n" + "\n".join(
        violations
    )


def test_persistence_model_metadata_registers_core_tables():
    missing_tables = sorted(REQUIRED_TABLES - set(Base.metadata.tables))

    assert not missing_tables
