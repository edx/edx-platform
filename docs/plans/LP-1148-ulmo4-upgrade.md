# Landing the ulmo.4+ upgrade (Django 5.2) on `release-ulmo`

Ticket: [LP-1148](https://2u-internal.atlassian.net/browse/LP-1148) ("Prepare and test edxapp Ulmo.1 branch" — the title is stale; the target is now ulmo.4+). Parent epic: [LP-1309](https://2u-internal.atlassian.net/browse/LP-1309) ("Ulmo.3 Deployment").

This plan, and `LP-1148-ulmo4-merge-audit.md`, ship in the upgrade PR itself: [edx/edx-platform#505](https://github.com/edx/edx-platform/pull/505), branch `robrap/lp1148-ulmo4-continue`. Keep both up to date in that branch as the work progresses; they land on `release-ulmo` with the upgrade. Delete both in a small follow-up PR once the upgrade is in production and stable — see `docs/plans/README.rst`.

Related: the upgrade-process playbook in edx-internal ([PR #14962](https://github.com/edx/edx-internal/pull/14962), `docs/openedx-upgrade-process/`, especially `05-edx-platform-ulmo3-batch.md` and the Django pre-flight item in `04-...` section B). That playbook treated this landing as a case study; this plan and the audit are the concrete execution of it.

## Context

- **Production** runs `edx/release-ulmo`, on Django `4.2.28`. Upstream `release/ulmo.1` through `ulmo.4` are all Django 5.2.x.
- **Previous attempts** to land ulmo.1 on `release-ulmo` were merged and reverted twice:
  - #212, merged and reverted on 2026-05-19 (`980550c446`, revert `f67142decf`).
  - #304, merged 2026-05-22 (`e45e7825ea`) and reverted 2026-05-27 (`3a8fdad2fd`, PR #311) after "errors on prod involving XBlocks".
- **Root cause of that XBlockSaveError:** `UnsignedBigIntAutoField` (the `StudentModule` primary key) reported `AutoField` as its internal type. Django 5.2's `IntegerFieldOverflow` check then silently dropped lookups for ids above 2147483647, and saving student state failed with "Forced update did not affect any rows". Fixed upstream (#39181) and on `release-ulmo` (#500, `0cfc2835be`).
- **`edx/edx/ulmo.3`** (last touched 2026-06-23) holds ulmo.1 re-applied (`010ea66478`, a revert of the revert) plus upstream `release/ulmo.3`. It was 214 commits behind `release-ulmo`.

### The revert trap (important for anything that merges into `release-ulmo`)

Because `release-ulmo` contains the reverted ulmo.1 merges, git considers ulmo.1 **already merged**. A plain `git merge openedx/release/ulmo` into `release-ulmo` therefore brings only the ulmo.1→ulmo.4 delta, silently leaving out all ulmo.1 content, including Django 5.2. Content only comes back through a revert of the revert, which `ulmo.3` carries. That is why the upgrade branch is built on `ulmo.3` rather than "from scratch".

The same trap applies to rollback: see [Rollout and rollback](#rollout-and-rollback).

## Decisions

| Decision | Choice |
|---|---|
| Target | **ulmo.4+**: upstream `openedx/release/ulmo` at `2efdce0760`, i.e. `release/ulmo.4` plus 3 later upstream fixes (stored-XSS sandboxing of course assets, Studio video-download resource bounds, course search by display number). Re-check for newer upstream commits before landing. |
| Django | Bump to **5.2.18** (security release) on top of upstream's 5.2.11, as its own commit so it is easy to drop. |
| Base | Continue from `edx/edx/ulmo.3`, not from scratch, because of the revert trap. A history-independent "from scratch" merge (explicit base `242a69d06b`) has 48 conflicted files; those were used as the audit's high-risk list instead of being hand-resolved twice. |
| PDF textbook viewer | Upstream pdf.js 5.7.284 plus the fork's guards (relative-URL only, dangerous-scheme sanitizing). All six options considered are in the commit message of `fc8f1f0a8c`; to be reviewed by the owners of fork PRs #51/#64/#86. |
| Branch naming | `robrap/` prefix. |
| Testing | CI on the PR, plus devstack for what CI cannot cover (see [Next steps](#next-steps)). |

## Current state

Upgrade branch: **`robrap/lp1148-ulmo4-continue`** (`edx/edx-platform`), draft PR [#505](https://github.com/edx/edx-platform/pull/505). First-parent history on top of `edx/edx/ulmo.3`:

| Commit | What |
|---|---|
| `fef5916109` | Merge `edx/release-ulmo` @ `0cfc2835be`. Note: its message describes the HLS port, but that file was not staged in this commit; the port is `687065c198`. |
| `fc8f1f0a8c` | Merge upstream `openedx/release/ulmo` @ `2efdce0760` (ulmo.4+). The commit message documents the PDF viewer options. |
| `6732d6fd28` | Port LP-1205 audio-description flag removal (#461) to the migrated video JS. |
| `687065c198` | Port the HLS fragment retry limit to the migrated video JS, and fix its spec. |
| `c56841c5e4` | Port the video language menu sizing (#215, JS half) to the migrated video JS. |
| `91b8e72823` | Drop the studio-frontend translations pull from the Makefile (upstream 28ab2ceb67). |
| `49b7adc344` + later docs commits | This plan, the merge audit, and the `docs/plans/` README. |

Status on 2026-10-06:

- Merges are complete and audited. See `LP-1148-ulmo4-merge-audit.md`: 4 real merge losses were found and fixed, nothing else was lost, and landing on `release-ulmo` is conflict-free and yields the branch's tree exactly.
- Django is at 5.2.11. The 5.2.18 bump has not been done.
- No tests have been run locally (no local Python environment). Draft PR #505 is open, so CI is running on this state, pre-Django-bump.

## Next steps

1. **Re-sync if `release-ulmo` or upstream moved.**
   - `git fetch edx openedx --tags`.
   - If `edx/release-ulmo` moved past `0cfc2835be`, merge it into the branch.
   - If `openedx/release/ulmo` moved past `2efdce0760`, merge it too.
   - Re-run the audit's survival check on the result (scripts are in the audit appendix).
   - For every merge: before committing, run `git diff --name-only --diff-filter=U` and `git status` to confirm that every hand-edited file is staged. Do not use `git checkout --theirs/--ours` on a whole file without first diffing that side against the merge base. Both mistakes happened once on this branch and were caught.
   - Remember that upstream moved the video JS from `xmodule/js/src/video/` to `xmodule/assets/video/public/js/`. Any new fork change to an old-path file must be ported by hand, because git will merge it into a stale copy or report a modify/delete conflict.
2. **Bump Django to 5.2.18**, as its own commit on the branch.
   - Create a Python 3.11 venv (pyenv has 3.11.10) and run `pip install -r requirements/pip-tools.txt`, then `make upgrade-package package=django`.
   - The constraints allow it: `Django<6.0` in `requirements/constraints.txt` and `common_constraints.txt`.
   - Do not run `make upgrade`: it re-downloads `common_constraints.txt` and recompiles everything.
   - The `compile-python-requirements.yml` workflow cannot do a single-package bump.
   - Review the diff (expect only `django==5.2.18` across `requirements/edx/*.txt` and `scripts/user_retirement/requirements/*.txt`), and say in the PR that this deviates from upstream ulmo.4.
3. ~~Push the branch and open a draft PR to `release-ulmo`.~~ Done: [#505](https://github.com/edx/edx-platform/pull/505). The PR description links this plan; do not duplicate the plan there.
4. **CI.** Fix failures as separate commits. Pay particular attention to:
   - `lms/djangoapps/courseware/tests/test_fields.py`
   - the video tests
   - `lms/djangoapps/staticbook/tests.py` (adapted to the fork's PDF guards)
   - query-count tests (ulmo.3 needed "Update queries expected")
   - `make lint-imports`, migrations checks, and `makemigrations --check --dry-run` for lms and cms
5. **Devstack checks**, the things CI cannot cover:
   - The previous prod failure: seed `courseware_studentmodule` `AUTO_INCREMENT` above 2^31 (e.g. `ALTER TABLE courseware_studentmodule AUTO_INCREMENT = 4240000000;`). Navigate into a unit and back in the Learning MFE, and confirm XBlock state saves without "Forced update did not affect any rows" (see the description of edx PR #495).
   - The fork's learner-state work that the branch carries: incremental loading of large assessment xblocks and hydrating learner state for paginated assessment children (`courseware/model_data.py`, `block_render.py`). Exercise a large problem bank or assessment.
   - Video: HLS playback, audio description (upload in Studio, playback in LMS), and the language menu height with many caption languages.
   - PDF textbooks: relative-URL books render in the pdf.js 5 viewer, chapter switching works, and an absolute `https://` URL does not render the viewer.
6. **Pre-flight items** from the edx-internal playbook (PR #14962 doc `04`, section B, Django 4.2→5.2) and its deployed-settings checks against `argocd/applications/edxapp-*/` in edx-internal. That repo, not Datadog, is the real deploy inventory.
7. **Owner reviews** (open items below), before merging.
8. **Write the rollout and rollback plan** (next section) into this document, then deploy to stage, then prod.
9. **Mark #505 ready for review and merge it.** The plan docs land with it.
10. **Once stable in production**, delete `docs/plans/LP-1148-*` in a follow-up PR, and update `docs/plans/README.rst`'s list.

## Rollout and rollback

To be completed before step 8. Points already known:

- **Watch:** XBlockSaveError, 500s on courseware state saves (`goto_position`, `xblock/handler`), Django 5.2 deprecation and runtime errors, video and PDF errors.
- **Prefer rolling back by redeploying the previous image** over reverting the merge on `release-ulmo`. Reverting the merge recreates the revert trap: re-landing would then need a revert of that revert, as `010ea66478` did for ulmo.1. If a revert is unavoidable, record here how to re-land.
- **Database migrations:** check whether any ulmo.1→ulmo.4 migrations are irreversible before relying on a rollback. A code-only rollback after forward-only migrations needs care.

## Open items for owning teams

1. **PDF textbook viewer** (owners of fork PRs #51/#64/#86): review the choice and the alternatives in the `fc8f1f0a8c` commit message.
   - The upstream patch adds the viewer's own origin to `HOSTED_VIEWER_ORIGINS`, which as read skips pdf.js's own cross-origin `file` check.
   - The fork guards do not block protocol-relative `//host` URLs.
   - The fork's #86 (empty `DEFAULT_URL`) no longer applies; confirm pdf.js 5's default-file behaviour.
2. **Forum bulk delete** (discussions owners): **not changed by this upgrade** (the branch matches prod), but worth knowing.
   - The fork's #97/#260 widened upstream 699c831fa3's global-staff-only gate to course Discussion Admins/Moderators.
   - With `course_or_org=org`, a moderator of one course can bulk-delete a learner's posts across the whole org.
   - Suggested narrow fix, separate from this upgrade: require global staff for org-scope bulk delete in `BulkDeleteUserPosts.post`.
3. **studio-frontend** (Studio owners): the branch drops it, following upstream, while `release-ulmo` still has 18 references. Confirm nothing still needs it.

## Follow-ups (separate from landing)

- Delete the unused RequireJS copy `xmodule/js/src/video/09_video_audio_description.js`. Upstream deleted that directory's other files; the live module is under `xmodule/assets/video/public/js/`.
- Optionally re-add the fork-only IPv6 and reserved-address cases to `TestValidateSAMLMetadataURL`. This is coverage only; the validator code is unchanged.
- Feed the methodology into the edx-internal playbook (v0.2): the hunk-level survival check, the revert trap, and the moved-files class of silent merge loss.
- Retitle LP-1148 to reflect ulmo.4+.
