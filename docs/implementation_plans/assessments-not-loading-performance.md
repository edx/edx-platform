# Implementation Plan: Large Library Content / Assessment Render Performance

**Status:** Draft  
**Branch (A1 in progress):** `mraman-2U/assessments-not-loading`  
**Code owner:** Aurora (`lms.djangoapps.courseware`)  
**Related incident:** IBM Cybfun mock exam vertical timeout — `render_xblock` ~77s, nginx 504 at 60s, `xb_user_state.get_many` ~36s for 359 block keys  

---

## Problem statement

Large quiz verticals that use **Library Content** / **Item Bank** blocks can attach many candidate CAPA problems in modulestore while showing only a subset per learner (`max_count`). Today:

1. **`FieldDataCache.add_block_descendents`** walks **`get_children()`** for every block, prefetching user state for **all** modulestore children—not only the learner’s selected subset (`get_child_blocks()`).
2. **`DjangoXBlockUserStateClient.get_many`** loads full JSON state for every requested key and parses it synchronously in the request thread.
3. **`render_xblock`** renders the entire vertical (90+ CAPA blocks) in one synchronous HTTP response, exceeding nginx upstream timeouts.

The Learning MFE loads units via a **single iframe** to `/xblock/{vertical_id}`; there is no incremental problem loading today.

---

## Goals

| Goal | Target |
|------|--------|
| Reduce user-state prefetch breadth | `xb_user_state.get_many.blocks_requested` scales with `max_count`, not library size |
| Reduce user-state prefetch latency | p95 `get_many` duration **&lt; 10s** for 90-problem configs (after A1–A3) |
| First meaningful paint | **&lt; 5s** TTFB for large quiz verticals (after Phase 1–2) |
| Avoid new bad configs | Studio warning when `max_count` &gt; threshold |
| No regression | Learner sees correct problem count; selection/analytics unchanged on first visit |

---

## Non-goals (this plan)

- Rewriting CAPA in React
- Per-problem iframes in Learning MFE (rejected pattern; browser perf)
- Fixing unrelated prod issues (e.g. `translatable_xblocks` 500s) unless they block testing
- edx-exams / special-exam registration (not root cause for graded library quizzes)

---

## Implementation order (phases)

```text
Phase A1 ──► Phase A2+A3 ──► Phase B1 ──► Phase B2 ──► Phase C
(FieldDataCache) (get_many)   (shell API)   (MFE lazy)  (CMS guardrails)
     │              │              │              │
     └──────────────┴──────────────┴──────────────┘
              LMS (edx-platform)        frontend-app-learning
```

Each phase is independently shippable; later phases depend on earlier ones only for **full** UX improvement, not for correctness of A1.

---

## Phase A1 — Dynamic children in `FieldDataCache`

**Priority:** P0 — ship first  
**Repos:** `edx-platform` only  
**MFE changes:** None  

### Summary

When building the descendant tree for user-state prefetch, use the same rule as `vertical_block.block_has_access_error`: for blocks with `has_dynamic_children()`, iterate **`get_child_blocks()`** instead of **`get_children()`**.

### Deliverables

- [ ] Helper `_children_for_field_data_cache(block)` in `lms/djangoapps/courseware/model_data.py`
- [ ] `add_block_descendents` uses helper when recursing
- [ ] Unit tests in `lms/djangoapps/courseware/tests/test_model_data.py`
- [ ] Devstack integration validator: `scripts/field_data_cache_integration/`

### Key files

| File | Change |
|------|--------|
| `lms/djangoapps/courseware/model_data.py` | Dynamic-child traversal |
| `lms/djangoapps/courseware/tests/test_model_data.py` | Unit + mock integration |
| `scripts/field_data_cache_integration/README.rst` | Manual validation recipe |

### Rollout

- No feature flag required (behavior aligns with render path).
- Optional flag `courseware.field_data_cache.use_dynamic_children` for conservative rollout if needed.
- Monitor Datadog: `xb_user_state.get_many.blocks_requested`, `.duration`, `render_xblock` p95.

### Acceptance criteria

- For vertical with library size 10, `max_count=2`: `blocks_requested` ≈ vertical + library_content + 2 problems (+ small overhead), **not** ~10+ problems.
- All existing `test_model_data`, `test_library_content`, `test_block_render` pass.
- Integration script reports **PASS (prefetch breadth)** on fix branch vs **FAIL** on baseline.

### Estimated effort

1 sprint (implementation + review + staging validation)

---

## Phase A2 — SQL micro-optimizations for `get_many`

**Priority:** P1  
**Repos:** `edx-platform`  
**Depends on:** A1 merged (measurement baseline)  

### Summary

Reduce cost per remaining `StudentModule` row fetched after A1 narrows the key set.

### Deliverables

