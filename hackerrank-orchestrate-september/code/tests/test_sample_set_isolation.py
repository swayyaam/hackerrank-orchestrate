"""`sample_requests.csv` is a format and style reference, never a label source.

AGENTS.md 6.1: *"`sample_requests.csv` contains public examples with completed
output fields. Use it to understand format and decision style, not as labels for
evaluation requests."*

The file carries the 8 input columns AND the 7 output columns. The loader reads
all 15 because refusing to parse a provided column would be a silent
truncation — but from that point on the seven answers must not reach anything
that decides. Three checks hold that line:

1. the type that carries the answers is confined to its declaration and loader,
2. no module that decides anything even names the sample set, and
3. the decision for a sample request is byte-identical when the answers are
   stripped from the input entirely.

(3) is the one that actually proves it. (1) and (2) make a regression obvious at
the point it is written rather than at the point it is measured.
"""

from __future__ import annotations

import ast

import pytest

from _treewalk import PACKAGE_ROOT, package_modules, parse, rel

#: The 7 output columns. Named from the spec, not from any observed row.
LABEL_FIELDS = {
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
}

#: Modules that legitimately name the labelled type: the contract that declares
#: it, the package export, and the loader that parses the file.
SAMPLE_TYPE_IS_PERMITTED_IN = {
    "bow/contracts/dataset.py",
    "bow/contracts/__init__.py",
    "bow/data/loaders.py",
}

#: Everything that decides, projects, renders or validates. None of it may so
#: much as mention the sample set.
DECIDING_SUBPACKAGES = ("engine", "explain", "format", "perception", "validate", "data")


def _names_in(path) -> set[str]:
    tree = parse(path)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.rsplit(".", 1)[-1])
            if node.asname:
                found.add(node.asname)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
    return found


def test_the_labelled_row_type_is_confined_to_its_declaration_and_loader():
    offenders = []
    for path in package_modules():
        if rel(path).replace("code/", "") in SAMPLE_TYPE_IS_PERMITTED_IN:
            continue
        if "SampleRequest" in _names_in(path):
            offenders.append(rel(path))
    assert not offenders, (
        "SampleRequest carries the published answers; it belongs to the "
        f"contract and the loader only. Found in: {offenders}"
    )


def test_no_deciding_module_names_the_sample_set():
    offenders = []
    for path in package_modules():
        parts = path.relative_to(PACKAGE_ROOT).parts
        if len(parts) < 2 or parts[0] not in DECIDING_SUBPACKAGES:
            continue
        if rel(path).replace("code/", "") in SAMPLE_TYPE_IS_PERMITTED_IN:
            continue  # the loader reads the file; that is its job
        names = _names_in(path)
        for needle in ("SampleRequest", "sample_requests", "sample_requests.csv"):
            if needle in names:
                offenders.append(f"{rel(path)} names {needle!r}")
    assert not offenders, (
        "the sample set selects which requests to run, which is the pipeline's "
        "job; nothing that decides may reach it:\n" + "\n".join(offenders)
    )


def test_no_module_reads_a_label_field_off_a_request():
    """`request.amount_safe_to_pay` and friends must appear nowhere.

    The same seven names are legitimate as output-row keys and as attributes of
    the engine's own `Decision`, so the check is on the *object* being read, not
    on the name.
    """
    offenders = []
    for path in package_modules():
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.Attribute) or node.attr not in LABEL_FIELDS:
                continue
            try:
                owner = ast.unparse(node.value).lower()
            except Exception:  # pragma: no cover - defensive
                continue
            if "request" in owner or "sample" in owner:
                offenders.append(f"{rel(path)}:{node.lineno} reads {ast.unparse(node)}")
    assert not offenders, (
        "a published answer was read off a request object:\n" + "\n".join(offenders)
    )


@pytest.mark.dataset
def test_stripping_the_answers_changes_no_prediction():
    """The decisive check: run the sample set with the labels removed.

    Every `SampleRequest` is replaced by a plain `Request` carrying only the 8
    input columns. If any answer were influencing anything, the output would
    move.
    """
    from bow import paths
    from bow.contracts.dataset import Request
    from bow.data.loaders import load_dataset
    from bow.pipeline import run_pipeline

    loaded = load_dataset(paths.require_dataset_dir())
    with_labels, _ = run_pipeline(
        paths.require_dataset_dir(), targets="sample_requests", perception=False
    )

    stripped = tuple(
        Request(**{name: getattr(row, name) for name in Request.model_fields})
        for row in loaded.dataset.sample_requests
    )
    for row in stripped:
        assert not set(LABEL_FIELDS) & set(type(row).model_fields), (
            "the stripped rows still carry a published answer"
        )

    blinded_dataset = loaded.dataset.model_copy(update={"sample_requests": stripped})

    import bow.data.loaders as loaders_module

    original = loaders_module.load_dataset
    loaders_module.load_dataset = lambda *a, **k: loaded.model_copy(
        update={"dataset": blinded_dataset}
    )
    try:
        blinded, _ = run_pipeline(
            paths.require_dataset_dir(), targets="sample_requests", perception=False
        )
    finally:
        loaders_module.load_dataset = original

    assert blinded.rows == with_labels.rows, (
        "predictions changed when the published answers were removed from the "
        "input, so something is reading them"
    )
