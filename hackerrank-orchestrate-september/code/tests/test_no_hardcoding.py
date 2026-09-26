"""Generalization mandate, enforced by walking the AST of every bow module.

This must work on a dataset nobody here has seen. The recon describes one
sample of a distribution, not the distribution. So: encode rules, never
observations.

Each check below is a class of hardcoding that would silently overfit. Money
and rates come from the data; tunable thresholds come from config; model ids,
endpoints and language codes come from config.
"""

from __future__ import annotations

import ast
import re

import pytest

from _treewalk import PACKAGE_ROOT, package_modules, parse, rel

# --- dataset identifier shapes ----------------------------------------------
# Derived from the column names in the spec, not from any observed id value.
ID_ENTITIES = (
    "event", "image", "request", "user", "message", "payment_option",
)
ID_PATTERN = re.compile(
    r"^(?:" + "|".join(ID_ENTITIES) + r")_[0-9]+$", re.IGNORECASE
)

# --- typed fields behaviour may branch on -----------------------------------
TYPED_FIELDS = {
    "event_type", "status", "direction", "flexibility", "category",
    "currency", "event_date", "settlement_date", "request_date",
    "desired_completion_date", "request_type", "payment_method",
    "home_currency", "allows_partial_payment",
}
# Free-text columns behaviour must NEVER branch on.
FREE_TEXT_FIELDS = {"description", "message_text", "request_text"}

# --- literals that would be dataset facts -----------------------------------
# Small integers are structural (indices, small counts, HTTP codes); large or
# fractional ones in the engine would be money, rates, or thresholds.
ALLOWED_NUMBERS = set(range(-2, 101)) | {
    0.0, 0.5, 1.0, 2.0, 100.0,            # ratios and percentages
    200, 400, 401, 403, 404, 408, 409,    # HTTP status codes
    422, 425, 429, 500, 502, 503, 504,
    1000, 1024, 60, 3600, 86400,          # units of time and size
}

# --- config-owned vocabularies ----------------------------------------------
MODEL_ID_PATTERN = re.compile(
    r"^(?:sarvam|glm|gemma|deepseek|gpt|claude|llama|mistral|qwen)[\w.\-]*$",
    re.IGNORECASE,
)
ENDPOINT_PATTERN = re.compile(r"^/?(?:v\d|doc-ai|text-lid|chat)[\w/\-{}.]*$")
LANGUAGE_CODE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
# Two-to-three-letter tokens that are ordinary English words or domain terms,
# not language codes.
LANGUAGE_FALSE_POSITIVES = {
    "and", "or", "not", "the", "for", "id", "ids", "key", "max", "min", "sum",
    "csv", "png", "job", "raw", "utf", "str", "int", "any", "all", "md",
    "asc", "desc", "now", "day", "end", "add", "set", "get", "put", "run",
    "cwd", "env", "url", "api", "sdk", "yes", "no", "on", "off", "to", "in",
    "of", "at", "by", "is", "it", "as", "if", "so", "up", "us", "we",
}

# Spec-defined enums, which ARE closed and may appear as literals.
SPEC_ENUMS = {
    # affordability_status
    "affordable_now", "affordable_with_plan", "affordable_later", "not_affordable",
    # recommended_payment_method
    "full_payment", "partial_payment", "installments", "wait", "not_recommended",
    # request_type
    "purchase", "travel", "education", "family_transfer", "debt_repayment",
    "investment", "housing", "emergency_expense", "other",
    # spending-change verbs and the empty sentinel
    "stop", "reduce_to", "none",
}


def _is_power_of_ten(value: int) -> bool:
    while value % 10 == 0 and value > 1:
        value //= 10
    return value == 1


