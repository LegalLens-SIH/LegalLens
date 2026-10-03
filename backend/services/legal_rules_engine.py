from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from typing import Any


DEFAULT_RULESET_NAME = "legal_metrology_packaged_commodities_rules.json"
IMPLEMENTED_OPERATORS = {
    "eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte",
    "exists", "not_exists", "matches", "contains", "between",
    "contains_any", "not_contains_any",
}
COMBINATORS = {"all", "any", "not"}
_ROOT_KEYS = {
    "meta", "data_model", "applicability", "definitions", "rules",
    "inspection_and_quantity_testing", "lookup_tables", "calculations",
    "penalties", "transitional_note",
}
_DERIVED_LOOKUP_TABLES = {
    "quantity_basis_by_commodity_state_or_fourth_schedule": "fourth_schedule_quantity_expression",
}
_RULE_KEYS = {
    "id", "rule_ref", "chapter", "category", "severity", "title", "description",
    "applies_when", "check", "notes", "exemptions", "definitions", "consequence",
    "applies_to", "accepted_formats_examples", "marketplace_liability",
    "required_listing_fields", "blocked_terms_examples", "exempt_characters",
    "deadline_days_from_commencement", "fees_inr",
}
_CONDITION_KEYS = {"field", "op", "value", "value_from", "note", "then"}
_FIELD_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_VALUE_FROM_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_MULTIPLY_EXPRESSION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[x*]\s*([A-Za-z_][A-Za-z0-9_.]*)\s*$", re.I)


class RulesetValidationError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedRuleset:
    data: dict[str, Any]
    path: Path
    sha256: str
    validation_warnings: tuple[str, ...] = ()

    @property
    def rules(self) -> list[dict[str, Any]]:
        return self.data["rules"]

    @property
    def rule_count(self) -> int:
        return len(self.rules)

    @property
    def rules_with_checks(self) -> int:
        return sum("check" in rule for rule in self.rules)

    @property
    def rules_without_checks(self) -> int:
        return self.rule_count - self.rules_with_checks


def default_ruleset_path() -> Path:
    configured_path = os.getenv("LEGAL_METROLOGY_RULESET_PATH", "").strip()
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        candidate = parent / DEFAULT_RULESET_NAME
        if candidate.is_file():
            return candidate
    parents = module_path.parents
    fallback_root = parents[min(2, len(parents) - 1)]
    return fallback_root / DEFAULT_RULESET_NAME


def _validate_condition(expression: Any, location: str, operators: set[str]) -> None:
    if not isinstance(expression, dict) or not expression:
        raise RulesetValidationError(f"{location} must be a non-empty condition object")
    combinators = COMBINATORS.intersection(expression)
    if combinators:
        if len(combinators) != 1 or len(expression) != 1:
            raise RulesetValidationError(f"{location} must contain exactly one combinator")
        combinator = next(iter(combinators))
        value = expression[combinator]
        if combinator == "not":
            _validate_condition(value, f"{location}.not", operators)
            return
        if not isinstance(value, list) or not value:
            raise RulesetValidationError(f"{location}.{combinator} must be a non-empty list")
        for index, child in enumerate(value):
            _validate_condition(child, f"{location}.{combinator}[{index}]", operators)
        return

    unknown_keys = set(expression) - _CONDITION_KEYS
    if unknown_keys:
        raise RulesetValidationError(f"{location} has unsupported condition keys: {sorted(unknown_keys)}")
    field_name = expression.get("field")
    operator = expression.get("op")
    if not isinstance(field_name, str) or not _FIELD_PATH.fullmatch(field_name):
        raise RulesetValidationError(f"{location}.field must be a dotted fact path")
    if not isinstance(operator, str) or operator not in operators:
        raise RulesetValidationError(f"{location}.op is unsupported: {operator!r}")
    has_value = "value" in expression
    has_reference = "value_from" in expression
    if has_value and has_reference:
        raise RulesetValidationError(f"{location} cannot define both value and value_from")
    if operator in {"exists", "not_exists"}:
        if has_value or has_reference:
            raise RulesetValidationError(f"{location}.{operator} does not take a comparison value")
    elif not has_value and not has_reference:
        raise RulesetValidationError(f"{location}.{operator} requires value or value_from")
    if has_reference:
        reference = expression["value_from"]
        if not isinstance(reference, str) or not (
            _VALUE_FROM_PATH.fullmatch(reference) or _MULTIPLY_EXPRESSION.fullmatch(reference)
        ):
            raise RulesetValidationError(f"{location}.value_from is not a supported path/expression")
    if operator == "matches" and has_value:
        pattern = expression["value"]
        if not isinstance(pattern, str):
            raise RulesetValidationError(f"{location}.matches requires a string regex")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RulesetValidationError(f"{location}.matches has an invalid regex") from exc
    if operator in {"in", "not_in", "contains_any", "not_contains_any"} and has_value:
        if not isinstance(expression["value"], (list, tuple, str)):
            raise RulesetValidationError(f"{location}.{operator} requires a sequence value")
    if operator == "between" and has_value:
        bounds = expression["value"]
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise RulesetValidationError(f"{location}.between requires exactly two bounds")


def _find_lookup_references(node: Any, references: set[str]) -> None:
    if isinstance(node, dict):
        reference = node.get("value_from")
        if isinstance(reference, str) and reference.startswith("lookup_tables."):
            references.add(reference.partition(".")[2])
        for value in node.values():
            _find_lookup_references(value, references)
    elif isinstance(node, list):
        for value in node:
            _find_lookup_references(value, references)


