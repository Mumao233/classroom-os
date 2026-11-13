"""Keep failed assertions/positions while redacting credentials before reporting."""
import re
import pytest

_PHC=re.compile(r'\$argon2id\$[^\s\x22\x27<>]+')
_OPAQUE=re.compile(r'([\x22\x27])(?:[A-Za-z0-9_-]{86}|[A-Za-z0-9_-]{43}|[A-Z2-7]{32})\1')


def redact(value):
    return _OPAQUE.sub(lambda m:m.group(1)+'<redacted>'+m.group(1),_PHC.sub('<redacted PHC>',value))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item,call):
    report=yield
    if report.failed and report.longrepr is not None:report.longrepr=redact(str(report.longrepr))
    report.sections=[(label,redact(value)) for label,value in report.sections]
    return report