- [ ] `.only('state', 'modified', 'module_state_key', 'course_id')` on prefetch query in `_get_student_modules`
- [ ] Single-query path when `len(usage_keys) <= STUDENTMODULE_BULK_QUERY_THRESHOLD` (e.g. 500) instead of always chunking
- [ ] Optional read-replica routing for read-only prefetch contexts (match pattern in `StudentModule.all_submitted_problems_read_only`)
- [ ] Extended metrics: `xb_user_state.get_many.db_ms` vs `parse_ms` (Datadog custom attributes)

### Key files

| File | Change |
|------|--------|
| `lms/djangoapps/courseware/user_state_client.py` | Query shaping, timing |
| `lms/djangoapps/courseware/models.py` | Optional read manager |

### Acceptance criteria

- Same functional behavior as A1; no change to selection or scores.
- p95 `get_many` duration improves measurably vs A1-only on 90-problem staging course.
- `EXPLAIN` on representative query uses `(student_id, course_id, module_state_key)` index.

### Estimated effort

0.5–1 sprint

---

## Phase A3 — JSON parse optimizations

**Priority:** P1 (can ship with A2)  
**Repos:** `edx-platform`  

### Summary

Reduce CPU time parsing ~121KB+ of CAPA state JSON per request.

### Deliverables

- [ ] Use `orjson` (or existing fast JSON path) in `get_many` hot loop
- [ ] Lazy parse in `UserStateCache`: store raw JSON string; parse dict on first field read
- [ ] (Optional) Pass `fields` from `_fields_to_cache` into `get_many` for blocks that do not need full state at render time

### Key files

| File | Change |
|------|--------|
| `lms/djangoapps/courseware/user_state_client.py` | Parse path |
| `lms/djangoapps/courseware/model_data.py` | `UserStateCache.cache_fields` |

### Risks

- CAPA `student_view` may require full state on first paint—audit before enabling field pruning globally.
- Lazy parse must preserve `set_many` / mutation semantics.

### Acceptance criteria

- Unit tests for cache hit/miss and field read after lazy load.
- No increase in `get_many` error rate; problem submission still works.

### Estimated effort

0.5–1 sprint (parallel with A2)

---

## Phase B1 — Backend shell render + batch child API

**Priority:** P2  
**Repos:** `edx-platform`  
**Depends on:** A1–A3 recommended (reduces blast radius); not strictly required  

### Summary

Split monolithic `render_xblock` into:

1. **Shell response** — vertical chrome + placeholders (`render_mode=shell`)
2. **Batch child API** — render N CAPA blocks per request with `FieldDataCache` depth=0 per child

Testable with **curl/Postman** before any MFE work.

### Deliverables

- [ ] Query param `render_mode=shell|full` on `render_xblock` (default `full`)
- [ ] Threshold: shell mode when descendant CAPA count &gt; `settings.LARGE_VERTICAL_PROBLEM_THRESHOLD` (e.g. 20) and waffle enabled
- [ ] Template fragment `vert_module_lazy.html` + bootstrap JS posting `xblock.lazy.ready` to parent
- [ ] New API: `GET /api/courseware/v1/xblock_children/` with `parent_usage_key`, `child_usage_keys` (max 10), auth same as courseware
- [ ] OpenAPI / internal doc for batch endpoint
- [ ] curl examples in `scripts/field_data_cache_integration/` or API doc

### Key files

| File | Change |
|------|--------|
| `lms/djangoapps/courseware/views/views.py` | `render_mode` branch |
| `lms/djangoapps/courseware/block_render.py` | `render_xblock_children()` helper |
| `xmodule/item_bank_block.py` | `student_view_shell()` or branch in `student_view` |
| New DRF view + URLconf | Batch API |

### Feature flags

| Flag | Purpose |
|------|---------|
| `courseware.render_xblock.lazy_library_content` | Enable shell mode |
| `courseware.render_xblock.lazy_threshold` | Django setting / Waffle config |

### Acceptance criteria

- Shell `render_xblock` TTFB **&lt; 5s** on 90-problem vertical in staging.
- Batch API returns valid CAPA HTML for each requested child key; 403 for keys not in learner’s selected set.
- Full `render_mode=full` unchanged when flag off.

### Estimated effort

2 sprints

---

## Phase B2 — Learning MFE lazy load

**Priority:** P2  
**Repos:** `frontend-app-learning`, `edx-platform` (coordination)  
**Depends on:** B1 deployed to staging/prod  

### Summary

Learning MFE continues single iframe per unit but orchestrates incremental child loading inside the iframe lifecycle.

### Deliverables