def validate_ruleset(data: Any, path: Path) -> LoadedRuleset:
    if not isinstance(data, dict):
        raise RulesetValidationError("The ruleset root must be an object")
    unknown_root_keys = set(data) - _ROOT_KEYS
    missing_root_keys = _ROOT_KEYS - set(data)
    if unknown_root_keys or missing_root_keys:
        raise RulesetValidationError(
            f"Ruleset root keys invalid; missing={sorted(missing_root_keys)}, unknown={sorted(unknown_root_keys)}"
        )
    meta = data.get("meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("schema_version"), str):
        raise RulesetValidationError("meta.schema_version is required")
    condition_dsl = meta.get("condition_dsl")
    if not isinstance(condition_dsl, dict) or not isinstance(condition_dsl.get("operators"), list):
        raise RulesetValidationError("meta.condition_dsl.operators is required")
    declared_operators = set(condition_dsl["operators"])
    unsupported_declared = declared_operators - IMPLEMENTED_OPERATORS
    if unsupported_declared:
        raise RulesetValidationError(f"Ruleset declares unsupported operators: {sorted(unsupported_declared)}")
    operators = declared_operators | IMPLEMENTED_OPERATORS
    severity_levels = meta.get("severity_levels")
    if not isinstance(severity_levels, list) or not severity_levels:
        raise RulesetValidationError("meta.severity_levels must be a non-empty list")
    if not isinstance(data.get("data_model"), dict) or not isinstance(data.get("applicability"), dict):
        raise RulesetValidationError("data_model and applicability must be objects")
    if not isinstance(data.get("lookup_tables"), dict) or not isinstance(data.get("calculations"), dict):
        raise RulesetValidationError("lookup_tables and calculations must be objects")
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise RulesetValidationError("rules must be a non-empty list")

    seen_ids: set[str] = set()
    for index, rule in enumerate(rules):
        location = f"rules[{index}]"
        if not isinstance(rule, dict):
            raise RulesetValidationError(f"{location} must be an object")
        unknown_rule_keys = set(rule) - _RULE_KEYS
        if unknown_rule_keys:
            raise RulesetValidationError(f"{location} has unsupported keys: {sorted(unknown_rule_keys)}")
        for required in ("id", "rule_ref", "chapter", "category", "severity", "title"):
            if not isinstance(rule.get(required), str) or not rule[required].strip():
                raise RulesetValidationError(f"{location}.{required} must be a non-empty string")
        rule_id = rule["id"]
        if rule_id in seen_ids:
            raise RulesetValidationError(f"Duplicate rule id: {rule_id}")
        seen_ids.add(rule_id)
        if rule["severity"] not in severity_levels:
            raise RulesetValidationError(f"{location}.severity is not declared in meta.severity_levels")
        for condition_key in ("applies_when", "check"):
            if condition_key in rule:
                _validate_condition(rule[condition_key], f"{location}.{condition_key}", operators)

    applicability = data["applicability"]
    for gate_name, gate in applicability.items():
        if not isinstance(gate, dict):
            raise RulesetValidationError(f"applicability.{gate_name} must be an object")
        for exemption_index, exemption in enumerate(gate.get("applies_unless_any", gate.get("exemptions", []))):
            if not isinstance(exemption, dict) or not isinstance(exemption.get("condition"), dict):
                raise RulesetValidationError(f"applicability.{gate_name} exemption {exemption_index} needs a condition")
            _validate_condition(exemption["condition"], f"applicability.{gate_name}[{exemption_index}].condition", operators)
            for condition_key in ("requires", "except_when"):
                if condition_key in exemption:
                    _validate_condition(exemption[condition_key], f"applicability.{gate_name}[{exemption_index}].{condition_key}", operators)
            for requirement_index, requirement in enumerate(exemption.get("conditional_requirements", [])):
                _validate_condition(requirement, f"applicability.{gate_name}[{exemption_index}].conditional_requirements[{requirement_index}]", operators)

    references: set[str] = set()
    used_operators: set[str] = set()
    used_namespaces: set[str] = set()
    for rule in rules:
        _find_lookup_references(rule, references)
        _collect_condition_schema(rule, used_operators, used_namespaces)
    _collect_condition_schema(applicability, used_operators, used_namespaces)
    missing_lookup_tables = sorted(
        reference for reference in references
        if reference not in data["lookup_tables"]
        and _DERIVED_LOOKUP_TABLES.get(reference) not in data["lookup_tables"]
    )
    declared_namespaces = {
        match.group(1)
        for namespace in condition_dsl.get("field_namespaces", [])
        if isinstance(namespace, str)
        for match in [re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", namespace)]
        if match
    }
    warnings = [f"Unresolved source lookup table: lookup_tables.{reference}" for reference in missing_lookup_tables]
    for reference in sorted(references.intersection(_DERIVED_LOOKUP_TABLES)):
        warnings.append(
            f"Source lookup lookup_tables.{reference} is absent; its resolver derives candidates from "
            f"lookup_tables.{_DERIVED_LOOKUP_TABLES[reference]} and returns UNKNOWN when no row matches."
        )
    omitted_operators = sorted(used_operators - declared_operators)
    if omitted_operators:
        warnings.append(f"DSL metadata omits operators used by rules: {', '.join(omitted_operators)}")
    omitted_namespaces = sorted(used_namespaces - declared_namespaces)
    if omitted_namespaces:
        warnings.append(f"DSL metadata omits fact namespaces used by rules: {', '.join(omitted_namespaces)}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return LoadedRuleset(data=data, path=path, sha256=digest, validation_warnings=tuple(warnings))


def _collect_condition_schema(node: Any, operators: set[str], namespaces: set[str]) -> None:
    if isinstance(node, dict):
        field_name = node.get("field")
        if isinstance(field_name, str) and isinstance(node.get("op"), str):
            operators.add(node["op"])
            namespaces.add(field_name.split(".", 1)[0])
        for value in node.values():
            _collect_condition_schema(value, operators, namespaces)
    elif isinstance(node, list):
        for value in node:
            _collect_condition_schema(value, operators, namespaces)


def load_legal_ruleset(path: str | Path | None = None) -> LoadedRuleset:
    source_path = Path(path).expanduser().resolve() if path is not None else default_ruleset_path().resolve()
    try:
        source_bytes = source_path.read_bytes()
        data = json.loads(source_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RulesetValidationError(f"Could not read JSON ruleset: {source_path}") from exc
    return validate_ruleset(data, source_path)


class Truth(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class _Unknown:
    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN = _Unknown()


def _to_json_value(value: Any) -> Any:
    if value is UNKNOWN:
        return "UNKNOWN"
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _tri_not(value: Truth) -> Truth:
    if value is Truth.TRUE:
        return Truth.FALSE
    if value is Truth.FALSE:
        return Truth.TRUE
    return Truth.UNKNOWN


def _tri_all(values: list[Truth]) -> Truth:
    if any(value is Truth.FALSE for value in values):
        return Truth.FALSE
    if any(value is Truth.UNKNOWN for value in values):
        return Truth.UNKNOWN
    return Truth.TRUE


def _tri_any(values: list[Truth]) -> Truth:
    if any(value is Truth.TRUE for value in values):
        return Truth.TRUE
    if any(value is Truth.UNKNOWN for value in values):
        return Truth.UNKNOWN
    return Truth.FALSE


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return float(left) == float(right)
    return left == right


def _contains(container: Any, value: Any) -> bool:
    if isinstance(container, str) and isinstance(value, str):
        return value.casefold() in container.casefold()
    if isinstance(container, (list, tuple, set)):
        return any(_equal(item, value) for item in container)
    if isinstance(container, dict):
        return value in container
    return False


def _contains_any(actual: Any, expected: Any) -> bool:
    if not isinstance(expected, (list, tuple, set)):
        return False
    if isinstance(actual, str):
        lowered = actual.casefold()
        return any(isinstance(item, str) and item.casefold() in lowered for item in expected)
    if isinstance(actual, (list, tuple, set)):
        return any(_contains(actual, item) for item in expected)
    return False


def _apply_operator(actual: Any, operator: str, expected: Any = None) -> Truth:
    if operator in {"exists", "not_exists"}:
        if actual is UNKNOWN:
            return Truth.UNKNOWN
        exists = actual is not None
        result = exists if operator == "exists" else not exists
        return Truth.TRUE if result else Truth.FALSE
    if actual is UNKNOWN or expected is UNKNOWN:
        return Truth.UNKNOWN
    try:
        if operator in {"eq", "ne"}:
            if isinstance(expected, (list, tuple, set)):
                result = any(_equal(actual, option) for option in expected)
            else:
                result = _equal(actual, expected)
            if operator == "ne":
                result = not result
        elif operator in {"gt", "gte", "lt", "lte"}:
            left_number = _as_number(actual)
            right_number = _as_number(expected)
            if left_number is None or right_number is None:
                return Truth.UNKNOWN
            result = {
                "gt": left_number > right_number,
                "gte": left_number >= right_number,
                "lt": left_number < right_number,
                "lte": left_number <= right_number,
            }[operator]
        elif operator in {"in", "not_in"}:
            if isinstance(expected, (list, tuple, set)):
                result = any(_equal(actual, item) for item in expected)
            else:
                return Truth.UNKNOWN
            if operator == "not_in":
                result = not result
        elif operator == "matches":
            if not isinstance(actual, str) or not isinstance(expected, str):
                return Truth.UNKNOWN
            result = re.search(expected, actual) is not None
        elif operator == "contains":
            result = _contains(actual, expected)
        elif operator == "contains_any":
            result = _contains_any(actual, expected)
        elif operator == "not_contains_any":
            result = not _contains_any(actual, expected)
        elif operator == "between":
            if not isinstance(expected, (list, tuple)) or len(expected) != 2:
                return Truth.UNKNOWN
            value_number = _as_number(actual)
            lower = _as_number(expected[0])
            upper = _as_number(expected[1])
            if value_number is None or lower is None or upper is None:
                return Truth.UNKNOWN
            result = lower <= value_number <= upper
        else:
            return Truth.UNKNOWN
    except (TypeError, ValueError, re.error):
        return Truth.UNKNOWN
    return Truth.TRUE if result else Truth.FALSE


def _normalize_unit(unit: Any) -> str | None:
    if not isinstance(unit, str):
        return None
    token = re.sub(r"[.\s]", "", unit.casefold())
    aliases = {
        "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
        "gm": "g", "gms": "g", "gram": "g", "grams": "g",
        "mls": "ml", "millilitre": "ml", "millilitres": "ml", "milliliter": "ml", "milliliters": "ml",
        "litre": "l", "litres": "l", "liter": "l", "liters": "l",
        "cms": "cm", "centimetre": "cm", "centimetres": "cm", "centimeter": "cm", "centimeters": "cm",
        "metre": "m", "metres": "m", "meter": "m", "meters": "m",
    }
    return aliases.get(token, token)


def _quantity_in_canonical_unit(package: dict[str, Any], basis: str) -> float | None:
    direct_names = {
        "mass": "net_quantity_in_kg",
        "volume": "net_quantity_in_l",
        "length": "net_quantity_in_m",
        "area": "net_quantity_in_m2",
        "number": "net_quantity_value",
    }
    direct = _as_number(package.get(direct_names[basis])) if basis in direct_names else None
    if direct is not None:
        return direct
    amount = _as_number(package.get("net_quantity_value"))
    unit = _normalize_unit(package.get("net_quantity_unit"))
    if amount is None or unit is None:
        return None
    factors = {
        "mass": {"g": 0.001, "kg": 1.0},
        "volume": {"ml": 0.001, "cm3": 0.001, "l": 1.0, "dm3": 1.0, "m3": 1000.0},
        "length": {"cm": 0.01, "m": 1.0},
        "area": {"dm2": 0.01, "m2": 1.0},
        "number": {"number": 1.0, "unit": 1.0, "piece": 1.0, "pair": 1.0, "set": 1.0},
    }
    factor = factors.get(basis, {}).get(unit)
    return amount * factor if factor is not None else None


def _resolve_lookup_table(
    table_name: str,
    ruleset: LoadedRuleset,
    facts: dict[str, Any],
    calculations: dict[str, Any],
) -> tuple[Any, str]:
    table = ruleset.data["lookup_tables"].get(table_name)
    package = facts.get("package", {})
    if table is None and table_name in _DERIVED_LOOKUP_TABLES:
        source_table_name = _DERIVED_LOOKUP_TABLES[table_name]
        source_table = ruleset.data["lookup_tables"].get(source_table_name, {})
        commodity = package.get("commodity", UNKNOWN)
        if not isinstance(commodity, str) or not commodity.strip():
            return UNKNOWN, "Commodity is required to query the Fourth Schedule quantity-basis table"
        matching_rows = [
            row for row in source_table.get("rows", [])
            if isinstance(row.get("commodity"), str)
            and row["commodity"].casefold() in commodity.casefold()
        ]
        if matching_rows:
            longest_match = max(len(row["commodity"]) for row in matching_rows)
            matching_rows = [row for row in matching_rows if len(row["commodity"]) == longest_match]
        if len(matching_rows) > 1:
            return UNKNOWN, "Commodity matches multiple Fourth Schedule rows"
        bases: list[str] = []
        for row in matching_rows:
            declarations = row.get("declared_in", [])
            for declaration in declarations if isinstance(declarations, list) else [declarations]:
                text = str(declaration).casefold()
                for token, basis in (("weight", "mass"), ("volume", "volume"), ("length", "length"), ("area", "area"), ("number", "number")):
                    if token in text and basis not in bases:
                        bases.append(basis)
        if bases:
            return bases, f"Resolved via lookup_tables.{source_table_name} rows for the supplied commodity"
        return UNKNOWN, "No matching Fourth Schedule commodity row was found"
    if table_name == "pdp_font_height_table_I" and isinstance(table, dict):
        area = _as_number(package.get("pdp_area_cm2"))
        if area is None:
            return UNKNOWN, "PDP area is unknown"
        for row in table.get("rows", []):
            interval = row.get("pdp_area_cm2", {})
            lower = interval.get("min_exclusive")
            upper = interval.get("max_inclusive")
            if (lower is None or area > float(lower)) and (upper is None or area <= float(upper)):
                normal = row.get("min_height_normal_mm")
                blown = row.get("min_height_blown_formed_or_molded_mm")
                molded_flag = package.get("is_blown_formed_or_molded_on_container", UNKNOWN)
                if molded_flag is UNKNOWN:
                    if normal == blown:
                        return normal, "Selected from the JSON PDP font-height table"
                    return UNKNOWN, "Container formation type is unknown"
                return (blown if molded_flag else normal), "Selected from the JSON PDP font-height table"
        return UNKNOWN, "PDP area falls outside the JSON table"

    if table_name == "unit_sale_price_units" and isinstance(table, dict):
        basis = package.get("net_quantity_basis", UNKNOWN)
        if basis is UNKNOWN:
            return UNKNOWN, "Net-quantity basis is unknown"
        if basis == "number":
            row = next((item for item in table.get("rows", []) if item.get("net_quantity_basis") == "number"), None)
            return (row.get("usp_unit"), "Selected from the JSON unit-sale-price table") if row else (UNKNOWN, "No number-based unit row")
        canonical_basis = {"mass": "mass", "volume": "volume", "length": "length"}.get(str(basis))
        if canonical_basis is None:
            return UNKNOWN, "Net-quantity basis is unsupported by this table"
        quantity = _quantity_in_canonical_unit(package, canonical_basis)
        if quantity is None:
            return UNKNOWN, "Net quantity is required to select the unit-sale-price unit"
        rows = [row for row in table.get("rows", []) if row.get("net_quantity_basis") == canonical_basis]
        for row in rows:
            condition = str(row.get("condition", "")).casefold()
            if condition == "any":
                return row.get("usp_unit"), "Selected from the JSON unit-sale-price table"
            match = re.search(r"(<=|>=|<|>)\s*([0-9.]+)\s*(kg|g|ml|l|m|cm)\b", condition)
            if not match:
                continue
            comparison, threshold_text, threshold_unit = match.groups()
            threshold = float(threshold_text)
            target_basis = {"kg": "mass", "g": "mass", "l": "volume", "ml": "volume", "m": "length", "cm": "length"}[threshold_unit]
            if target_basis != canonical_basis:
                continue
            threshold_package = {"net_quantity_value": threshold, "net_quantity_unit": threshold_unit}
            threshold_value = _quantity_in_canonical_unit(threshold_package, canonical_basis)
            if threshold_value is None:
                continue
            matches = {
                "<": quantity < threshold_value,
                "<=": quantity <= threshold_value,
                ">": quantity > threshold_value,
                ">=": quantity >= threshold_value,
            }[comparison]
            if matches:
                return row.get("usp_unit"), "Selected from the JSON unit-sale-price table"
        return UNKNOWN, "No matching unit-sale-price table row"

    if table_name == "units_by_magnitude" and isinstance(table, dict):
        basis = package.get("net_quantity_basis", UNKNOWN)
        unit = _normalize_unit(package.get("net_quantity_unit"))
        quantity = _quantity_in_canonical_unit(package, str(basis)) if basis is not UNKNOWN else None
        if quantity is None:
            return UNKNOWN, "Net quantity is required to select the unit by magnitude"
        if basis == "mass":
            row = next((item for item in table.get("rows", []) if item.get("basis") == "mass"), None)
            if quantity == 1 and row and "option" in str(table.get("proviso", "")).casefold():
                return [row["unit"], row["unit_at_or_above"]], "Both JSON units are permitted at the exact magnitude boundary"
            return ((row["unit_at_or_above"] if quantity >= 1 else row["unit"]), "Selected from the JSON units-by-magnitude table") if row else (UNKNOWN, "No mass unit row")
        if basis == "length":
            row = next((item for item in table.get("rows", []) if item.get("basis") == "length"), None)
            if quantity == 1 and row and "option" in str(table.get("proviso", "")).casefold():
                return [row["unit"], row["unit_at_or_above"]], "Both JSON units are permitted at the exact magnitude boundary"
            return ((row["unit_at_or_above"] if quantity >= 1 else row["unit"]), "Selected from the JSON units-by-magnitude table") if row else (UNKNOWN, "No length unit row")
        if basis == "area":
            row = next((item for item in table.get("rows", []) if item.get("basis") == "area"), None)
            if quantity == 1 and row and "option" in str(table.get("proviso", "")).casefold():
                return [row["unit"], row["unit_at_or_above"]], "Both JSON units are permitted at the exact magnitude boundary"
            return ((row["unit_at_or_above"] if quantity >= 1 else row["unit"]), "Selected from the JSON units-by-magnitude table") if row else (UNKNOWN, "No area unit row")
        if basis == "volume" and unit in {"l", "ml"}:
            row = next((item for item in table.get("rows", []) if item.get("basis") == "volume (liquid)"), None)
            if quantity == 1 and row and "option" in str(table.get("proviso", "")).casefold():
                return [row["unit"], row["unit_at_or_above"]], "Both JSON units are permitted at the exact magnitude boundary"
            return ((row["unit_at_or_above"] if quantity >= 1 else row["unit"]), "Selected from the JSON units-by-magnitude table") if row else (UNKNOWN, "No liquid-volume unit row")
        if basis == "volume" and unit in {"cm3", "dm3", "m3"}:
            rows = [row for row in table.get("rows", []) if row.get("basis") == "volume (cubic measure)"]
            thresholds = []
            for row in rows:
                match = re.fullmatch(r"\s*([0-9.]+)\s*(cm3|dm3|m3)\s*", str(row.get("less_than", "")), re.I)
                if match:
                    threshold_package = {"net_quantity_value": float(match.group(1)), "net_quantity_unit": match.group(2)}
                    threshold_value = _quantity_in_canonical_unit(threshold_package, "volume")
                    if threshold_value is not None:
                        thresholds.append((threshold_value, row))
            thresholds.sort(key=lambda item: item[0])
            for index, (threshold, row) in enumerate(thresholds):
                if quantity < threshold:
                    return row["unit"], "Selected from the JSON cubic-volume magnitude table"
                if quantity == threshold:
                    choices = [row["unit_at_or_above"]]
                    if index + 1 < len(thresholds):
                        choices.append(thresholds[index + 1][1]["unit"])
                    return choices, "JSON proviso permits either unit at the exact magnitude boundary"
            if thresholds:
                return thresholds[-1][1]["unit_at_or_above"], "Selected from the JSON cubic-volume magnitude table"
            return UNKNOWN, "No cubic-volume unit rows are available"
        return UNKNOWN, "Volume kind is required to select a unit by magnitude"

    if table is None:
        return UNKNOWN, f"lookup_tables.{table_name} is not present in the source JSON"
    if isinstance(table, dict) and isinstance(table.get("commodities"), list):
        return table["commodities"], "Resolved from the JSON commodity lookup table"
    if isinstance(table, dict) and table_name in {
        "mpe_weight_volume_first_schedule_table_I",
        "mpe_length_area_number_first_schedule_table_II",
    }:
        return table, "Resolved from the JSON First Schedule table"
    return UNKNOWN, f"lookup_tables.{table_name} needs a supported contextual resolver"


def _resolve_value_from(
    reference: str,
    ruleset: LoadedRuleset,
    facts: dict[str, Any],
    calculations: dict[str, Any],
) -> tuple[Any, str]:
    multiplication = _MULTIPLY_EXPRESSION.fullmatch(reference)
    if multiplication:
        factor = float(multiplication.group(1))
        field_path = multiplication.group(2)
        value, detail = _resolve_fact(facts, field_path)
        numeric = _as_number(value)
        if numeric is None:
            return UNKNOWN, f"Cannot calculate {reference}: {detail}"
        return factor * numeric, f"Resolved arithmetic value from {reference}"
    if reference.startswith("lookup_tables."):
        return _resolve_lookup_table(reference.partition(".")[2], ruleset, facts, calculations)
    return _resolve_fact(facts, reference)


def _resolve_fact(facts: dict[str, Any], field_path: str) -> tuple[Any, str]:
    current: Any = facts
    for segment in field_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return UNKNOWN, f"{field_path} was not supplied"
        current = current[segment]
    if isinstance(current, dict) and "value" in current and set(current).issubset({"value", "evidence_ids", "source", "confidence", "known"}):
        if current.get("known") is False:
            return UNKNOWN, f"{field_path} is explicitly unknown"
        return current.get("value"), f"Resolved fact {field_path}"
    return current, f"Resolved fact {field_path}"


def _evaluate_condition(
    expression: dict[str, Any],
    facts: dict[str, Any],
    evidence_by_field: dict[str, list[str]],
    ruleset: LoadedRuleset,
    calculations: dict[str, Any],
    location: str,
) -> tuple[Truth, list[dict[str, Any]]]:
    if "all" in expression:
        child_results = [_evaluate_condition(child, facts, evidence_by_field, ruleset, calculations, f"{location}.all[{index}]") for index, child in enumerate(expression["all"])]
        outcome = _tri_all([result for result, _trace in child_results])
        trace = [entry for _result, entries in child_results for entry in entries]
        trace.append({"path": location, "combinator": "all", "result": outcome.value})
        return outcome, trace
    if "any" in expression:
        child_results = [_evaluate_condition(child, facts, evidence_by_field, ruleset, calculations, f"{location}.any[{index}]") for index, child in enumerate(expression["any"])]
        outcome = _tri_any([result for result, _trace in child_results])
        trace = [entry for _result, entries in child_results for entry in entries]
        trace.append({"path": location, "combinator": "any", "result": outcome.value})
        return outcome, trace
    if "not" in expression:
        result, trace = _evaluate_condition(expression["not"], facts, evidence_by_field, ruleset, calculations, f"{location}.not")
        outcome = _tri_not(result)
        trace.append({"path": location, "combinator": "not", "result": outcome.value})
        return outcome, trace

    field_path = expression["field"]
    operator = expression["op"]
    actual, actual_detail = _resolve_fact(facts, field_path)
    if "value_from" in expression:
        expected, expected_detail = _resolve_value_from(expression["value_from"], ruleset, facts, calculations)
    else:
        expected = expression.get("value", None)
        expected_detail = "Literal comparison value"
    outcome = _apply_operator(actual, operator, expected)
    return outcome, [{
        "path": location,
        "field": field_path,
        "operator": operator,
        "actual": _to_json_value(actual),
        "expected": _to_json_value(expected),
        "result": outcome.value,
        "evidence_ids": evidence_by_field.get(field_path, []),
        "detail": f"{actual_detail}; {expected_detail}",
    }]


def _merge_fact_source(
    target: dict[str, Any],
    source: Any,
    namespace: str,
    evidence_by_field: dict[str, list[str]],
) -> None:
    if not isinstance(source, dict):
        return
    for key, value in source.items():
        if isinstance(value, dict) and "value" in value and set(value).issubset({"value", "evidence_ids", "source", "confidence", "known"}):
            ids = value.get("evidence_ids", [])
            if isinstance(ids, list):
                evidence_by_field[f"{namespace}.{key}"] = [str(item) for item in ids]
            if value.get("known") is False:
                continue
            target[key] = value.get("value")
        else:
            target[key] = value


def _normalized_field_value(normalized_fields: Any, field_name: str) -> tuple[Any, list[str], bool]:
    if not isinstance(normalized_fields, dict):
        return None, [], False
    field = normalized_fields.get(field_name)
    if field is None:
        return None, [], False
    if isinstance(field, dict):
        return field.get("value"), list(field.get("region_ids") or []), bool(field.get("detected"))
    return getattr(field, "value", None), list(getattr(field, "region_ids", []) or []), bool(getattr(field, "detected", False))


def _normalized_field_confidence(normalized_fields: Any, field_name: str) -> float:
    if not isinstance(normalized_fields, dict):
        return 0.0
    field = normalized_fields.get(field_name)
    if field is None:
        return 0.0
    value = field.get("confidence", 0.0) if isinstance(field, dict) else getattr(field, "confidence", 0.0)
    number = _as_number(value)
    return number if number is not None else 0.0


def _normalized_field_regions(normalized_fields: Any, field_name: str) -> list[tuple[str, str]]:
    if not isinstance(normalized_fields, dict):
        return []
    field = normalized_fields.get(field_name)
    if field is None:
        return []
    regions = field.get("regions", []) if isinstance(field, dict) else getattr(field, "regions", [])
    output = []
    for region in regions or []:
        if isinstance(region, dict):
            region_id, text = region.get("region_id"), region.get("text")
        else:
            region_id, text = getattr(region, "region_id", None), getattr(region, "text", None)
        if region_id and isinstance(text, str):
            output.append((str(region_id), text))
    return output


def _derive_rule_facts(
    ocr_result: dict[str, Any] | None,
    normalized_fields: Any,
    validation_profile: str | None,
    package_facts: dict[str, Any] | None,
    label_facts: dict[str, Any] | None,
    confidence_threshold: float,
    normalized_metadata: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    source = ocr_result if isinstance(ocr_result, dict) else {}
    facts: dict[str, Any] = {"package": {}, "label": {}, "sale": {}, "advertisement": {}, "entity": {}, "inspection": {}}
    evidence_by_field: dict[str, list[str]] = {}

    def put(namespace: str, key: str, value: Any, region_ids: list[str]) -> None:
        if value is not None:
            facts[namespace][key] = value
            if region_ids:
                evidence_by_field[f"{namespace}.{key}"] = [str(region_id) for region_id in region_ids]

    package_type = source.get("package_type")
    if package_type is None:
        package_type = {"e_commerce_product_listing": "retail", "wholesale_package": "wholesale"}.get(validation_profile)
    if package_type is not None:
        put("package", "package_type", package_type, [])
    if validation_profile == "e_commerce_product_listing":
        facts["package"].setdefault("is_sold_via_ecommerce", True)

    normalized_mappings = {
        "country_of_origin": ("label", "country_of_origin"),
        "common_generic_name_of_commodity": ("label", "generic_name"),
        "month_and_year_of_manufacture_or_packing": ("label", "mfg_month_year"),
    }
    for source_name, (namespace, target_name) in normalized_mappings.items():
        value, region_ids, detected = _normalized_field_value(normalized_fields, source_name)
        if detected:
            if _normalized_field_confidence(normalized_fields, source_name) >= confidence_threshold:
                put(namespace, target_name, value, region_ids)
                if target_name == "generic_name":
                    facts["package"].setdefault("commodity", value)
            elif region_ids:
                evidence_by_field[f"{namespace}.{target_name}"] = [str(region_id) for region_id in region_ids]

    quantity, quantity_regions, quantity_detected = _normalized_field_value(normalized_fields, "net_quantity")
    if quantity_detected and _normalized_field_confidence(normalized_fields, "net_quantity") < confidence_threshold and quantity_regions:
        evidence_by_field["label.net_quantity_text"] = [str(region_id) for region_id in quantity_regions]
    if quantity_detected and _normalized_field_confidence(normalized_fields, "net_quantity") >= confidence_threshold:
        quantity_text = str(quantity)
        match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*(kilograms?|kgs?|grams?|gms?|g|millilit(?:er|re)s?|mls?|ml|lit(?:er|re)s?|l|cm3|dm3|m3|cm|m|dm2|m2|number|unit|piece|pair|set)\b", quantity_text, re.I)
        if match:
            amount = float(match.group(1).replace(",", "."))
            unit = _normalize_unit(match.group(2))
            basis = "mass" if unit in {"g", "kg"} else "volume" if unit in {"ml", "l"} else "length" if unit in {"cm", "m"} else "area" if unit in {"dm2", "m2"} else "number" if unit in {"number", "unit", "piece", "pair", "set"} else None
            if basis:
                put("label", "net_quantity_text", quantity_text, quantity_regions)
                put("label", "net_quantity_declared_value", amount, quantity_regions)
                put("label", "net_quantity_declared_unit", unit, quantity_regions)
                put("package", "net_quantity_basis", basis, quantity_regions)
                put("package", "net_quantity_value", amount, quantity_regions)
                put("package", "net_quantity_unit", unit, quantity_regions)
                if basis == "mass":
                    put("package", "net_quantity_in_g_or_ml", amount * (1000 if unit == "kg" else 1), quantity_regions)
                    put("package", "net_quantity_in_kg", amount / (1000 if unit == "g" else 1), quantity_regions)
                    put("package", "net_quantity_in_kg_or_l", amount / (1000 if unit == "g" else 1), quantity_regions)
                elif basis == "volume":
                    volume_in_l = amount * {"ml": 0.001, "cm3": 0.001, "l": 1, "dm3": 1, "m3": 1000}[unit]
                    put("package", "net_quantity_in_g_or_ml", volume_in_l * 1000, quantity_regions)
                    put("package", "net_quantity_in_l", volume_in_l, quantity_regions)
                    put("package", "net_quantity_in_kg_or_l", volume_in_l, quantity_regions)

    mrp_text, mrp_regions, mrp_detected = _normalized_field_value(normalized_fields, "maximum_retail_price_mrp")
    if mrp_detected and _normalized_field_confidence(normalized_fields, "maximum_retail_price_mrp") < confidence_threshold and mrp_regions:
        evidence_by_field["label.mrp_text"] = [str(region_id) for region_id in mrp_regions]
    if mrp_detected and _normalized_field_confidence(normalized_fields, "maximum_retail_price_mrp") >= confidence_threshold and isinstance(mrp_text, str):
        put("label", "mrp_text", mrp_text, mrp_regions)
        amount = re.search(r"(?:₹|Rs\.?|INR)?\s*([0-9]+(?:[.,][0-9]{1,2})?)", mrp_text, re.I)
        if amount:
            put("label", "mrp_value", float(amount.group(1).replace(",", ".")), mrp_regions)
            put("label", "mrp_currency_indian", bool(re.search(r"₹|\bRs\.?|\bINR\b", mrp_text, re.I)), mrp_regions)
            put("label", "mrp_inclusive_of_taxes_wording", bool(re.search(r"incl(?:usive|\.)?\s*(?:of\s*)?all\s*taxes", mrp_text, re.I)), mrp_regions)

    unit_price_text, unit_price_regions, unit_price_detected = _normalized_field_value(normalized_fields, "unit_sale_price")
    if unit_price_detected and _normalized_field_confidence(normalized_fields, "unit_sale_price") < confidence_threshold and unit_price_regions:
        evidence_by_field["label.unit_sale_price_value"] = [str(region_id) for region_id in unit_price_regions]
    if unit_price_detected and _normalized_field_confidence(normalized_fields, "unit_sale_price") >= confidence_threshold and isinstance(unit_price_text, str):
        match = re.search(r"(?:₹|Rs\.?|INR)?\s*([0-9]+(?:[.,][0-9]{1,2})?)\s*(?:/|per\s+)(kg|g|ml|l|m|cm|unit|number)\b", unit_price_text, re.I)
        if match:
            put("label", "unit_sale_price_value", float(match.group(1).replace(",", ".")), unit_price_regions)
            put("label", "unit_sale_price_unit", f"per {_normalize_unit(match.group(2))}", unit_price_regions)

    care, care_regions, care_detected = _normalized_field_value(normalized_fields, "consumer_care_details")
    care_region_texts = _normalized_field_regions(normalized_fields, "consumer_care_details")
    care_heading_ids = [
        region_id for region_id, text in care_region_texts
        if re.search(r"\b(?:consumer|customer)\s+care\b|\bfor\s+(?:queries|feedback)\b|\btoll\s*free\b|\bhelpline\b|\bcontact\s+us\b", text, re.I)
    ]
    if care_detected and _normalized_field_confidence(normalized_fields, "consumer_care_details") < confidence_threshold and care_regions:
        for key in ("name", "address", "telephone_number", "email_address"):
            target_name = "consumer_care_" + {"telephone_number": "phone", "email_address": "email"}.get(key, key)
            evidence_ids = care_heading_ids if key == "name" else care_regions
            evidence_by_field[f"label.{target_name}"] = [str(region_id) for region_id in evidence_ids]
    if care_detected and _normalized_field_confidence(normalized_fields, "consumer_care_details") >= confidence_threshold and isinstance(care, dict):
        for key in ("name", "address", "telephone_number", "email_address"):
            target_name = "consumer_care_" + {"telephone_number": "phone", "email_address": "email"}.get(key, key)
            evidence_ids = care_heading_ids if key == "name" and not care.get(key) else care_regions
            if evidence_ids:
                evidence_by_field[f"label.{target_name}"] = [str(region_id) for region_id in evidence_ids]
            put("label", target_name, care.get(key), evidence_ids)

    components = (normalized_metadata or {}).get("product_components") or source.get("product_components") or (source.get("metadata") or {}).get("product_components")
    if isinstance(components, list) and len(components) > 1:
        component_evidence_ids = []
        for component in components:
            if isinstance(component, dict):
                for key in ("name_region_ids", "quantity_region_ids"):
                    component_evidence_ids.extend(str(region_id) for region_id in component.get(key, []) if region_id not in component_evidence_ids)
        put("package", "is_multi_product_package", True, component_evidence_ids)
        if component_evidence_ids:
            evidence_by_field["label.each_product_name_and_quantity_listed"] = component_evidence_ids
        if all(isinstance(component, dict) and component.get("quantity") for component in components):
            if _normalized_field_confidence(normalized_fields, "common_generic_name_of_commodity") >= confidence_threshold:
                put("label", "each_product_name_and_quantity_listed", True, [])

    fact_sources = source.get("facts") if isinstance(source.get("facts"), dict) else {}
    for namespace in facts:
        _merge_fact_source(facts[namespace], fact_sources.get(namespace), namespace, evidence_by_field)
        _merge_fact_source(facts[namespace], source.get(f"{namespace}_facts"), namespace, evidence_by_field)
        if namespace in {"package", "label", "sale", "advertisement", "entity", "inspection"}:
            _merge_fact_source(facts[namespace], source.get(namespace), namespace, evidence_by_field)
    _merge_fact_source(facts["package"], package_facts, "package", evidence_by_field)
    _merge_fact_source(facts["label"], label_facts, "label", evidence_by_field)
    return facts, evidence_by_field


def _calculation_result(status: str, **values: Any) -> dict[str, Any]:
    return {"status": status, **_to_json_value(values)}


def calculate_pdp_area(package: dict[str, Any], ruleset: LoadedRuleset) -> dict[str, Any]:
    explicit_area = _as_number(package.get("pdp_area_cm2"))
    if explicit_area is not None:
        return _calculation_result("CALCULATED", pdp_area_cm2=explicit_area, source="package.pdp_area_cm2")
    geometry = package.get("pdp_geometry")
    if not isinstance(geometry, dict):
        return _calculation_result("NOT_ASSESSED", reason="No PDP geometry or measured PDP area was supplied.")
    shape = geometry.get("shape")
    method = next((item for item in ruleset.data["calculations"]["pdp_area"].get("methods", []) if item.get("shape") == shape), None)
    if not isinstance(method, dict):
        return _calculation_result("NOT_ASSESSED", reason="PDP shape is not described in the JSON calculation metadata.")

    def to_cm(value: Any, direct_key: str, mm_key: str) -> float | None:
        direct = _as_number(geometry.get(direct_key))
        if direct is not None:
            return direct
        millimetres = _as_number(geometry.get(mm_key))
        return millimetres / 10 if millimetres is not None else None

    formula = str(method.get("formula", ""))
    factor_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*x", formula, re.I)
    factor = float(factor_match.group(1)) if factor_match else 1.0
    if shape == "rectangular":
        height = to_cm(geometry, "height_cm", "height_mm")
        width = to_cm(geometry, "width_cm", "width_mm")
        if height is None or width is None:
            return _calculation_result("NOT_ASSESSED", reason="Rectangular PDP height and width are required.")
        return _calculation_result("CALCULATED", pdp_area_cm2=height * width * factor, formula=formula, source_rule_ref=method.get("condition"))
    if shape == "cylindrical or nearly cylindrical":
        height = to_cm(geometry, "height_cm", "height_mm")
        circumference = to_cm(geometry, "circumference_cm", "circumference_mm")
        if height is None or circumference is None:
            return _calculation_result("NOT_ASSESSED", reason="Cylinder PDP height and circumference are required.")
        return _calculation_result("CALCULATED", pdp_area_cm2=height * circumference * factor, formula=formula)
    total_surface = _as_number(geometry.get("total_surface_area_cm2"))
    considered_area = _as_number(geometry.get("considered_pdp_area_cm2"))
    base_area = considered_area if considered_area is not None else total_surface
    if base_area is None:
        return _calculation_result("NOT_ASSESSED", reason="Other-shape PDP requires its considered PDP area or total surface area.")
    return _calculation_result("CALCULATED", pdp_area_cm2=base_area * factor, formula=formula)


def calculate_maximum_permissible_error(package: dict[str, Any], ruleset: LoadedRuleset) -> dict[str, Any]:
    basis = package.get("net_quantity_basis")
    schedule = ruleset.data["lookup_tables"].get("mpe_weight_volume_first_schedule_table_I", {})
    if basis in {"mass", "volume"}:
        quantity = _as_number(package.get("net_quantity_in_g_or_ml"))
        if quantity is None:
            canonical = _quantity_in_canonical_unit(package, str(basis))
            quantity = canonical * 1000 if canonical is not None else None
        if quantity is None or quantity <= 0:
            return _calculation_result("NOT_ASSESSED", reason="A positive net quantity is required for the First Schedule MPE lookup.")
        for row in schedule.get("rows", []):
            band = row.get("declared_g_or_ml", {})
            lower = band.get("from_exclusive")
            upper = band.get("to_inclusive")
            if (lower is None or quantity > float(lower)) and (upper is None or quantity <= float(upper)):
                absolute = row.get("mpe_absolute_g_or_ml")
                if absolute is not None:
                    return _calculation_result("CALCULATED", mpe=float(absolute), unit="g" if basis == "mass" else "ml", declared_quantity=quantity, table="mpe_weight_volume_first_schedule_table_I")
                percent = _as_number(row.get("mpe_percent"))
                if percent is None:
                    return _calculation_result("NOT_ASSESSED", reason="The selected MPE table row has no executable value.")
                raw_mpe = Decimal(str(quantity)) * Decimal(str(percent)) / Decimal("100")
                rounding = str(schedule.get("rounding", ""))
                if quantity <= 1000 and re.search(r"nearest\s+0\.1", rounding, re.I):
                    mpe = float(raw_mpe.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
                elif quantity > 1000 and re.search(r"next\s+whole", rounding, re.I):
                    mpe = float(raw_mpe.quantize(Decimal("1"), rounding=ROUND_CEILING))
                else:
                    return _calculation_result("NOT_ASSESSED", reason="The JSON MPE rounding instruction is not executable for this band.")
                return _calculation_result("CALCULATED", mpe=mpe, unit="g" if basis == "mass" else "ml", declared_quantity=quantity, mpe_percent=percent, table="mpe_weight_volume_first_schedule_table_I")
        return _calculation_result("NOT_ASSESSED", reason="No First Schedule MPE band matched the declared quantity.")
    if basis in {"length", "area", "number"}:
        table = ruleset.data["lookup_tables"].get("mpe_length_area_number_first_schedule_table_II", {})
        row = next((item for item in table.get("structured", []) if item.get("basis") == basis), None)
        quantity = _quantity_in_canonical_unit(package, str(basis))
        if row is None or quantity is None:
            return _calculation_result("NOT_ASSESSED", reason="A matching First Schedule Table-II row and declared quantity are required.")
        if basis in {"length", "area"} and table.get("note"):
            return _calculation_result("NOT_ASSESSED", reason=table["note"])
        percent = _as_number(row.get("percent_up_to_threshold"))
        if percent is None:
            return _calculation_result("NOT_ASSESSED", reason="The selected Table-II row lacks a percentage.")
        return _calculation_result("CALCULATED", mpe=quantity * percent / 100, unit=basis, declared_quantity=quantity, mpe_percent=percent, table="mpe_length_area_number_first_schedule_table_II")
    return _calculation_result("NOT_ASSESSED", reason="Net-quantity basis is not known for MPE calculation.")


def _fifth_schedule_plan(lot_size: Any, ruleset: LoadedRuleset) -> dict[str, Any]:
    size = _as_number(lot_size)
    if size is None:
        return _calculation_result("NOT_ASSESSED", reason="Lot size is unknown.")
    table = ruleset.data["lookup_tables"].get("fifth_schedule_sampling_plan", {})
    for row in table.get("rows", []):
        lower = row.get("lot_size_min")
        upper = row.get("lot_size_max")
        if (lower is None or size >= float(lower)) and (upper is None or size <= float(upper)):
            return _calculation_result("CALCULATED", **row, table="fifth_schedule_sampling_plan")
    return _calculation_result("NOT_ASSESSED", reason="Lot size is outside the supplied Fifth Schedule sampling rows.")


def _sample_quantity(value: Any, package: dict[str, Any], basis: str) -> float | None:
    if isinstance(value, dict):
        direct = _as_number(value.get("net_quantity_in_g_or_ml"))
        if direct is not None and basis in {"mass", "volume"}:
            return direct
        quantity_value = value.get("net_quantity", value.get("value"))
        quantity_unit = value.get("unit", package.get("net_quantity_unit"))
    else:
        quantity_value = value
        quantity_unit = package.get("net_quantity_unit")
    amount = _as_number(quantity_value)
    if amount is None:
        return None
    canonical_package = {"net_quantity_value": amount, "net_quantity_unit": quantity_unit}
    canonical = _quantity_in_canonical_unit(canonical_package, basis)
    if canonical is None:
        return None
    return canonical * 1000 if basis in {"mass", "volume"} else canonical


def calculate_lot_approval(
    package: dict[str, Any], inspection: dict[str, Any], mpe_result: dict[str, Any], ruleset: LoadedRuleset,
) -> dict[str, Any]:
    lot_size = inspection.get("lot_size", package.get("lot_size"))
    plan = _fifth_schedule_plan(lot_size, ruleset)
    if plan["status"] != "CALCULATED":
        return {"status": "NOT_ASSESSED", "plan": plan, "reason": plan.get("reason")}
    measurements = inspection.get("actual_net_quantities")
    expected_count = plan.get("sample_size_n")
    if not isinstance(measurements, list) or len(measurements) != expected_count:
        return {"status": "NOT_ASSESSED", "plan": plan, "reason": f"The source Fifth Schedule requires {expected_count} sample measurements; that exact sample was not supplied."}
    basis = str(package.get("net_quantity_basis", ""))
    declared = (
        _as_number(package.get("net_quantity_in_g_or_ml"))
        if basis in {"mass", "volume"}
        else _quantity_in_canonical_unit(package, basis)
    )
    mpe = _as_number(mpe_result.get("mpe")) if mpe_result.get("status") == "CALCULATED" else None
    values = [_sample_quantity(item, package, basis) for item in measurements]
    if declared is None or mpe is None or any(value is None for value in values):
        return {"status": "NOT_ASSESSED", "plan": plan, "reason": "Declared quantity, MPE, and every sample net quantity are required."}
    numeric_values = [float(value) for value in values if value is not None]
    mean_value = statistics.mean(numeric_values)
    deviation = statistics.stdev(numeric_values) if len(numeric_values) > 1 else 0.0
    correction = _as_number(plan.get("correction_factor_C"))
    if correction is None:
        return {"status": "NOT_ASSESSED", "plan": plan, "reason": "Fifth Schedule correction factor is missing."}
    corrected_average = mean_value + deviation * correction
    errors = [max(0.0, declared - value) for value in numeric_values]
    over_mpe = sum(error > mpe and error < 2 * mpe for error in errors)
    any_over_double = any(error > 2 * mpe for error in errors)
    declarations = [item.get("required_declarations_present") for item in measurements if isinstance(item, dict)]
    declarations_known = len(declarations) == len(measurements) and all(isinstance(value, bool) for value in declarations)
    all_declarations = declarations_known and all(declarations)
    if not declarations_known:
        return {"status": "NOT_ASSESSED", "plan": plan, "mean": mean_value, "standard_deviation": deviation, "corrected_average": corrected_average, "reason": "Presence of required declarations on every sampled package was not supplied."}
    passed = (
        corrected_average >= declared
        and over_mpe <= int(plan["max_packages_with_error_gt_MPE_but_lt_2xMPE"])
        and not any_over_double
        and all_declarations
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "plan": plan,
        "mean": mean_value,
        "standard_deviation": deviation,
        "correction_factor": correction,
        "corrected_average": corrected_average,
        "declared_quantity": declared,
        "mpe": mpe,
        "packages_over_mpe_below_double": over_mpe,
        "any_package_over_double_mpe": any_over_double,
        "all_required_declarations_present": all_declarations,
    }


def calculate_sixth_schedule_tare(
    package: dict[str, Any], inspection: dict[str, Any], mpe_result: dict[str, Any], ruleset: LoadedRuleset,
) -> dict[str, Any]:
    procedure = ruleset.data.get("calculations", {}).get("tare_weight_procedure_sixth_schedule", {})
    mpe = _as_number(mpe_result.get("mpe")) if mpe_result.get("status") == "CALCULATED" else None
    if mpe is None:
        return _calculation_result("NOT_ASSESSED", reason="A calculated MPE is required by the JSON Sixth Schedule procedure.")
    low_instruction = str(procedure.get("if_tare_weight_le_0_3_x_MPE", ""))
    low_key = next((key for key in procedure if key.startswith("if_tare_weight_le_")), "")
    low_match = re.search(r"_le_(\d+)_(\d+)_x_MPE$", low_key, re.I)
    branch_text = str(procedure.get("else", ""))
    spread_match = re.search(r"\(max\s+tare\s*-\s*min\s+tare\)\s*<=\s*([0-9]+(?:\.[0-9]+)?)\s*x\s*MPE", branch_text, re.I)
    tare_factor = float(f"{low_match.group(1)}.{low_match.group(2)}") if low_match else None
    tare_limit = tare_factor * mpe if tare_factor is not None else None
    spread_limit = float(spread_match.group(1)) * mpe if spread_match else None
    if tare_limit is None or spread_limit is None:
        return _calculation_result("NOT_ASSESSED", reason="The JSON Sixth Schedule thresholds could not be interpreted.")
    sample_count_match = re.search(r"weigh\s+(\d+)\s+tare\s+packages", branch_text, re.I)
    required_tare_count = int(sample_count_match.group(1)) if sample_count_match else None
    if required_tare_count is None:
        return _calculation_result("NOT_ASSESSED", reason="The JSON Sixth Schedule tare sample count could not be interpreted.")
    if package.get("net_quantity_basis") == "volume":
        specific_gravity = package.get("specific_gravity_sufficiently_constant", UNKNOWN)
        if specific_gravity is UNKNOWN:
            return _calculation_result("NOT_ASSESSED", reason="Specific-gravity consistency is required by the JSON liquid procedure.", source_text=procedure.get("liquids_by_volume"))
        if specific_gravity is False:
            return _calculation_result("NOT_ASSESSED", procedure="measure_by_volume", source_text=procedure.get("liquids_by_volume"))
    single_tare = _as_number(inspection.get("tare_weight"))
    if single_tare is not None and single_tare <= tare_limit:
        return _calculation_result("CALCULATED", procedure="single_tare_for_all_samples", tare_weight=single_tare, threshold=tare_limit)
    tare_weights = inspection.get("tare_weights")
    if not isinstance(tare_weights, list) or len(tare_weights) < required_tare_count:
        return _calculation_result("NOT_ASSESSED", reason=f"The JSON Sixth Schedule requires {required_tare_count} tare-package weights when tare exceeds the single-tare threshold.", single_tare_threshold=tare_limit)
    values = [_as_number(value) for value in tare_weights[:required_tare_count]]
    if any(value is None for value in values):
        return _calculation_result("NOT_ASSESSED", reason="Tare weights must all be finite numbers.")
    numbers = [float(value) for value in values if value is not None]
    spread = max(numbers) - min(numbers)
    if spread <= spread_limit:
        return _calculation_result("CALCULATED", procedure="mean_tare", tare_weights=numbers, spread=spread, spread_limit=spread_limit, mean_tare=statistics.mean(numbers))
    return _calculation_result(
        "REVIEW_REQUIRED",
        procedure="individual_tare_weighing_or_marked_empty_containers",
        tare_weights=numbers,
        spread=spread,
        spread_limit=spread_limit,
        source_text=branch_text,
    )


def calculate_package_level_deficiency(
    package: dict[str, Any], inspection: dict[str, Any], mpe_result: dict[str, Any],
) -> dict[str, Any]:
    basis = package.get("net_quantity_basis")
    if mpe_result.get("status") != "CALCULATED" or basis is None:
        return _calculation_result("NOT_ASSESSED", reason="Declared quantity, basis, and MPE are required by the JSON package-deficiency formula.")
    if basis in {"mass", "volume"}:
        declared = _as_number(package.get("net_quantity_in_g_or_ml"))
        actual = _as_number(inspection.get("actual_net_quantity_in_g_or_ml"))
        unit = "g" if basis == "mass" else "ml"
    else:
        declared = _quantity_in_canonical_unit(package, str(basis))
        actual = _as_number(inspection.get("actual_net_quantity_canonical"))
        unit = str(basis)
    if declared is None or actual is None:
        return _calculation_result("NOT_ASSESSED", reason="An actual net quantity measured in the JSON-required basis is not supplied.")
    error = max(0.0, declared - actual)
    mpe = _as_number(mpe_result.get("mpe"))
    if mpe is None:
        return _calculation_result("NOT_ASSESSED", reason="MPE could not be calculated from the source schedule.")
    return _calculation_result(
        "CALCULATED",
        declared_quantity=declared,
        actual_quantity=actual,
        error_in_deficiency=error,
        mpe=mpe,
        unit=unit,
        exceeds_mpe=error > mpe,
        exceeds_twice_mpe=error > 2 * mpe,
    )


def calculate_json_defined_values(
    facts: dict[str, Any], ruleset: LoadedRuleset,
) -> dict[str, Any]:
    package = facts.get("package", {})
    inspection = facts.get("inspection", {})
    pdp = calculate_pdp_area(package, ruleset)
    if pdp.get("status") == "CALCULATED":
        package.setdefault("pdp_area_cm2", pdp.get("pdp_area_cm2"))
    mpe = calculate_maximum_permissible_error(package, ruleset)
    deficiency = calculate_package_level_deficiency(package, inspection, mpe)
    lot = calculate_lot_approval(package, inspection, mpe, ruleset)
    tare = calculate_sixth_schedule_tare(package, inspection, mpe, ruleset)
    return {"pdp_area": pdp, "maximum_permissible_error": mpe, "package_level_deficiency": deficiency, "lot_level": lot, "sixth_schedule_tare": tare}


def _evaluate_gate_exemptions(
    exemptions: list[dict[str, Any]],
    facts: dict[str, Any],
    evidence_by_field: dict[str, list[str]],
    ruleset: LoadedRuleset,
    calculations: dict[str, Any],
    gate_name: str,
) -> tuple[Truth, list[dict[str, Any]]]:
    results: list[Truth] = []
    traces: list[dict[str, Any]] = []
    for index, exemption in enumerate(exemptions):
        condition, condition_trace = _evaluate_condition(exemption["condition"], facts, evidence_by_field, ruleset, calculations, f"{gate_name}[{index}].condition")
        traces.extend(condition_trace)
        if "requires" in exemption:
            required, required_trace = _evaluate_condition(exemption["requires"], facts, evidence_by_field, ruleset, calculations, f"{gate_name}[{index}].requires")
            traces.extend(required_trace)
            condition = _tri_all([condition, required])
        if "except_when" in exemption:
            exception, exception_trace = _evaluate_condition(exemption["except_when"], facts, evidence_by_field, ruleset, calculations, f"{gate_name}[{index}].except_when")
            traces.extend(exception_trace)
            condition = _tri_all([condition, _tri_not(exception)])
        requirements = exemption.get("conditional_requirements", [])
        if requirements:
            required_results = []
            for requirement_index, requirement in enumerate(requirements):
                required, required_trace = _evaluate_condition(requirement, facts, evidence_by_field, ruleset, calculations, f"{gate_name}[{index}].conditional_requirements[{requirement_index}]")
                traces.extend(required_trace)
                required_results.append(required)
            condition = _tri_all([condition, _tri_all(required_results)])
        results.append(condition)
    return _tri_any(results), traces


def _overall_data_driven_status(results: list[dict[str, Any]]) -> str:
    statuses = [result["status"] for result in results]
    if "FAIL" in statuses:
        return "NON_COMPLIANT"
    if "REVIEW_REQUIRED" in statuses or "NOT_ASSESSED" in statuses:
        return "REVIEW_REQUIRED"
    if statuses and all(status == "NOT_APPLICABLE" for status in statuses):
        return "NOT_APPLICABLE"
    return "PASS"


def _evidence_context(normalized_fields: Any, structured_extraction: Any) -> dict[str, dict[str, Any]]:
    context: dict[str, dict[str, Any]] = {}
    fields = normalized_fields if isinstance(normalized_fields, dict) else {}
    for field in fields.values():
        regions = field.get("regions", []) if isinstance(field, dict) else getattr(field, "regions", [])
        for region in regions or []:
            if isinstance(region, dict):
                region_id = region.get("region_id")
                text = region.get("text")
                confidence = region.get("confidence")
            else:
                region_id = getattr(region, "region_id", None)
                text = getattr(region, "text", None)
                confidence = getattr(region, "confidence", None)
            if region_id:
                context[str(region_id)] = {
                    "evidence_id": str(region_id),
                    "source_text": text,
                    "ocr_confidence": confidence,
                    "_extraction_confidence_by_value": {},
                }

    if hasattr(structured_extraction, "model_dump"):
        structured_extraction = structured_extraction.model_dump()

    def add_extraction_metadata(value: Any) -> None:
        if isinstance(value, dict):
            evidence_ids = value.get("evidence_region_ids", [])
            if isinstance(evidence_ids, list) and evidence_ids:
                raw_text = value.get("raw_text")
                extraction_confidence = value.get("extraction_confidence")
                ocr_confidence = value.get("ocr_confidence")
                extracted_value = value.get("value")
                for evidence_id in evidence_ids:
                    detail = context.setdefault(str(evidence_id), {"evidence_id": str(evidence_id)})
                    if not detail.get("source_text") and raw_text:
                        detail["source_text"] = raw_text
                    if detail.get("ocr_confidence") is None and ocr_confidence is not None:
                        detail["ocr_confidence"] = ocr_confidence
                    if extraction_confidence is not None and extracted_value is not None and not isinstance(extracted_value, (dict, list)):
                        value_key = json.dumps(_to_json_value(extracted_value), sort_keys=True)
                        confidences = detail.setdefault("_extraction_confidence_by_value", {})
                        existing = confidences.get(value_key)
                        confidences[value_key] = extraction_confidence if existing in (None, extraction_confidence) else None
            for child in value.values():
                add_extraction_metadata(child)
        elif isinstance(value, list):
            for child in value:
                add_extraction_metadata(child)

    add_extraction_metadata(structured_extraction)
    return context


def _trace_evidence(evidence_ids: list[str], actual: Any, context: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    actual_key = None if actual == "UNKNOWN" else json.dumps(_to_json_value(actual), sort_keys=True)
    for evidence_id in evidence_ids:
        detail = dict(context.get(str(evidence_id), {"evidence_id": str(evidence_id)}))
        confidences = detail.pop("_extraction_confidence_by_value", {})
        detail["extraction_confidence"] = confidences.get(actual_key) if actual_key else None
        output.append(detail)
    return output


class LegalRulesEvaluator:
    def __init__(self, ruleset_path: str | Path | None = None):
        self.ruleset = load_legal_ruleset(ruleset_path)

    def evaluate(
        self,
        ocr_result: dict[str, Any] | None = None,
        *,
        normalized_fields: Any = None,
        validation_profile: str | None = None,
        package_facts: dict[str, Any] | None = None,
        label_facts: dict[str, Any] | None = None,
        confidence_threshold: float = 0.8,
        normalized_metadata: dict[str, Any] | None = None,
        structured_extraction: Any = None,
    ) -> dict[str, Any]:
        facts, evidence_by_field = _derive_rule_facts(
            ocr_result, normalized_fields, validation_profile, package_facts, label_facts,
            confidence_threshold, normalized_metadata,
        )
        calculations = calculate_json_defined_values(facts, self.ruleset)
        evidence_context = _evidence_context(normalized_fields, structured_extraction)

        gates = self.ruleset.data["applicability"]
        rule3_definition = gates.get("chapter_II_retail", {})
        rule3_exemptions, rule3_trace = _evaluate_gate_exemptions(
            rule3_definition.get("applies_unless_any", []), facts, evidence_by_field,
            self.ruleset, calculations, "applicability.chapter_II_retail",
        )
        package_type = facts["package"].get("package_type", UNKNOWN)
        if package_type is UNKNOWN:
            chapter_ii_scope = Truth.UNKNOWN
        elif package_type in {"wholesale", "export"}:
            chapter_ii_scope = Truth.FALSE
        elif rule3_exemptions is Truth.TRUE:
            chapter_ii_scope = Truth.FALSE
        elif rule3_exemptions is Truth.UNKNOWN:
            chapter_ii_scope = Truth.UNKNOWN
        else:
            chapter_ii_scope = Truth.TRUE

        rule26_definition = gates.get("rule_26_exemptions", {})
        rule26_exempt, rule26_trace = _evaluate_gate_exemptions(
            rule26_definition.get("exemptions", []), facts, evidence_by_field,
            self.ruleset, calculations, "applicability.rule_26_exemptions",
        )

        results: list[dict[str, Any]] = []
        for rule in self.ruleset.rules:
            rule_trace: list[dict[str, Any]] = []
            chapter = str(rule.get("chapter", ""))
            if chapter == "II":
                rule_trace.append({"path": "applicability.chapter_II_retail", "combinator": "Rule 3 gate", "result": chapter_ii_scope.value, "evidence_ids": [], "detail": "See applicability_gates.rule_3_trace for the full gate trace."})
            rule_trace.append({"path": "applicability.rule_26_exemptions", "combinator": "Rule 26 gate", "result": rule26_exempt.value, "evidence_ids": [], "detail": "See applicability_gates.rule_26_trace for the full gate trace."})
            if "applies_when" in rule:
                specific_applicability, specific_trace = _evaluate_condition(
                    rule["applies_when"], facts, evidence_by_field, self.ruleset, calculations,
                    f"rules.{rule['id']}.applies_when",
                )
                rule_trace.extend(specific_trace)
            else:
                specific_applicability = Truth.TRUE

            if "applies_to" in rule:
                actor = facts.get("entity", {}).get("role", UNKNOWN)
                if actor is UNKNOWN:
                    applies_to = Truth.UNKNOWN
                    rule_trace.append({"path": f"rules.{rule['id']}.applies_to", "actual": "UNKNOWN", "expected": rule["applies_to"], "result": "UNKNOWN", "evidence_ids": [], "detail": "Actor role was not supplied."})
                else:
                    applies_to = Truth.TRUE if actor in rule["applies_to"] else Truth.FALSE
                    rule_trace.append({"path": f"rules.{rule['id']}.applies_to", "actual": actor, "expected": rule["applies_to"], "result": applies_to.value, "evidence_ids": evidence_by_field.get("entity.role", []), "detail": "Compared with the JSON applies_to roles."})
                specific_applicability = _tri_all([specific_applicability, applies_to])

            chapter_ii_applies = chapter_ii_scope if chapter == "II" else Truth.TRUE
            scope = _tri_all([chapter_ii_applies, _tri_not(rule26_exempt), specific_applicability])
            has_check = "check" in rule
            check_result = Truth.UNKNOWN
            if has_check:
                check_result, check_trace = _evaluate_condition(
                    rule["check"], facts, evidence_by_field, self.ruleset, calculations,
                    f"rules.{rule['id']}.check",
                )
                rule_trace.extend(check_trace)

            if scope is Truth.FALSE:
                status = "NOT_APPLICABLE"
                explanation = "The JSON applicability conditions or exemption gates show this rule does not apply."
            elif not has_check:
                status = "NOT_ASSESSED"
                explanation = "The source rule has no machine-checkable check; no legal outcome was inferred."
            elif scope is Truth.UNKNOWN or check_result is Truth.UNKNOWN:
                status = "REVIEW_REQUIRED"
                explanation = "Applicability or one or more required facts are unknown; manual review is required."
            elif check_result is Truth.TRUE:
                status = "PASS"
                explanation = "All machine-checkable conditions in the JSON rule evaluated true."
            else:
                status = "FAIL"
                explanation = "One or more machine-checkable conditions in the JSON rule evaluated false."

            evidence_ids: list[str] = []
            for item in rule_trace:
                for evidence_id in item.get("evidence_ids", []):
                    if evidence_id not in evidence_ids:
                        evidence_ids.append(evidence_id)
                item["evidence"] = _trace_evidence(item.get("evidence_ids", []), item.get("actual"), evidence_context)
            results.append({
                "rule_id": rule["id"],
                "rule_ref": rule["rule_ref"],
                "rule_name": rule["title"],
                "title": rule["title"],
                "category": rule["category"],
                "severity": rule["severity"],
                "chapter": chapter,
                "status": status,
                "applicability": scope.value,
                "has_executable_check": has_check,
                "explanation": explanation,
                "evidence_ids": evidence_ids,
                "trace": rule_trace,
            })

        counts = {status: sum(result["status"] == status for result in results) for status in ("PASS", "FAIL", "REVIEW_REQUIRED", "NOT_APPLICABLE", "NOT_ASSESSED")}
        source_meta = self.ruleset.data["meta"]
        return {
            "overall_status": _overall_data_driven_status(results),
            "rules": results,
            "summary": {
                "total_rules_in_source": self.ruleset.rule_count,
                "total_rules_loaded": self.ruleset.rule_count,
                "total_rules_evaluated": len(results),
                "total_rules_with_executable_checks": self.ruleset.rules_with_checks,
                "total_not_assessed_or_context_dependent": counts["NOT_ASSESSED"] + counts["REVIEW_REQUIRED"],
                "status_counts": counts,
            },
            "provenance": {
                "source_file": self.ruleset.path.name,
                "source_sha256": self.ruleset.sha256,
                "title": source_meta.get("title"),
                "notification": source_meta.get("notification"),
                "schema_version": source_meta.get("schema_version"),
                "consolidated_as_on": source_meta.get("consolidated_as_on"),
                "source": source_meta.get("source"),
                "validation_warnings": list(self.ruleset.validation_warnings),
            },
            "applicability_gates": {
                "rule_3_chapter_ii_scope": chapter_ii_scope.value,
                "rule_3_exemption_conditions": rule3_exemptions.value,
                "rule_3_trace": rule3_trace,
                "rule_26_package_exemption": rule26_exempt.value,
                "rule_26_trace": rule26_trace,
            },
            "calculations": calculations,
        }