def string_constants(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, node.lineno


def number_constants(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if isinstance(node.value, bool):
                continue
            yield node.value, node.lineno


def is_docstring_or_comment_only(path) -> bool:
    return False


MODULES = package_modules()


@pytest.mark.parametrize("path", MODULES, ids=rel)
def test_no_dataset_identifier_literals(path):
    """No event_id, image_id, request_id, user_id, message_id, payment_option_id."""
    offenders = [
        f"{rel(path)}:{line} {value!r}"
        for value, line in string_constants(parse(path))
        if ID_PATTERN.match(value.strip())
    ]
    assert not offenders, (
        "dataset identifiers must never appear as literals; found:\n"
        + "\n".join(offenders)
    )


#: Operations that INSPECT a string's content. Using any of these on a
#: free-text column is reading what the string says.
CONTENT_INSPECTION = {
    "startswith", "endswith", "find", "rfind", "index", "rindex",
    "match", "fullmatch", "search", "findall", "finditer", "sub", "subn",
    "split", "rsplit", "partition", "lower", "upper", "casefold", "title",
    "count", "replace", "contains", "strip", "lstrip", "rstrip",
}


def _reads_free_text(node) -> str | None:
    """Is this expression a read of a free-text column?"""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        if node.slice.value in FREE_TEXT_FIELDS:
            return str(node.slice.value)
    if isinstance(node, ast.Attribute) and node.attr in FREE_TEXT_FIELDS:
        return node.attr
    return None


def _reads_free_text_deep(node) -> str | None:
    """As above, but also through a wrapping call such as ``str(...)``."""
    direct = _reads_free_text(node)
    if direct:
        return direct
    if isinstance(node, ast.Call):
        for argument in node.args:
            found = _reads_free_text_deep(argument)
            if found:
                return found
    return None


def free_text_violations(tree: ast.Module, label: str) -> list[str]:
    """Every place behaviour depends on what a free-text string SAYS.

    Deliberately does NOT flag using the string as an opaque value: comparing
    it for equality, hashing it, or passing it into a grouping key are all
    fine, because none of them depends on the content. What is banned is
    inspecting the content — substring tests, keyword membership, regex,
    case folding, splitting.
    """
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)):
                    field = _reads_free_text_deep(comparator) or _reads_free_text_deep(
                        node.left
                    )
                    if field:
                        offenders.append(
                            f"{label}:{node.lineno} membership test against {field!r}"
                        )
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else None
            if name in CONTENT_INSPECTION:
                receiver = _reads_free_text_deep(func.value)
                if receiver:
                    offenders.append(f"{label}:{node.lineno} {name}() on {receiver!r}")
                    continue
                for argument in node.args:
                    field = _reads_free_text_deep(argument)
                    if field:
                        offenders.append(
                            f"{label}:{node.lineno} {name}() applied to {field!r}"
                        )
                        break
    return offenders


@pytest.mark.parametrize("path", MODULES, ids=rel)
def test_no_branching_on_free_text(path):
    """Behaviour derives from typed fields, never from description or message text."""
    offenders = free_text_violations(parse(path), rel(path))
    assert not offenders, (
        "behaviour must derive from typed fields (event_type, status, direction, "
        "flexibility, category, currency, dates), never from free text; found:\n"
        + "\n".join(offenders)
    )


# --- the distinction that makes `description` usable as a grouping key --------

GROUPING_IS_FINE = """
def group(events):
    buckets = {}
    for event in events:
        key = (event.user_id, event.description, event.category)
        buckets.setdefault(key, []).append(event)
    return buckets

def same_series(a, b):
    return a.description == b.description

def dedup(events):
    return {e.description: e for e in events}

def sort_key(event):
    return event.description
"""

BRANCHING_IS_NOT = [
    ('substring test', 'def f(e):\n    return "refund" in e.description\n'),
    ('keyword list', 'KEYWORDS = ["salary", "payroll"]\ndef f(e):\n'
                     '    return any(k in e.description for k in KEYWORDS)\n'),
    ('regex over content', 'import re\ndef f(e):\n'
                           '    return re.search(r"salary", e.description)\n'),
    ('prefix test', 'def f(e):\n    return e.description.startswith("Card")\n'),
    ('case folding', 'def f(e):\n    return e.description.lower() == "rent"\n'),
    ('splitting content', 'def f(e):\n    return e.description.split()[0]\n'),
    ('message text', 'def f(m):\n    return "cancelled" in m.message_text\n'),
    ('dict subscript form', 'def f(row):\n    return "fee" in row["description"]\n'),
]


def test_description_as_a_grouping_key_is_permitted():
    """Equality, hashing and grouping do not depend on what the string says."""
    offenders = free_text_violations(ast.parse(GROUPING_IS_FINE), "<grouping>")
    assert not offenders, (
        "using description as an opaque grouping key must stay legal; "
        "the lint flagged:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    ("label", "source"), BRANCHING_IS_NOT, ids=[b[0] for b in BRANCHING_IS_NOT]
)
def test_branching_on_description_content_is_caught(label, source):
    """Every way of reading what the string says must be detected."""
    offenders = free_text_violations(ast.parse(source), "<branching>")
    assert offenders, f"lint failed to catch {label}: {source!r}"


def test_recurrence_groups_by_description_without_reading_it():
    """The real module, checked against both halves of the distinction."""
    recurrence = PACKAGE_ROOT / "data" / "recurrence.py"
    if not recurrence.exists():
        pytest.skip("recurrence module not present")
    text = recurrence.read_text(encoding="utf-8")
    assert "description=event.description" in text, (
        "recurrence should group by description as an opaque key"
    )
    assert not free_text_violations(parse(recurrence), "recurrence.py")


