# Course-completion percentage as a Snowflake SQL aggregate

## Context

We want the completion percentage a learner sees on the course Progress tab, computed as a warehouse aggregate in Snowflake, for backend reporting instead of a per-request Python call. The Python entrypoint is `calculate_progress_for_learner_in_course()` in `lms/djangoapps/course_home_api/progress/api.py:253`, which delegates to `get_course_blocks_completion_summary()` in `lms/djangoapps/courseware/courses.py:596`. The output must track the page "as closely as possible."

This document is a technical plan (tradeoffs + path forward) for external feedback, not working code.

### Decisions locked so far

- Output: `complete_percentage` only, per `(user, course)`. Drop the incomplete/locked split.
- Population: verified learners / ungated courses only, so `locked_count` is always 0.
- Fidelity: track-level approximation. Model the dimensions that move the number; accept small drift for staff / beta / individual-override populations.
- Sources in the warehouse: `BlockCompletion` and the flattened `PROD.CORE_SOURCES.COURSE_STRUCTURE` table (see below). This supersedes the earlier draft's Python exporter and the `COURSE_BLOCKS_RELATIONSHIPS` edges view.

## What the page actually computes (the target)

`get_course_blocks_completion_summary()` walks section -> subsection -> unit and buckets each **unit (vertical)**:

```python
complete_percentage = round(complete_count / num_total_units, 2)   # api.py:273
num_total_units = complete_count + incomplete_count + locked_count  # api.py:269
```

Two rules drive everything:

1. A unit is **complete** iff every non-excluded completable descendant leaf has `completion == 1.0` in `BlockCompletion` (recursive `all(...)` in `BlockCompletionTransformer.mark_complete`, `lms/djangoapps/course_api/blocks/transformers/block_completion.py`). Aggregator recursion makes unit-complete equivalent to "all completable leaves under the unit equal 1.0," which flattens cleanly in SQL. The stored `completion` float already encodes leaf semantics (video at 95%, complete-on-view), so no special leaf handling is needed.
2. The set of countable units is **per-learner**: `get_course_blocks(...)` filters through visibility, content-gating, user-partition, and start-date transformers. With `allow_start_dates_in_future=True`, start date does **not** shrink the denominator.

## Revised core insight: `COURSE_STRUCTURE` gives us almost everything

`PROD.CORE_SOURCES.COURSE_STRUCTURE` is a flattened per-block export of the Course Blocks API (one row per block, with ancestor rollups precomputed). The columns that matter here:

- `COURSE_ID`, `VERSION`, `EXPORT_DATE`: course scoping and version selection. No `active_versions` join, no cross-rerun `block_id` collision to worry about.
- `UNIT_BLOCK_ID` (also `SECTION_BLOCK_ID`, `SUBSECTION_BLOCK_ID`, `COURSE_BLOCK_ID`): every block already carries its ancestor unit. No recursive CTE and no edges view needed to group leaves under their unit.
- `BLOCK_ID` / `LOCAL_BLOCK_ID`: the full usage key and the bare id. If `BLOCK_ID` is the usage key, it joins directly to `BlockCompletion.block_key` with no string parsing.
- `BLOCK_TYPE`, `DEPTH`, `PARENT_ID`: identify units and leaves structurally.
- `IS_VISIBLE_TO_STAFF_ONLY`: apply the staff-only denominator filter exactly, an improvement over the earlier "accept drift" approximation.
- `IS_GRADED`: available if content-gating / the locked bucket comes back in scope.

Two gaps remain:

- **No `group_access`.** Per-learner content-group denominators cannot come from this table. Given the locked decisions, use a course-wide denominator (non-staff verticals) and flag drift for content-grouped courses, or source `group_access` separately if that drift proves material.
- **No `completion_mode`.** It is an XBlock class attribute, never exported. Identify completable leaves structurally (a block with a `UNIT_BLOCK_ID` that is not itself a parent of any other block) and subtract a small static list of AGGREGATOR/EXCLUDED block types. This avoids any Python read for the common case; refine the exclusion list if validation shows drift.

## Proposed architecture

Two existing tables, one aggregate, one optional tiny lookup.

- **`COURSE_STRUCTURE`** (exists): the structure dimension.
- **`BlockCompletion`** (exists): completion facts. Filter `completion = 1.0`.
- **`excluded_block_types`** (optional static list): AGGREGATOR/EXCLUDED `block_type` values to drop from required leaves (`course`, `chapter`, `sequential`, `vertical`, `split_test`, `library_content`, `conditional`, `randomize`, plus any confirmed EXCLUDED types).

## The join key

`BlockCompletion.block_key` is a full usage key (for example `block-v1:edX+DemoX+2024+type@problem+block@abc123`); `BlockCompletion.context_key` is the course key. Confirm whether `COURSE_STRUCTURE.BLOCK_ID` is the same full usage key (then join directly) or the bare id (then join `LOCAL_BLOCK_ID` to `split_part(block_key, 'block@', 2)`). Scope every join by course (`context_key = COURSE_ID`) regardless.

