"""Bounded catalog values matching the frozen 0.1.0 contract."""
import hashlib
import json


LANGUAGES = frozenset({"auto", "zh", "en", "mixed"})


def bounded_text(value: str, limit: int, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError("invalid text value")
    if nonempty and not value.strip():
        raise ValueError("a nonempty value is required")
    return value


def language_value(value: str) -> str:
    if value not in LANGUAGES:
        raise ValueError("unsupported language")
    return value


def material_payload(syllabus: str, glossary: list[dict[str, str]], language: str):
    bounded_text(syllabus, 120000)
    language_value(language)
    if not isinstance(glossary, list) or len(glossary) > 2000:
        raise ValueError("invalid glossary")
    for term in glossary:
        if not isinstance(term, dict) or set(term) != {"term", "meaning", "language"}:
            raise ValueError("invalid glossary fields")
        bounded_text(term["term"], 160, nonempty=True)
        bounded_text(term["meaning"], 1000)
        language_value(term["language"])
    encoded = json.dumps({"syllabus": syllabus, "glossary": glossary, "language": language},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    # Decode to detach the caller's mutable input from the persistence operation.
    return json.loads(encoded), hashlib.sha256(encoded).hexdigest()


def base_revision(value: int):
    if type(value) is not int or value < 1:
        raise ValueError("base_revision must be a positive integer")