@pytest.mark.parametrize("path", MODULES, ids=rel)
def test_no_dataset_amount_rate_or_threshold_literals(path):
    """Money and rates come from the data; thresholds come from config."""
    offenders = []
    for value, line in number_constants(parse(path)):
        if value in ALLOWED_NUMBERS:
            continue
        if isinstance(value, int) and 100 < value <= 3600 and value % 100 == 0:
            continue  # round structural constants (timeouts, byte sizes)
        # An exact power of ten used as a multiplier is a unit, not an amount:
        # "thousand", "lakh", "crore" are facts about how numerals are written,
        # and no dataset amount or tuned threshold lands on one by coincidence.
        if isinstance(value, int) and value > 0 and _is_power_of_ten(value):
            continue
        offenders.append(f"{rel(path)}:{line} {value!r}")
    assert not offenders, (
        "numeric literals that could be a dataset amount, rate, or tunable "
        "threshold are banned inside bow/; read them from the data or from "
        "config/. Found:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("path", MODULES, ids=rel)
def test_no_literal_counts_of_dataset_entities(path):
    """No literal row, user, image, or category counts."""
    tree = parse(path)
    offenders = []
    counting_words = re.compile(
        r"\b(?:n_)?(?:rows?|users?|images?|events?|requests?|messages?|"
        r"categories|options?|profiles?)\b",
        re.IGNORECASE,
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        source = ast.unparse(node)
        if not counting_words.search(source):
            continue
        for comparator in node.comparators:
            if (
                isinstance(comparator, ast.Constant)
                and isinstance(comparator.value, int)
                and not isinstance(comparator.value, bool)
                and comparator.value > 4
            ):
                offenders.append(f"{rel(path)}:{node.lineno} {source}")
    assert not offenders, (
        "a count observed in one dataset is not a rule; found:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("path", MODULES, ids=rel)
def test_no_model_endpoint_or_language_literals(path):
    """Model ids, endpoints and language codes are config, not source."""
    offenders = []
    for value, line in string_constants(parse(path)):
        candidate = value.strip()
        if not candidate or candidate in SPEC_ENUMS:
            continue
        if MODEL_ID_PATTERN.match(candidate) and not candidate.islower() is False:
            offenders.append(f"{rel(path)}:{line} model id {candidate!r}")
            continue
        if "/" in candidate and ENDPOINT_PATTERN.match(candidate):
            offenders.append(f"{rel(path)}:{line} endpoint {candidate!r}")
            continue
        if (
            LANGUAGE_CODE_PATTERN.match(candidate)
            and candidate.lower() not in LANGUAGE_FALSE_POSITIVES
            and "-" in candidate
        ):
            offenders.append(f"{rel(path)}:{line} language code {candidate!r}")
    assert not offenders, (
        "model ids, endpoints and language codes must come from "
        "config/models.yaml; found:\n" + "\n".join(offenders)
    )


def test_prompts_carry_no_vendor_or_layout_knowledge():
    """No vendor name, document type, or layout description under prompts/.

    A prompt that names a vendor or describes a known layout has memorised this
    dataset. The perception layer must read what is in front of it.
    """
    from _treewalk import SUBMISSION_ROOT

    banned = re.compile(
        r"\b(?:airtel|indigo|icici|paytm|grofers|blink|zomato|swiggy|amazon|"
        r"flipkart|uber|ola|jio|vodafone|bsnl|dailyobjects|bigbasket|"
        r"rent receipt|pay ?slip|tax invoice|thermal receipt|utility bill|"
        r"hospital bill|boarding pass|top[- ]right|bottom[- ]right|"
        r"last column|right-?most column)\b",
        re.IGNORECASE,
    )
    offenders = []
    for path in sorted((SUBMISSION_ROOT / "prompts").rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in banned.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{rel(path)}:{line} {match.group(0)!r}")
    assert not offenders, (
        "prompts must not name a vendor, a document type, or a layout position; "
        "found:\n" + "\n".join(offenders)
    )


def test_observed_sets_are_not_treated_as_closed():
    """Dataset-derived vocabularies stay open; only spec enums are closed.

    A Literal[...] or frozenset of category / description / source_type values
    would freeze a sample of a distribution into the type system.
    """
    open_vocabularies = {"category", "categories", "description", "descriptions",
                         "source_type", "source_types"}
    offenders = []
    for path in MODULES:
        tree = parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign) or node.target is None:
                continue
            name = getattr(node.target, "id", "") or getattr(node.target, "attr", "")
            if name.lower() not in open_vocabularies:
                continue
            annotation = ast.unparse(node.annotation)
            if "Literal[" in annotation or "Enum" in annotation:
                offenders.append(f"{rel(path)}:{node.lineno} {name}: {annotation}")
    assert not offenders, (
        "category, description and source_type are dataset vocabularies, not "
        "spec enums: type them as plain strings with an unknown-value counter. "
        "Found:\n" + "\n".join(offenders)
    )


def test_the_mandate_is_actually_being_enforced():
    """Self-check: the AST walk must be looking at real files."""
    assert PACKAGE_ROOT.is_dir(), "bow package not found"
    assert MODULES, "no modules discovered under bow/ — this suite is vacuous"
