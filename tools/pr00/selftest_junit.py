"""Synthetic regression for parent-test/subtest accounting and failure exit gates."""
from io import StringIO
from run_checks import junit_result


def sample(parents=525, subtests=171, **extra):
    attributes = {"tests": parents + subtests, "failures": 0, "errors": 0, "skipped": 0, **extra}
    header = " ".join(f'{key}="{value}"' for key, value in attributes.items())
    return StringIO(f"<testsuites><testsuite {header}>" + "<testcase/>" * parents + "</testsuite></testsuites>")


counts, valid = junit_result(sample())
assert valid and counts["parent_testcases"] == 525 and counts["subtests"] == 171
assert not junit_result(sample(parents=524, subtests=1000))[1], "Subtests cannot replace missing parent tests"
assert not junit_result(sample(subtests=170))[1]
for field in ("failures", "errors", "skipped"):
    assert not junit_result(sample(**{field: 1}))[1], field
assert not junit_result(StringIO("<testsuites/>"))[1]
print("PASS: JUnit parent/subtest separation, missing cases, failures, errors, skips and empty report")
