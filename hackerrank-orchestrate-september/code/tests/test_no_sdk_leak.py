"""Only bow/providers/ may import a vendor SDK.

Every other package reaches a model through the provider layer, which owns the
rate limiter, the job poller, retries, and the usage meter.
"""

from __future__ import annotations

from _treewalk import PACKAGE_ROOT, imported_names, package_modules, parse, rel

SDK_ROOTS = {"sarvamai", "openai", "anthropic"}


def test_sdk_imports_are_confined_to_providers():
    violations = []
    for path in package_modules():
        parts = path.relative_to(PACKAGE_ROOT).parts
        in_providers = len(parts) > 1 and parts[0] == "providers"
        for module, lineno in imported_names(parse(path)):
            root = module.split(".", 1)[0]
            if root in SDK_ROOTS and not in_providers:
                violations.append(f"{rel(path)}:{lineno} imports {module!r}")
    assert not violations, (
        "vendor SDK imports are confined to bow/providers/; found:\n"
        + "\n".join(violations)
    )


def test_single_vendor():
    """One provider. An `anthropic` import anywhere is a design violation."""
    violations = []
    for path in package_modules():
        for module, lineno in imported_names(parse(path)):
            if module.split(".", 1)[0] == "anthropic":
                violations.append(f"{rel(path)}:{lineno} imports {module!r}")
    assert not violations, (
        "this submission is single-vendor (Sarvam); found:\n" + "\n".join(violations)
    )