## The SQL aggregate (core deliverable)

Assumes `BLOCK_ID` is the full usage key and one chosen `VERSION` per course (latest `EXPORT_DATE`).

```sql
with structure as (  -- one published version per course
  select * from course_structure
  qualify row_number() over (partition by course_id order by export_date desc) = 1
),
units as (
  select course_id, block_id as unit_id, is_visible_to_staff_only
  from structure where block_type = 'vertical'
),
parents as (select distinct course_id, parent_id from structure where parent_id is not null),
required as (  -- completable leaves: have a unit ancestor, are not a parent, not an aggregator type
  select s.course_id, s.unit_block_id as unit_id, s.block_id as leaf_id
  from structure s
  left join parents p on p.course_id = s.course_id and p.parent_id = s.block_id
  where s.unit_block_id is not null
    and p.parent_id is null
    and s.block_type not in (select block_type from excluded_block_types)
),
unit_totals as (select course_id, unit_id, count(*) n_total from required group by 1,2),
done as (
  select r.course_id, r.unit_id, bc.user_id, count(*) n_done
  from required r
  join block_completion bc
    on bc.context_key = r.course_id and bc.block_key = r.leaf_id and bc.completion = 1.0
  group by 1,2,3
),
unit_complete as (
  select d.user_id, d.course_id, d.unit_id
  from done d join unit_totals t using (course_id, unit_id)
  where d.n_done = t.n_total and t.n_total > 0
),
countable as (  -- denominator: non-staff verticals
  select course_id, unit_id from units where coalesce(is_visible_to_staff_only, false) = false
)
select e.user_id, e.course_id,
       round(count(distinct uc.unit_id) * 1.0
             / nullif(count(distinct c.unit_id), 0), 2) as complete_percentage
from enrollment e
join countable c on c.course_id = e.course_id
left join unit_complete uc
       on uc.user_id = e.user_id and uc.course_id = c.course_id and uc.unit_id = c.unit_id
group by 1, 2;
```

## Tradeoffs and known drift

- `BLOCK_ID` vs `LOCAL_BLOCK_ID` join key: wrong choice silently zeroes the numerator. Confirm first.
- Version selection: latest `EXPORT_DATE` assumes the export tracks the published branch. If drafts or reruns land in the table, filter to the published version explicitly.
- No `group_access`: denominator is course-wide, so content-grouped courses drift. Acceptable under the track-level decision; revisit if those courses matter.
- `completion_mode` approximation: the leaf-minus-aggregator rule misclassifies EXCLUDED leaves (rare). Extend `excluded_block_types` if validation shows it.
- Non-vertical depth-3 blocks: the page counts by position, we count `block_type = 'vertical'`. Rare divergence.
- Empty-unit edge: a vertical with zero completable leaves is complete in Python (`all([])`) once any course completion exists; the SQL (`n_total > 0`) marks it incomplete. Rare.
- Rounding: Python `round()` is banker's rounding; Snowflake `ROUND` is half-away-from-zero. Only diverges at exact `.xx5` boundaries.

## Recommended path forward

1. Confirm the `BLOCK_ID` / `LOCAL_BLOCK_ID` join key against `BlockCompletion.block_key` on the Demo course.
2. Confirm version/`EXPORT_DATE` selection yields exactly the published structure per course.
3. Seed `excluded_block_types` (start from the AGGREGATOR list above; add EXCLUDED types if found).
4. Write the aggregate as a dbt model / view.
5. Validation harness: for a sample of `(user, course)`, compare the SQL `complete_percentage` against `calculate_progress_for_learner_in_course()` and quantify drift.
6. Decide whether content-group drift warrants sourcing `group_access`.

## Verification

- Structure: pick the Demo course, compare the SQL vertical count and per-unit completable-leaf sets against `get_block_structure_manager(course_key).get_collected()` in a devstack shell.
- End-to-end: pick a devstack test learner, call `calculate_progress_for_learner_in_course(course_key, user)`, run the aggregate for the same pair, assert `complete_percentage` matches within rounding tolerance.
- Fleet check: run the harness across a course sample; report the drift distribution before trusting the aggregate.

## Open questions to confirm before build

- Is `COURSE_STRUCTURE.BLOCK_ID` the full usage key (direct join to `block_key`) or the bare id (join via `LOCAL_BLOCK_ID`)?
- Does `VERSION` / `EXPORT_DATE` selection give the published structure, and is history retained if we later need historical accuracy?
- Is `UNIT_BLOCK_ID` populated on component rows to point at their containing vertical (and what is it on the vertical row itself)?
- Do the target courses use content-group partitioning heavily enough that the missing `group_access` matters?
