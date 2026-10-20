"""Restricted string-metadata selectors, shared by model and adapter."""
from dataclasses import dataclass
from typing import Mapping

from .exceptions import InvalidOperationError


@dataclass(frozen=True)
class Eq:
    field: str
    value: str


@dataclass(frozen=True)
class And:
    terms: tuple["Predicate", ...]

    def __post_init__(self):
        if type(self.terms) is not tuple:
            raise InvalidOperationError("And terms must be an immutable tuple")


@dataclass(frozen=True)
class Or:
    terms: tuple["Predicate", ...]

    def __post_init__(self):
        if type(self.terms) is not tuple:
            raise InvalidOperationError("Or terms must be an immutable tuple")


@dataclass(frozen=True)
class Not:
    term: "Predicate"


Predicate = Eq | And | Or | Not


def validate_predicate(predicate: Predicate, max_nodes: int = 256,
                       max_depth: int = 32) -> None:
    pending = [(predicate, 1)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > max_nodes or depth > max_depth:
            raise InvalidOperationError("Selector exceeds supported complexity")
        if type(node) is Eq:
            if (not isinstance(node.field, str) or not node.field
                    or len(node.field) > 128 or not node.field.isidentifier()
                    or not isinstance(node.value, str) or len(node.value) > 4096):
                raise InvalidOperationError("Eq requires a supported field and string value")
        elif type(node) in (And, Or):
            if type(node.terms) is not tuple:
                raise InvalidOperationError("Selector terms must be immutable")
            pending.extend((child, depth + 1) for child in node.terms)
        elif type(node) is Not:
            pending.append((node.term, depth + 1))
        else:
            raise InvalidOperationError("Unsupported selector expression")


def predicate_data(predicate: Predicate):
    if type(predicate) is Eq:
        return ["eq", predicate.field, predicate.value]
    if type(predicate) is And:
        return ["and", *[predicate_data(p) for p in predicate.terms]]
    if type(predicate) is Or:
        return ["or", *[predicate_data(p) for p in predicate.terms]]
    return ["not", predicate_data(predicate.term)]


def matches(predicate: Predicate, fields: Mapping[str, str]) -> bool:
    """Missing fields do not equal any string; Not negates that result."""
    validate_predicate(predicate)
    return _matches(predicate, fields)


def _matches(predicate: Predicate, fields: Mapping[str, str]) -> bool:
    if type(predicate) is Eq:
        return predicate.field in fields and fields[predicate.field] == predicate.value
    if type(predicate) is And:
        return all(_matches(p, fields) for p in predicate.terms)
    if type(predicate) is Or:
        return any(_matches(p, fields) for p in predicate.terms)
    return not _matches(predicate.term, fields)