- [x] Unit iframe URL includes `render_mode=shell` when course metadata indicates large library quiz (new field from course blocks API or heuristic)
- [x] `postMessage` handler for `xblock.lazy.ready` with child usage key list
- [x] Batch fetch client (sequential or max 3 parallel) to `/api/courseware/v1/xblock_children/`
- [x] Skeleton UI + progress (“Loading question 12 of 90”)
- [x] iframe resize after each batch (reuse existing height postMessage)
- [x] E2E test or manual QA checklist

### Key files (MFE)

| Area | Change |
|------|--------|
| Courseware container / unit iframe loader | Shell URL, lazy orchestration |
| Course blocks / sequence metadata | Expose `has_large_library_content` or problem count |

### Feature flag

- `learning_mfe.enable_lazy_xblock_load` (MFE config + backend waffle)

### Acceptance criteria

- IBM-scale vertical loads in Learning MFE without nginx 504.
- Learner can answer and submit problems loaded via batch API.
- No regression on small units (flag off → current behavior).

### Estimated effort

2 sprints (cross-team)

---

## Phase C — CMS guardrails

**Priority:** P3  
**Repos:** `edx-platform` (CMS / xmodule)  
**Can ship anytime** after A1; independent of B phases  

### Summary

Prevent authors from creating new “90 problems in one vertical” configs without acknowledgment.

### Deliverables

- [x] `LegacyLibraryContentBlock.validate()` / `ItemBankBlock.validate()` warning when `max_count > 25` (threshold configurable)
- [x] Studio message: recommend splitting verticals or lowering count; link to internal runbook
- [x] (Optional) hard cap for net-new courses via org-level waffle

### Key files

| File | Change |
|------|--------|
| `xmodule/library_content_block.py` | Validation message |
| `xmodule/item_bank_block.py` | Same for v2 item bank |

### Acceptance criteria

- Saving block in Studio shows warning at threshold; publish still allowed.
- No change to existing published courses until edited.

### Estimated effort

0.5 sprint

---

## Testing strategy

| Layer | What | Where |
|-------|------|--------|
| Unit | Dynamic children, get_many, lazy cache | `test_model_data.py`, `test_user_state_client.py` |
| Integration | Prefetch breadth, events, DB | `scripts/field_data_cache_integration/validate_dynamic_children_prefetch.py` |
| Manual staging | IBM Cybfun or clone course, darfield-like learner | Datadog trace + nginx logs |
| Load | 90-problem vertical concurrent renders | k6 or internal load test (post A1) |
| E2E | MFE lazy load happy path | Phase B2 QA checklist |

**Important:** HTTP 200 alone is insufficient. Validate `StudentModule` rows, `xb_user_state.get_many` metrics, and tracking events (`edx.librarycontentblock.content.assigned`).

---

## Metrics & dashboards

Add or watch in Datadog:

- `xb_user_state.get_many.blocks_requested`
- `xb_user_state.get_many.duration`
- `xb_user_state.get_many.problem.blocks_out`
- `resource:render_xblock` p95 by `course_id` / org
- nginx `upstream_duration` for `/xblock/` 504 rate

Success: IBM vertical p95 render **&lt; 45s** after A1–A3; **&lt; 5s** TTFB after B1–B2.

---

## Rollout sequence (recommended)

| Step | Action | Environment |
|------|--------|-------------|
| 1 | Merge A1 + run integration script | Devstack → staging |
| 2 | Deploy A1 to prod; monitor 48h | Prod |
| 3 | Merge A2+A3 | Staging → prod |
| 4 | Deploy B1 behind waffle; curl validate | Staging |
| 5 | Enable B1 for pilot org (IBM) | Prod |
| 6 | Ship B2 MFE for pilot org | Prod |
| 7 | Ship C guardrails | All envs |

---

## Open questions

1. **Prefetch + selection side effect:** `get_child_blocks()` during prefetch may invoke `selected_children()` on bound blocks—confirm whether A1 alone can fire `assigned` events during cache build; document or defer binding until after cache if needed.
2. **nginx timeout:** Raise `/xblock/` timeout as temporary ops mitigation, or rely solely on perf fixes?
3. **Item Bank v2 vs Legacy Library Content:** Same code paths via `ItemBankMixin`—confirm both in test matrix.
4. **Mobile apps:** Do they use same `/xblock/` iframe path or native CAPA? Scope B2 accordingly.

---

## References

- Incident trace: `trace_id:6a6b1de50000000084279e774be9f57c` (IBM vertical `8e077c86…`, user 64849306)
- Integration test: [`scripts/field_data_cache_integration/README.rst`](../../scripts/field_data_cache_integration/README.rst)
- Learning MFE unit iframe ADR: [frontend-app-learning ADR-0002](https://github.com/openedx/frontend-app-learning/blob/master/docs/decisions/0002-courseware-page-decisions.md)
- Vertical dynamic-child precedent: `xmodule/vertical_block.py` — `block_has_access_error`
