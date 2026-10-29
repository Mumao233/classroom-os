"""Task metadata is canonical, bounded and contains IDs rather than code/body."""
import math
from uuid import UUID, uuid4

import pytest

from classroom_web.domain.tasks import fingerprint, input_reference


def test_fingerprint_canonicalizes_object_keys_without_reordering_arrays():
    first = {"path": str(uuid4()), "body": {"b": 2, "a": 1}, "ids": ["a", "b"]}
    reordered = {"ids": ["a", "b"], "body": {"a": 1, "b": 2}, "path": first["path"]}
    assert fingerprint(first) == fingerprint(reordered)
    assert fingerprint(first) != fingerprint(first | {"ids": ["b", "a"]})
    assert fingerprint(first) != fingerprint(first | {"path": str(uuid4())})


def test_fingerprint_includes_cas_and_binary_digest_metadata():
    request = {"base_revision": 1, "sha256": "a" * 64}
    assert fingerprint(request) != fingerprint(request | {"base_revision": 2})
    assert fingerprint(request) != fingerprint(request | {"sha256": "b" * 64})


@pytest.mark.parametrize("value", (math.nan, math.inf, -math.inf))
def test_nonfinite_values_cannot_have_a_request_fingerprint(value):
    with pytest.raises(ValueError):
        fingerprint({"value": value})


def test_oversized_operation_metadata_is_rejected():
    with pytest.raises(ValueError):
        fingerprint({"oversize": "合成" * 10000})


def test_input_reference_normalizes_only_supported_uuid_fields():
    classroom, material = uuid4(), uuid4()
    assert input_reference({"schema_version": 1, "class_session_id": classroom,
                            "course_material_version_id": str(material).upper()}) == {
        "schema_version": 1, "class_session_id": str(classroom),
        "course_material_version_id": str(material)}


@pytest.mark.parametrize("value", (
    {}, {"schema_version": 2}, {"schema_version": True},
    {"schema_version": 1, "url": "https://provider.invalid/request"},
    {"schema_version": 1, "code": "print('not executable')"},
    {"schema_version": 1, "raw_output": "private synthetic body"},
    {"schema_version": 1, "class_session_id": "bad uuid"},
    {"schema_version": 1, "class_session_id": "00000000-0000-1000-8000-000000000000"},
    {"schema_version": 1, "owner_id": str(uuid4())},
))
def test_unsupported_or_unversioned_input_references_are_rejected(value):
    with pytest.raises((ValueError, TypeError, AttributeError)):
        input_reference(value)
