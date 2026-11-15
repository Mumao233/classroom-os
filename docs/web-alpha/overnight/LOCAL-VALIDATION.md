# Infrastructure validation — 2026-11-06

Baseline: `b5f7ef97833ad0166c743a4c3f01fa7aaff99df4`, Windows local checkout.
Only infrastructure, scoped instructions and this guide changed. Existing
business source, migrations, original tests and frozen contracts are preserved.

Actual commands/results before the infrastructure commit:

- Python 3.12.14: `python -B -m unittest discover -s tools/overnight/tests -p test_*.py -v`
  — **96 passed**, 31.512 seconds. Includes real subprocess/lock recovery and
  synthetic Git object validation; GitHub publication/network responses mocked.
- `python -B tools/overnight/check_contracts.py --deps work/pr01/validation-deps`
  — **9 groups passed**. Static contracts and documents only, no database claim.
- Codex CLI `0.165.0-alpha.1.2`, existing ChatGPT login, Windows MXC, Python3.12:
  **15 real sandbox assertions passed** across writable/read-only profiles.
  Outside synthetic canary read, `.git`/control-file write and controlled
  loopback connections were denied; allowed scratch operations succeeded.
- GitHub connector and local GCM client both returned login `Mumao233`, numeric
  ID `325669781`. No credential contents were printed or persisted.
- `git diff --check` passed. Workflow YAML, embedded Python and Bash validation
  passed; validation dependencies are pinned to the existing isolated versions.

Evidence files are local under `outputs/overnight/`: `all-unit-final.log`,
`contracts-final.json`, `validation-workflow-static.json`, and
`executor-sandbox-20261106-04-py312/sandbox-probe.json`.
The first sandbox probe failed because a required Windows system environment
variable was absent; it was corrected, and the failed evidence was retained.

At this checkpoint **no model was invoked** and no GitHub Actions result is
claimed. The new Linux/PG17 workflow, original Windows regression workflow,
actual model execution, independent review and publication require their own
run-specific evidence. Subscription tokens are not a dollar-cost measurement.
