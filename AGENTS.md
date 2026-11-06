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

## Long-term collaboration (PR-01, 2026-10-28)

The original rules above are retained. Their PR-00 scope describes that completed stage; later work requires the user's corresponding authorization. PR-01 architecture is approved in principle and its local closeout commit is authorized; all 11 TBDs remain open. This does not authorize PR-02/PR-09 implementation. Current scope/approval is recorded in [PR-01 closeout](docs/web-alpha/architecture/PR01-CLOSEOUT.md); never infer approval from a future task list.

- **Product and authority:** build the complete, invitation-only Classroom OS Web Alpha learning loop for 10–30 testers. Priority is the user's latest explicit decision → complete PRD v1.2 including its ASR addendum → approved ADR/contracts → implementation details. Read [Web index](docs/web-alpha/README.md), [architecture index](docs/web-alpha/architecture/README.md), requirement IDs and relevant conflicts. Do not invent features, silently change confirmed quotas/privacy/adoption rules, or treat a visual concept as a requirement. Record product changes and obtain explicit product approval.
- **Product isolation:** local v1.3.0 and Web have separate entrypoints, dependencies, versions, data and configuration. Never expose the local app by deleting `local_only`, instantiate its global `Classroom` for Web, or move real data/config/models. Shared logic extraction requires both product test gates.
- **Architecture:** follow the reviewed modular FastAPI/PostgreSQL/private-object design and independent React/TypeScript Web frontend. Domain logic depends on ports, not UI/ORM/provider internals. Owner comes from authenticated server context; classroom IDs are explicit. Five workflows, CAS/op receipts, durable ACK, immutable sources, candidate adoption and unknown-charge protection are contract invariants. Details live in architecture documents, not here.
- **Roles:** Astra owns architecture, technology decisions, versioned HTTP/WS contracts and cross-module review. Claude Code (Claude Desktop) leads PR-09a/09b/10a/10b and related frontend. Codex leads backend, database, ASR/AI, identity, infrastructure and integration tests. Terra is optional visual exploration; Claude integrates its output. Roles do not grant product, payment or release authority.
- **Contract collaboration:** [OpenAPI](docs/web-alpha/architecture/contracts/openapi.json) and [WS schema](docs/web-alpha/architecture/contracts/events.schema.json) are the shared boundary. Claude may use independent mocks and simulated audio/AI; mock results are not backend/real-device evidence. Contract changes require an Astra-reviewed version/changelog, matching fixtures, producer/consumer impact and compatibility notes. No private undocumented fields or silent enum changes.
- **PR workflow:** follow all 28 packages and gates in [implementation map](docs/web-alpha/architecture/08-IMPLEMENTATION.md). Scope each PR, cite requirement IDs, preserve failures and supply actual commands/results, compatibility, security/cost effects, open items and recovery steps. PR-11a precedes paid calls; PR-05a security approval precedes persistent private recovery. Passing design tests does not mean Web integration is implemented.
- **Git and worktrees:** inspect root/branch/HEAD/dirty state before editing; preserve user changes. Reuse a suitable checkout or use a separate worktree/task branch for each authorized package. Do not let two Agents edit the same files concurrently. Record base SHA, contract version and handoff evidence. Commit only when authorized, stage the explicit reviewed file set; never reset/clean another Agent's work or automatically push, merge, tag or release.
- **Validation:** retain original Windows Python/Node/DPAPI gates. Add real PostgreSQL/object/worker/browser tests where those mechanisms matter; do not replace them with dictionaries/DOM mocks. Docs-only changes need contract, traceability, diagram/link and protected-file checks; distinguish them from historical regressions and unrun remote CI. Use the relevant skill when useful, without broadening scope.
- **Safety and privacy:** no real classroom content, secrets, microphone, paid providers, production migration, public deployment or messaging without corresponding authorization. Synthetic defaults, no provider keys in browser/logs, no private content in normal logs, no Admin impersonation/content bypass. Unknown provider work is never automatically resent/refunded. Deletion revokes access before physical cleanup; backup recovery applies tombstones first.
- **Storage and handoff:** task-controlled dependencies/caches/intermediates stay in ignored `work/<pr>/`, outputs in `outputs/<pr>/`; durable architecture, decisions and review evidence stay in Git. Do not use C: as a selectable task storage default. [CLAUDE.md](CLAUDE.md) is only a thin entrypoint to these rules and the [frontend handoff](docs/web-alpha/architecture/09-CLAUDE-FRONTEND-HANDOFF.md), not a competing policy copy. Decisions must be recoverable from the repository, not only chat history.


## Independent Web repository (2026-11-05)

The latest user decision supersedes historical plans to merge Web work into the old repository's main. The formal remote is `https://github.com/Mumao233/classroom-os.git` (public, repository ID `1445898183`), and its formal branch is `main`. The sole formal local development entry remains `J:\class-assistant\classroom-os`.

- Continue subsequent Web development, tests and version management only in the new independent repository. Do not establish ongoing synchronization with `Mumao233/classroom-assistant` or write Web updates to its main, branches, tags, PRs or settings.
- Preserve approved commit history, historical URLs/SHAs/reports, Windows product behavior and all existing safety/collaboration constraints. Migration documentation is not authorization for new business packages, deployment, release, production database migration or paid services.
- Temporary migration clones are transport/evidence copies, not additional long-term development sources. Preserve other worktrees and user changes; shared Git configuration changes require all-worktree checks.
- PR-00 Actions proves only its actual Windows/Node/static gates. Keep historical local results separate; bootstrap-only, P3-01, other P3/TBD and real-service limitations remain open as recorded in [migration notes](docs/web-alpha/repository-migration/README.md).

## Authorized unattended pilot (2026-11-06)

- The user authorized automation infrastructure and one PR-06a **simulated** trial, including feature branches, commits, pushes and Draft PRs in this repository. This supersedes the historical PR-00 no-write stage only for this scope. No other new business package is enabled.
- Model execution uses the user's existing ChatGPT/Codex subscription locally. Extra API expenditure is **$0**; do not configure API keys, buy credits, switch billing providers, upload Codex authentication caches, or fall back to an API after an authentication/quota error. The earlier $50 API ceiling was replaced by this instruction.
- All new Git authors and committers must be `Mumao233 <325669781+Mumao233@users.noreply.github.com>`; Draft PR author must be `Mumao233`. Do not add Codex or other coauthor trailers.
- Task workers may only add files in the four PR-06a directories approved by `automation/prompts/06a.md`. They may not change this file, control scripts, workflow/configuration, existing tests, migrations, frozen contracts or Git metadata. Only the trusted controller publishes validated objects.
- Use a pinned clean control commit, isolated tracked-source checkout, restricted local executor and persistent limits. Maximum three repairs; unresolved calls block further invocation. Passing a simulation does not approve real ASR, budget, persistence, security or recovery integration.
- All PRs remain Draft. No automatic merge, deployment, release, tags, production operations or real business service calls. See [pilot operation guide](docs/web-alpha/overnight/README.md).
