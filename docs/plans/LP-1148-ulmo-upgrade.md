# Landing the ulmo upgrade (Django 5.2) on `release-ulmo`, in batches

Ticket: [LP-1148](https://2u-internal.atlassian.net/browse/LP-1148) ("Prepare and test edxapp Ulmo.1 branch"; the title is stale, see [Follow-ups](#follow-ups-separate-from-landing)). Parent epic: [LP-1309](https://2u-internal.atlassian.net/browse/LP-1309) ("Ulmo.3 Deployment").

This plan and the audit files next to it live in the **batch-1 PR** [#514](https://github.com/edx/edx-platform/pull/514) (branch `robrap/lp1148-ulmo1-batch1`) and are the single copy. They were moved here from the superseded reference PR [#505](https://github.com/edx/edx-platform/pull/505). Keep them up to date in whichever PR is current, and delete them in a small follow-up once the upgrade is in production and stable; see `docs/plans/README.rst`.

Related: the upgrade-process playbook in edx-internal ([PR #14962](https://github.com/edx/edx-internal/pull/14962), `docs/openedx-upgrade-process/`). It holds the reusable method; this plan is the concrete execution.

## Start here (handoff)

Written 2026-10-08 so that a fresh session can continue from this file alone. State at that time:

- `edx/release-ulmo` tip was `3c3fbbad34` (2026-10-08). Production runs `release-ulmo` on Django `4.2.28`.
- **Batch 1 = ulmo.1 only.** The branch exists and holds only these docs; **the code work has not started.**
- **Next action: the merge work** in [Next step: the merge work (batch 1)](#next-step-the-merge-work-batch-1).
- #505 (`robrap/lp1148-ulmo4-continue`) is a superseded reference branch; its docs are to be removed and the PR closed once this PR exists (see [What happens to #505](#what-happens-to-505)).
- **Housekeeping still pending** (do these early, they are quick): (a) repoint references to #505 as "source of truth" to #514: edx-internal#14962 (several places), LP-1148, LP-1309; (b) retitle LP-1148; (c) **close #505** once (a) is done (its docs were removed and its description now points to #514; keep its branch); (d) ask `release-ulmo` owners for a soft freeze once manual testing starts.
- Nothing about the decisions below lives only in a conversation; if something here looks wrong, check it against git (every claim cites commits).

## Where things are

| What | Ref |
|---|---|
| Batch-1 branch (this PR, [#514](https://github.com/edx/edx-platform/pull/514)) | `robrap/lp1148-ulmo1-batch1` on `edx/edx-platform`, based on `edx/release-ulmo` `3c3fbbad34` |
| Deploy branch | `edx/release-ulmo` |
| Upstream | `openedx/release/ulmo` (tags `release/ulmo.1` 2026-01-15, `.2` 2026-02-18, `.3` 2026-04-24, `.4` 2026-07-13; tip `2efdce0760`) |
| Reference branch #505 | `robrap/lp1148-ulmo4-continue` @ `be17bd60ca`, draft PR [#505](https://github.com/edx/edx-platform/pull/505). ulmo.3 base plus ulmo.4+ merge. Source of ports, the 4-loss audit, and a second opinion on conflicts. |
| `edx/edx/ulmo.1` | `010ea66478`, the revert of the revert of the May deploy (see [History](#history-what-was-deployed)). |
| `edx/edx/ulmo.3` | Someone's test branch: ulmo.1 re-applied + ulmo.2 + ulmo.3. Never landed. Not proven. Reference only. |
| Audit of #505 | `LP-1148-ulmo4-merge-audit.md` (this directory). The batch-1 audit goes in `LP-1148-ulmo1-merge-audit.md`, not yet created. |
| Playbook | edx-internal PR #14962, `docs/openedx-upgrade-process/` |

## History: what was deployed

All of this is ordinary linear history on `release-ulmo`; nothing was force-pushed.

| Date | Commit | What |
|---|---|---|
| 2026-05-19 | `980550c446` | Merged #212 (ulmo.1). |
| 2026-05-19 | `f67142decf` | Reverted it (#300): casbin migration error and h5p xblock issue. |
| 2026-05-22 | `e45e7825ea` | Merged #304 (head `2b5f74b19e`, branch `edx/ulmo.1`). Its head contained `0972c91dc4`, a revert of the revert, so the content came back. Pinned `django==5.2.7`; contains upstream `release/ulmo.1` and nothing later (ulmo.2/.3/.4 are not ancestors). |
| 2026-05-27 | `3a8fdad2fd` | Reverted it (#311): "errors on prod involving XBlocks". |
| 2026-05-29 | `010ea66478` | Revert of that revert, on top of `release-ulmo` `08d667b9c7`. Now the tip of `edx/edx/ulmo.1`. **Not in `release-ulmo`.** Differs from `e45e7825ea` in 3 files only (unrelated #310/#312 config). Does not have #500. |

- **Only ulmo.1 has ever run in prod** (about five days). ulmo.2 and later never have. The earlier idea that ulmo.3 was deployed is wrong.
- **Root cause of the XBlockSaveError:** `UnsignedBigIntAutoField` (the `StudentModule` primary key) reported `AutoField` as its internal type. Django 5.2's `IntegerFieldOverflow` check silently dropped lookups for ids above 2147483647, and saving student state failed with "Forced update did not affect any rows". Fixed upstream (#39181) and on `release-ulmo` (#500, `0cfc2835be`). This is the one bug found in May, and it was in upstream-adjacent code, not a conflict resolution. Five days of prod is weak evidence that the original merge resolutions are right.

### The revert trap (important for anything that merges into `release-ulmo`)

A revert of a merge works. The trap comes afterwards: once the ulmo.1 merge is reverted, git treats those commits as already merged, so a plain merge of `openedx/release/ulmo` (or anything built on ulmo.1) brings only what came **after** ulmo.1 and silently leaves out all ulmo.1 content, including Django 5.2. The only way back is a revert of the revert, which is what #304 and `010ea66478` did. If batch 1 lands and is reverted again, the trap comes back and the next attempt needs another revert of the revert.

Once ulmo.1 is genuinely in `release-ulmo`, the trap is gone and later batches are plain merges of upstream tags.

## Decisions

| Decision | Choice |
|---|---|
| Batching | Small batches over one big catch-up (edx-internal ADR 0001). Point releases are **not** assumed safe: each batch gets its own risk review. |
| Batch 1 | **ulmo.1 only**, the only point that has run in prod. Built as: fresh branch from the `release-ulmo` tip, `git revert 3a8fdad2fd` (the revert of the revert), the ports below, the Django bump. Does **not** use `edx/ulmo.3` as a base. |
| Batch 2+ | Remaining `openedx/release/ulmo` (ulmo.2 through ulmo.4+) merged straight into `release-ulmo`, by tag or in one go. This is a plain merge once batch 1 has landed. Do this before starting verawood. |
| Django | Batch 1 is on upstream's 5.2.7; bump to **5.2.18** (security release) as its own commit so it is easy to drop. Use the `upgrade-one-python-dependency.yml` workflow (it produced #506 for #505); do not run `make upgrade`. |
| Cross-checks | Treat `e45e7825ea` / `edx/ulmo.1`, `edx/ulmo.3` and #505 as second opinions, never as truth. See [Cross-checks and the disagreement rule](#cross-checks-and-the-disagreement-rule). |
| Rollback | Redeploy the previous image first. Revert the merge only if `release-ulmo` has to keep shipping other changes meanwhile, and record that re-landing then needs a revert of the revert. See [Rollout and rollback](#rollout-and-rollback). |
| Branching (long term) | Keep `release-ulmo` as the deploy branch through the ulmo catch-up. Then cut `release-verawood` **from `release-ulmo`** (not from upstream) and merge `openedx/release/verawood` into it in batches; revisit `master` only after reaching verawood parity. While catching up, watch `openedx/release/verawood` for security/bug fixes and cherry-pick urgent ones (as #319, #333 and #481 were). This is open question 4 in the edx-internal playbook; recommend it there. |
| Testing | CI on the PR, devstack for what CI cannot cover, probably a sandbox for manual testing, **stage** near the very end (anything needing stage data, such as the migration-state queries, belongs there). |
| Docs | In the batch-1 PR only. Later batches carry them forward. |
| Branch naming | `robrap/` prefix. |

## What happens to #505

#505 was built on ulmo.3 with a single ulmo.4+ merge. It cannot sensibly be batch 2: after batch 1 lands, its ulmo.3-based history would conflict heavily, and a fresh merge of the upstream tags is simpler. It stays as a **reference**:

1. Docs move to this PR first (this PR). Then #505's branch removes its copies in a follow-up commit and its title/description say "superseded, reference only", pointing here.
2. Repoint references: edx-internal#14962 (several places call #505 the source of truth), LP-1148 and LP-1309.
3. **Close #505.** Do not delete its branch until batch 2 no longer needs it as a reference.

#505's reusable content: the ports (`6732d6fd28`, `687065c198`, `c56841c5e4`, `91b8e72823`), the merge audit and its scripts, the fork's PDF-viewer analysis (commit message of `fc8f1f0a8c`, for batch 2), `contentstore 0014` analysis, and its conflict resolutions.

## Next step: the merge work (batch 1)

This is the meaty work. Do it in a throwaway worktree first to measure the conflicts, then on the branch.

1. **Sync.** `git fetch edx openedx --tags`. Base is `edx/release-ulmo`; if it moved past `3c3fbbad34`, rebase/merge so the tested tree is the tree that lands.
2. **Revert the revert.** `git revert -m1 3a8fdad2fd` on the branch. `release-ulmo` has moved about 230 commits since `010ea66478`, so expect conflicts. Resolve using, in this order of preference: the fork's own intent (divergence registry in the playbook, `03-...registry`), then the resolutions already made in `010ea66478` and, for the same files, in #505's `fef5916109` (merge of `release-ulmo`) and `fc8f1f0a8c`. Do not take a side wholesale: do not use `git checkout --theirs/--ours` on a whole file without diffing that side against the merge base, and before committing run `git diff --name-only --diff-filter=U` and `git status` to confirm every hand-edited file is staged. (Both mistakes happened once in #505 and were caught.)
3. **Ports** (video JS moved from `xmodule/js/src/video/` to `xmodule/assets/video/public/js/` in ulmo.1, so fork changes to the old paths are not carried by git): cherry-pick or redo `6732d6fd28` (LP-1205 audio-description flag removal, #461), `687065c198` (HLS fragment retry limit and its spec), `c56841c5e4` (language menu sizing, #215 JS half), `91b8e72823` (drop the studio-frontend translations pull from the Makefile; upstream `28ab2ceb67` is in ulmo.1). Check whether `release-ulmo` gained any other fork changes to old-path video files since May (`git log 010ea66478..edx/release-ulmo -- xmodule/js/src/video xmodule/assets/video`; at last look: `353a5da311`, `43300f9143`, `fb8bc85234`). Delete the dead RequireJS copy follow-up later.
4. **Take only the audit's merge-loss fixes that belong to the ulmo.1 range**, and skip ulmo.4-only fixes (the pycodestyle E302/E303 fixes in `test_extract_archive.py` / `test_videos.py`, and the `.annotation_safe_list.yml` entry for `oel_publishing.PublishableEntityVersionDependency`) unless CI shows they are needed. The `xmodule/` CODEOWNERS fix in #505 was for upstream's community owners; check whether ulmo.1 does the same.
5. **Django bump** to 5.2.18 as its own commit (see Decisions).
6. **Audit.** Re-run the audit scripts (appendix of `LP-1148-ulmo4-merge-audit.md`: `survival.sh`, `hunks.py`, the dead-copy sweep, `lockfile_downgrades.py`) with the ulmo.1 range: the pre-ulmo.1 common base `242a69d06b`, upstream `release/ulmo.1`, fork `edx/release-ulmo`. Write results in a new `LP-1148-ulmo1-merge-audit.md`. Also run the history-independent merge (`git merge-recursive 242a69d06b -- edx/release-ulmo release/ulmo.1`, 48 conflicted files at ulmo.4+; recount for ulmo.1) as the independent reference.
7. **Cross-check** per the next section.
8. **Then**: push, open the batch-1 PR (draft) with these docs, CI, devstack, sandbox, stage, owner reviews, rollout plan, deploy.

### Cross-checks and the disagreement rule

Comparisons to run (record results in the batch-1 audit file):

| Compare | Expectation |
|---|---|
| candidate vs `e45e7825ea` (what ran in May) | Every difference explained by `release-ulmo` drift, #500, the ports, or the Django bump. Anything else is a disagreement. |
| candidate vs `010ea66478` | Same, plus the revert-of-revert resolutions. |
| candidate vs #505 (`be17bd60ca`) | Differences should be only the upstream ulmo.1 → `2efdce0760` delta, the pdf.js/ulmo.4 changes, docs, and Django 5.2.11/5.2.18. |
| candidate vs `edx/edx/ulmo.3` | Differences = ulmo.2/3 content plus whatever ulmo.3's author fixed ("Clean up some merge errors", "Update queries expected"); each such fix is a hint to check, not an instruction. |
| candidate vs the history-independent merge | The independent reference; the 48-file list is the high-risk list. |
| Lockfile downgrade check | No production (`base.txt`) downgrades against `release-ulmo`; investigate anything. |
| Batch 2 later (merge `openedx/release/ulmo` into `release-ulmo`) vs #505's tree | A nice consistency check, not a pass/fail: merge history differs so trees need not be identical. |

**Disagreement rule.** None of the references is proven, and `e45e7825ea`/`edx/ulmo.1`, `edx/ulmo.3` and #505 are not independent of each other (ulmo.3 and #505 inherit the May resolutions). For each differing hunk, decide who got it right:
- Classify it as upstream intent or a deliberate fork-only change (use the divergence registry and `git log` on the fork side).
- A fork-only change wins unless upstream removed the feature deliberately.
- Break ties with tests/CI, how the code behaved in prod in May, and the owner of the fork PR.
- Record each ruling (hunk, who was right, why) in the audit file.

### Ulmo.1-regression check

Not every post-ulmo.1 upstream commit is safe to skip or take. Before finalizing batch 1, read the ulmo.2, ulmo.3 and ulmo.4 release notes and grep post-ulmo.1 commit bodies for "regression", "fixes #" and "fixes ulmo.1", and ask whether any fixes a bug **introduced by** ulmo.1 that `release-ulmo` would not have. First pass (by commit message only, not code): none found; the fixes below target code `release-ulmo` already has.

## Post-ulmo.1 changes not yet in `release-ulmo` (batch 2 risk list)

Found with `git patch-id` against the last 400 `release-ulmo` commits (ancestry misses cherry-picks). Caveat: only unmodified cherry-picks match, so a modified cherry-pick shows up as "not in". Re-run on fresh refs before relying on it.

**Already in `release-ulmo` by content** (not new): asset-sandbox XSS fix (`2efdce0760` = `723014f37b`, #481, 2026-09-21, with the per-course `course_assets.allow_unsafe_asset_rendering` flag), LTI nonce replay (+style fix), `set_course_mode_price` staff gate (#319), discussion-email style-tag removal, survey redirect (`fca21c955f`), `activation_key` removal from the account API.

**Not in `release-ulmo`** (new to prod when batch 2 lands):

| Commit | What | Coordination note |
|---|---|---|
| `46c543590c` | Gate support course-team GET on can-manage authorization | Behaviour change for support users. |
| `52f2b3bb3e` | Escape user input in notification content templates | Rendering change. |
| `241b914a19`, `c99de016f1` | Studio video-download SSRF block, resource bounds | Behaviour change for large/remote downloads. |
| `6291b6badd` (+`4f15b6d4e0`) | SAML metadata SSRF block | The fork has its own validator (`TestValidateSAMLMetadataURL`); see follow-ups. |
| `c5ab9b34a3` | library_content transformer for all ItemBankMixin xblocks | Targets code that already exists on `release-ulmo`. |
| `97de0586d8` | `safe_extractall` commonpath | |
| `f7f95089fe` | Sidebar cache key uses block-structure version | `release-ulmo` still keys on `course_version`. |
| `4d2e220d6f` | Vendored pdf.js 1.0.907 → 5.7.284 | Fork PDF guards (#51/#64/#86); see [Open items](#open-items-for-owning-teams). **Not in batch 1.** |
| `723be60058` | openedx-forum 0.3.8 → 0.4.1 | Pinned-NULL thread sort bug. |
| `6fd5f947a3`, `e66e4ebbf7` | Display number/org in course serializer; search by display number | |
| Django 5.2.7 → 5.2.11, ora2 6.17.1 → 6.17.2, edx-search 4.3.0 → 4.4.0 | Requirements | `release-ulmo` already has ora2 6.17.2. |

Behaviour-changing items (support gate, video SSRF/limits, SAML SSRF, notification escaping) need coordination with their owners before batch 2; they are the reason not to deploy everything at once. Whether #481 itself is deployed is unknown from the repo; check gocd/argocd in edx-internal.

## Blocking tickets (outside this branch)

Two other tickets must be done before the upgrade is deployed. Both are tracked in Jira, not here.

| Ticket | What | Affects testing? |
|---|---|---|
| LP-1304 (Maintenance, P3): Ensure edxapp private requirements are Django 5.2 compatible | The private production/stage plugin pins live in edx-internal, `argocd/applications/edxapp-lms/requirements/private_requirements.txt` (also used by CMS, BOMS-233; deployed via `edx/internal-dockerfiles`). `ai-aside==3.8.9` and `federated-content-connector==1.7.0` are already tested on Django 5.2 upstream and are just older pins. `platform-plugin-braze` (git-pinned commit) and `learner-pathway-progress<1.3.5` are **real blockers**: no Django 5.2 in their test matrix, and still on Python 3.8. Some need their own tickets and upgrades. | Yes. CI installs the repo's own requirements, not these private ones. A sandbox or stage deploy only tests Django 5.2 against the plugins if they are installed there. |
| LP-1150 (Maintenance, priority unset): Ensure web certificates are tested for Django 5.2 compatibility | Affects the DB only; certainly before deploying. | No. |

Open question from LP-1304: for plugins already Django 5.2-compatible (`ai-aside`, `federated-content-connector`), is it safe to bring in the newer version **before** the edxapp upgrade, while prod is still on Django 4.2? Depends on whether each package's supported-Django matrix still includes 4.2 at the newer version; check per package.

## Remaining steps (after the merge work)

1. **Re-sync gate.** Manual testing must run against the latest `release-ulmo` plus this branch. Cadence:
   - Until manual testing starts: check once a day (`git fetch edx --tags && git fetch openedx`, then `git log --oneline HEAD..edx/release-ulmo`), and merge promptly. Small merges are easier to audit. Merge the branch tip; do not cherry-pick.
   - Sync gate right before manual testing starts, again before marking ready, and again right before merging: merge, re-run CI, record the tested `release-ulmo` and upstream SHAs here, confirm the PR is still conflict-free.
   - Freeze during a manual test pass; merge new commits at the start of the next pass, re-testing what they touch (video JS, courseware state, PDF viewer).
   - Ask the `release-ulmo` owners for a soft freeze (urgent fixes only) from the start of manual testing until the PR merges.
   - Wide commits (like #503, 39 files) need the survival check most.
   - Re-run the survival check and the lockfile downgrade check on every sync.
2. **CI.** Fix failures as separate commits. Pay particular attention to `lms/djangoapps/courseware/tests/test_fields.py`, the video tests, query-count tests (ulmo.3 needed "Update queries expected"), `make lint-imports`, migrations checks and `makemigrations --check --dry-run` for lms and cms. `make check_keywords` runs after `pii_check` in the same job.
3. **Migration state in stage and prod.** `release-ulmo` was deployed with ulmo.1 in May (2026-05-22 to 2026-05-27) and rolled back by revert. Rolling back code never un-applies migrations, so what the databases have applied is unknown from the repo. Run read-only on stage and prod and record results (with dates) here:
   1. `SELECT app, name, applied FROM django_migrations WHERE applied >= '2026-05-15' ORDER BY applied;` Did May apply the ulmo.1 migrations?
   2. `showmigrations --plan` for lms and cms with the batch-1 code against a stage snapshot.
   3. If `contentstore 0014` is already applied in prod: check for ComponentLink/ContainerLink errors since May, and whether prod uses library upstream-sync at all.
   4. `SELECT VERSION();` on `prod-edx-edxapp.rds.edx.org` (argocd shows `django.db.backends.mysql`, which cannot tell MySQL from MariaDB). The five `*_mariadb_uuid_conversion` migrations are no-ops unless the version contains "mariadb".
   5. Row counts for `contentstore_componentlink`, `contentstore_containerlink` and `modulestore_migrator_*` (lock and duration risk of `0014` and the migrator migrations).

   Migrations that the ulmo.1 range adds relative to pre-May `release-ulmo`: `contentstore 0014`, `modulestore_migrator 0002`, `0003`, `0004`, `0006` (`0004` has a squash-style name but no `replaces`), `survey_report 0006`, and five MariaDB conversions (`student 0048`, `entitlements 0017`, `course_goals 0010`, `program_enrollments 0012`, `external_user_ids 0009`). `announcements 0001_initial` is deleted with the app; its `django_migrations` row will be stale, harmless unless a migration depends on it. `release/ulmo.1` and `release/ulmo.4` have identical migration file sets, so batch 2 adds none. Re-check the list against the batch-1 diff (it was computed for #505). The fork migrations `third_party_auth 0014/0015`, `support 0007` and `course_overviews 0030` are already in `release-ulmo`.
4. **Devstack checks**, the things CI cannot cover. Do the sync gate first.
   - The previous prod failure: seed `courseware_studentmodule` `AUTO_INCREMENT` above 2^31 (e.g. `ALTER TABLE courseware_studentmodule AUTO_INCREMENT = 4240000000;`). Navigate into a unit and back in the Learning MFE and confirm XBlock state saves without "Forced update did not affect any rows" (see edx PR #495).
   - The fork's learner-state work: incremental loading of large assessment xblocks and hydrating learner state for paginated assessment children (`courseware/model_data.py`, `block_render.py`). Exercise a large problem bank or assessment.
   - Video: HLS playback, audio description (upload in Studio, playback in LMS), language menu height with many caption languages.
   - PDF textbooks: batch 1 keeps the old pdf.js plus the fork's guards; confirm they still work.
5. **Pre-flight items** from the playbook (PR #14962 doc `04`, section B, Django 4.2→5.2) and its deployed-settings checks against `argocd/applications/edxapp-*/` in edx-internal (the real deploy inventory, not Datadog). Verified on 2026-10-06 against #505:
   - **Storage settings:** argocd prod sets the legacy `DEFAULT_FILE_STORAGE` and `STATICFILES_STORAGE`, which Django 5.1 removed. They are still honoured because `lms/envs/production.py` and `cms/envs/production.py` map them into `STORAGES` ("For backward compatibility"). No `get_storage_class` callers remain (only a docstring in `common/djangoapps/util/storage.py`).
   - **Fork-only settings** from the learner-state and bulk-unenroll work are in `lms/envs/common.py`: `INCREMENTAL_LOAD_PROBLEM_THRESHOLD`, `INCREMENTAL_LOAD_EAGER_COUNT`, `XBLOCK_CHILDREN_BATCH_MAX`, `BULK_UNENROLL_*`. Re-confirm on the batch-1 tree.
   - **Intentionally deferred commits:** the only `temp:` commit relative to `release-ulmo` was `582e345108` (SAML SSRF revert, on `edx/ulmo.3`). Not applicable to batch 1.
   - **MariaDB UUID migrations** are no-ops unless the engine is MariaDB; unconfirmed until query 4 above.
   - **Breaking changes:** see [Breaking changes in the range](#breaking-changes-in-the-range).
   - `.github`: in #505 all differences from `release-ulmo` were modifications (mostly action version bumps), no added or removed workflows, and the `django-version: "5.2"` matrix leg of `unit-tests.yml` was dropped since `pinned` is now 5.2, so the "dj=pinned" jobs are the Django 5.2 tests. Re-check for batch 1.
6. **Owner reviews** (open items below), before merging.
7. **Write the rollout and rollback plan** (below), then deploy to stage, then prod. LP-1304 and LP-1150 must be done first.
8. **Mark the PR ready and merge it.** Do the sync gate first, and again just before merging.
9. **Once stable in production**, delete `docs/plans/LP-1148-*` in a follow-up PR, and update `docs/plans/README.rst`. First make sure every item in [Feed to playbook](#feed-to-playbook) is resolved with an edx-internal edit or consciously dropped.

## Breaking changes in the range

The merge audit checks that nothing was lost in merging. It does not check what upstream deliberately broke. These are the commits marked `!` in `242a69d06b..openedx/release/ulmo` (the pre-ulmo.1 base, not the merge-base with `release-ulmo`, which spans only 25 commits because of the revert trap). The `BREAKING CHANGE:` footers were also searched and found nothing beyond these. **All rows except `fca21c955f` are in ulmo.1 and so are in batch 1.**

| Commit | What | Check before landing |
|---|---|---|
| `ae8996f68b` | Django 5.2 | Covered by CI, devstack and the storage-settings check. |
| `0077058e37`, `e64d4cee8d`, `fcfa4138fd` | Legacy Studio home, course outline and files/uploads pages removed, with their `legacy_studio.*` waffle flags | Forked authoring MFE works without the legacy pages. No DB-backed course/org overrides still selecting a legacy page (needs the toggles API or DB). |
| `20bc7113e3` | Studio Maintenance and Announcements app removed (`openedx/features/announcements`) | Nothing in fork templates or menus links to it. argocd prod config has no reference. The app's migration is deleted. |
| `4c051378d0` | Last calls to `cs_comments_service` removed | The audit covers `thread.py` (the fork keeps its soft-delete `_delete_thread`). Prod still sets `COMMENTS_SERVICE_URL`/`KEY` in argocd; confirm nothing in the branch still reads them. |
| `f7a1a9d990` | `version` removed from the library serializer | Forked authoring MFE does not read `version`. |
| `09e86e24b2` | `top_level_downstream_parent_key` changed from a Dict field to a String field | No migration; touches the upstream-sync code (`cms/lib/xblock/upstream_sync.py`, `contentstore/tasks.py`, `xmodule/util/keys.py`). Confirm whether prod has downstream blocks that stored the old dict form, and whether the new code reads them. Not yet investigated. |
| `fca21c955f` | Survey `redirect_url` (GHSA-2843-x998-f8r2) | Not in ulmo.1 (batch 2). Already in prod independently as `e46653783d`; the files are identical. No action. Verify by content, not ancestry. |

Cross-repo: the first three rows and `f7a1a9d990` change what the authoring MFE can call. Confirm the forked `frontend-app-authoring` (and `frontend-app-learner-dashboard` where it touches the same APIs) before deploying, and decide the deploy order if a fix is needed on the MFE side.

## Rollout and rollback

To be completed before deploying. Points already known:

- **Watch:** XBlockSaveError, 500s on courseware state saves (`goto_position`, `xblock/handler`), Django 5.2 deprecation and runtime errors, video and PDF errors.
- **Redeploy the previous image first** for a fast rollback: it touches no git history. While the upgrade merge is in `release-ulmo`, though, any other deploy from that branch ships the upgrade too. If `release-ulmo` must keep shipping unrelated changes, revert the merge (as #300 and #311 did) and write down that re-landing needs a **revert of the revert** (as `0972c91dc4` and `010ea66478` did).
- **Database migrations:** check whether any batch migrations are irreversible before relying on a rollback. A code-only rollback after forward-only migrations needs care.
- **`contentstore 0014` makes a code-only rollback unsafe.** It drops `downstream_is_modified` from `ComponentLink` and `ContainerLink` and adds `downstream_customized`. The previous image (`release-ulmo`) still expects `downstream_is_modified`, so after this migration is applied, redeploying it breaks anything that queries those tables. The options are to reverse-migrate with `migrate contentstore 0013` (the reverse re-adds the column but loses `downstream_customized` data), or to accept that library upstream-sync is broken until forward again. Decide before deploying, using the migration-state results (are the tables used, how big are they, was `0014` already applied in May). If they are in use, consider splitting the migration out (expand now, contract later), as the playbook's principles suggest.
- Because ulmo.1 was already deployed once, prod may already be past some of these migrations. That is not a rollback hazard for this batch, but the first-deploy behaviour of `0014` may already have been exercised in May.

## Open items for owning teams

1. **PDF textbook viewer** (owners of fork PRs #51/#64/#86): applies to **batch 2** (pdf.js 5.7.284, `4d2e220d6f`), not batch 1. Review the choice and the alternatives in the commit message of `fc8f1f0a8c` on #505.
   - The upstream patch adds the viewer's own origin to `HOSTED_VIEWER_ORIGINS`, which as read skips pdf.js's own cross-origin `file` check.
   - The fork guards do not block protocol-relative `//host` URLs.
   - The fork's #86 (empty `DEFAULT_URL`) no longer applies; confirm pdf.js 5's default-file behaviour.
2. **Forum bulk delete** (discussions owners): **not changed by this upgrade**, but worth knowing.
   - The fork's #97/#260 widened upstream 699c831fa3's global-staff-only gate to course Discussion Admins/Moderators.
   - With `course_or_org=org`, a moderator of one course can bulk-delete a learner's posts across the whole org.
   - Suggested narrow fix, separate from this upgrade: require global staff for org-scope bulk delete in `BulkDeleteUserPosts.post`.
3. **studio-frontend** (Studio owners): the upgrade drops it, following upstream, while `release-ulmo` still has 18 references. Confirm nothing still needs it.
4. **Authoring MFE and legacy Studio removal** (Studio/authoring owners): see [Breaking changes in the range](#breaking-changes-in-the-range). Confirm the forked `frontend-app-authoring` does not depend on the removed legacy home, course outline or files pages, or on the library serializer `version` field. Also check for DB-backed `legacy_studio.*` course/org waffle overrides.
5. **Announcements app removed** (whoever owns the learner-facing site content): nothing in argocd prod config references it, but confirm no fork template, menu or link depends on `openedx/features/announcements`.
6. **Batch-2 behaviour changes** (support, Studio, SSO and notification owners): see the post-ulmo.1 table above; coordinate before batch 2.

## Follow-ups (separate from landing)

- Delete the unused RequireJS copy `xmodule/js/src/video/09_video_audio_description.js`. Upstream deleted that directory's other files; the live module is under `xmodule/assets/video/public/js/`.
- Optionally re-add the fork-only IPv6 and reserved-address cases to `TestValidateSAMLMetadataURL`. Coverage only; the validator code is unchanged.
- Retitle LP-1148 (still says "Ulmo.1 branch", which is now accurate for batch 1; revisit after batch 2).
- Remove the Open edX tutorial workflow `.github/workflows/check-for-tutorial-prs.yml` from `release-ulmo` (it comments on any PR touching `lms/templates/dashboard.html`). Tracked in separate PR [#507](https://github.com/edx/edx-platform/pull/507).
- Report upstream that openedx-learning 0.30.2's `PublishableEntityVersionDependency` docstring says `.. no_pii` without the colon (fixed on openedx-learning main).
- Report the two pycodestyle errors upstream (E302 in `openedx/core/lib/tests/test_extract_archive.py`, E303 in `cms/djangoapps/contentstore/rest_api/v1/views/tests/test_videos.py`, on `openedx/release/ulmo`) so the next upstream merge doesn't conflict with our whitespace fix.

## Feed to playbook

The edx-internal playbook ([PR #14962](https://github.com/edx/edx-internal/pull/14962)) is for learning how to continue upgrades; this plan is the source of truth for landing ulmo. Lessons that belong there, so that `docs/plans/LP-1148-*` can be deleted without losing them. Mark each one resolved when the edx-internal edit has merged, or dropped with a reason.

| Lesson | Playbook home | Status |
|---|---|---|
| Hunk-level survival check, and the moved-files / dead-copy class of silent merge loss (scripts are in the #505 audit appendix; copy them, since the appendix will be deleted) | `01-playbook.md` | Open |
| The revert trap: a plain merge omits content that was merged and reverted; the fix is a revert of the revert; also applies to rollback | `01-playbook.md` | Open |
| Do not assume point releases are safe: diff candidate changes against prod by `git patch-id` (not ancestry, which misses cherry-picks) and review behaviour-changing fixes (per-course flags, SSRF gates, auth gates) for coordination; split batches at the last point that has run in prod | `01-playbook.md` | Open |
| Do not build on someone's unproven test branch (`edx/ulmo.3`); build on `release-ulmo` plus prod-tested content, and use test branches only as cross-checks, with a disagreement rule | `01-playbook.md` | Open |
| Breaking-commit detection needs the true prior content base. With reverts, the merge-base spans 25 commits here instead of 157 | `01-playbook.md`, breaking-commit detection | Open |
| Prior-deploy state: when an earlier attempt was deployed and rolled back, query `django_migrations` in every environment before re-landing | `01-playbook.md`, migration review | Open |
| Rollback section: a code-only rollback is unsafe after contract-phase migrations (`contentstore 0014`); image rollback is only a stopgap while the branch keeps shipping | `01-playbook.md`, new section | Open |
| MariaDB UUID migrations are no-ops on MySQL but are in this batch; "not applicable" assumed the engine | `04-...preflight`, `02-...digest` | Open |
| `openedx/features/announcements` is retired upstream, so it is not a pluginize candidate; batch-state wording in registry rows 3 and 16 belongs here, not there | `03-...registry` | Open |
| `05-...ulmo3-batch` open review items are superseded by the batch-1/batch-2 split | `05-...ulmo3-batch` | Open |
| Branching strategy: keep `release-ulmo` through the ulmo catch-up, then cut `release-verawood` from it and merge upstream in batches; revisit `master` at parity (open question 4) | `00-index.md` | Open |
| Lockfile downgrade check at every sync gate (script is in the audit appendix; also copied to the playbook) | `01-playbook.md`, `scripts/` | Done in edx-internal#14962 |
| First retrospective entry: the playbook, applied before any deploy, surfaced the breaking-change inventory and the rollback caveat that the merge audit alone missed | `01-playbook.md`, retrospectives | Open |
