# Classroom OS repository instructions

## Repository and product lines

- The canonical development repository is `J:\class-assistant\classroom-os`. The parent `J:\class-assistant` is the project container, not the Git root.
- Preserve the sibling `Classroom_OS_Web_Alpha_0.1.0_PRD_v1.1_Package` directory and all original documents. Archived PRD copies live in `docs/web-alpha/`; check `SHA256SUMS.txt` before using a replacement.
- Windows local v1.3.0 and Web Alpha 0.1.0 are independent product lines. Do not change local version identifiers, launchers, local storage/configuration, or existing behavior to make Web development convenient.
- The initial Web branch is `feature/web-alpha-0.1.0`. PR-00 is engineering preparation only; PR-01 and later business work require authorization for that scope.

## Current PR-00 scope and safety

- Allowed: documentation, requirement traceability, CI, baseline check tooling, and isolated synthetic tests.
- Do not modify existing business source files, original tests or requirements for PR-00. Do not remove assertions, skip failures, or replace real failures with weaker mocks.
- Do not read real classroom data, actual configuration secrets, API keys, model weights or Codex credentials. Tests use explicit temporary directories and synthetic fixtures.
- Do not invoke paid APIs or real Codex/model execution, activate a real microphone, deploy, expose a public service, push, merge, create tags or publish releases without the user's corresponding authorization.
- Preserve `data/`, `config.json`, model directories, runtime environments and installed local applications. Do not run setup/start/stop scripts merely to prepare tests; parse them without execution.

## Files and dependencies

- Keep task-controlled dependencies, downloads, caches, temporary scripts and test data in this repository's ignored `work/` tree. Use `work/pr00/` for PR-00. Deliver logs and reports under `outputs/pr00/`, with small durable evidence records under `docs/web-alpha/evidence/`.
- Do not use C: Desktop/Documents/Downloads/temp as the default. The user's explicit J: project path takes precedence over the generic D:\Codex default for new unrelated tasks.
- Existing global tools may be read/executed from their installed locations; do not relocate Codex or user environments.

## Verification

- Read `docs/web-alpha/README.md`, `TESTING.md` and the current acceptance report before continuing.
- Retain the full existing Python test suite and all nine Node suites. Windows testing must keep the synthetic DPAPI tests. Web tests are additional, not a replacement.
- A local tool run is not a GitHub Actions run; a mock model is not a cloud ASR quality result. Report commit, environment, commands, exit codes, failures/skips and limitations separately from historical results.
- Keep cloud quota/usage protection ahead of any authorized paid integration and keep encrypted recovery storage behind its security POC. See `DECISIONS.md` and `CONFLICTS.md`.
- Prefer relevant skills by task intent. Reuse the existing analysis and evidence instead of repeating the entire research process. Product decisions and the user's latest instructions override historical planning documents.
