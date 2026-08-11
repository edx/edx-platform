#!/usr/bin/env python
"""
Devstack integration validator for FieldDataCache dynamic-children prefetch (A1).

Run inside LMS Django context:

    export FDC_TEST_COURSE_ID=...
    export FDC_TEST_VERTICAL_KEY=...
    export FDC_TEST_LIBRARY_CONTENT_KEY=...
    export FDC_TEST_USERNAME=fdc_test_learner
    python scripts/field_data_cache_integration/validate_dynamic_children_prefetch.py

Optional:
    export FDC_TEST_PASSWORD=edx
    export FDC_TEST_RUN_HTTP=1
    export FDC_TEST_MAX_COUNT=2   # expected max_count from Studio (for assertions)

See README.rst in this directory for full setup and pass criteria.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Django bootstrap when executed as a standalone script from edx-platform root
# ---------------------------------------------------------------------------
if 'django' not in sys.modules:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lms.envs.devstack')
    import django  # pylint: disable=wrong-import-order

    django.setup()

from django.contrib.auth import get_user_model
from django.db import connection, reset_queries
from django.test import Client
from opaque_keys.edx.keys import UsageKey
from xmodule.modulestore.django import modulestore

from lms.djangoapps.courseware.model_data import FieldDataCache
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.courseware.user_state_client import DjangoXBlockUserStateClient
from lms.djangoapps.courseware.block_render import get_block_for_descriptor
from common.djangoapps.student.models import CourseEnrollment

User = get_user_model()

REQUIRED_ENV = (
    'FDC_TEST_COURSE_ID',
    'FDC_TEST_VERTICAL_KEY',
    'FDC_TEST_LIBRARY_CONTENT_KEY',
    'FDC_TEST_USERNAME',
)


@dataclass
class TestConfig:
    course_id: str
    vertical_key: str
    library_content_key: str
    username: str
    password: str = 'edx'
    run_http: bool = False
    expected_max_count: int = 2


@dataclass
class PrefetchMetrics:
    blocks_requested: int = 0
    block_types_requested: dict[str, int] = field(default_factory=dict)
    studentmodule_queries: int = 0
    published_events: list[tuple[Any, str, dict]] = field(default_factory=list)


def _load_config() -> TestConfig:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print('ERROR: Missing required environment variables:')
        for name in missing:
            print(f'  - {name}')
        print('\nSee scripts/field_data_cache_integration/README.rst')
        sys.exit(1)

    return TestConfig(
        course_id=os.environ['FDC_TEST_COURSE_ID'],
        vertical_key=os.environ['FDC_TEST_VERTICAL_KEY'],
        library_content_key=os.environ['FDC_TEST_LIBRARY_CONTENT_KEY'],
        username=os.environ['FDC_TEST_USERNAME'],
        password=os.environ.get('FDC_TEST_PASSWORD', 'edx'),
        run_http=os.environ.get('FDC_TEST_RUN_HTTP', '').lower() in ('1', 'true', 'yes'),
        expected_max_count=int(os.environ.get('FDC_TEST_MAX_COUNT', '2')),
    )


def _usage_key(key_str: str):
    return UsageKey.from_string(key_str)


def snapshot_library_state(config: TestConfig) -> dict:
    """Return StudentModule row snapshot for library_content + all problem rows for learner."""
    user = User.objects.get(username=config.username)
    course_key = _usage_key(config.vertical_key).course_key
    lc_key = _usage_key(config.library_content_key)

    lc_row = StudentModule.objects.filter(
        student=user,
        course_id=course_key,
        module_state_key=lc_key,
    ).first()

    lc_state = None
    selected = None
    if lc_row and lc_row.state:
        lc_state = json.loads(lc_row.state)
        selected = lc_state.get('selected')

    problem_rows = StudentModule.objects.filter(
        student=user,
        course_id=course_key,
        module_type='problem',
    ).values_list('module_state_key', 'state')

    return {
        'library_content_modified': lc_row.modified.isoformat() if lc_row else None,
        'library_content_state': lc_state,
        'selected': selected,
        'problem_row_count': problem_rows.count(),
        'problem_keys': [str(k) for k, _ in problem_rows],
    }


def _count_modulestore_children(config: TestConfig) -> dict[str, int]:
    """How many children modulestore exposes vs learner-selected subset."""
    store = modulestore()
    course_key = _usage_key(config.vertical_key).course_key
    lc_block = store.get_item(_usage_key(config.library_content_key))

    modulestore_child_count = len(lc_block.get_children())
    selected_child_count = None
    if hasattr(lc_block, 'get_child_blocks'):
        try:
            selected_child_count = len(lc_block.get_child_blocks())
        except Exception as exc:  # pylint: disable=broad-except
            selected_child_count = f'error: {exc}'

    return {
        'modulestore_children': modulestore_child_count,
        'get_child_blocks_count': selected_child_count,
        'max_count': getattr(lc_block, 'max_count', None),
    }


@contextmanager
def _instrument_prefetch(config: TestConfig):
    """Capture get_many breadth, ORM queries, and XBlock publish calls during prefetch."""
    metrics = PrefetchMetrics()
    original_get_many = DjangoXBlockUserStateClient.get_many

    def tracking_get_many(self, username, block_keys, scope=None, fields=None):
        keys = list(block_keys)
        metrics.blocks_requested += len(keys)
        for key in keys:
            block_type = key.block_type
            metrics.block_types_requested[block_type] = (
                metrics.block_types_requested.get(block_type, 0) + 1
            )
        yield from original_get_many(self, username, keys, scope=scope, fields=fields)

    published = []

    def capture_publish(block, event_type, event_data):
        published.append((block, event_type, event_data))

    user = User.objects.get(username=config.username)
    course_key = _usage_key(config.vertical_key).course_key
    vertical = modulestore().get_item(_usage_key(config.vertical_key))

    reset_queries()
    old_debug = connection.force_debug_cursor
    connection.force_debug_cursor = True

    try:
        with patch.object(DjangoXBlockUserStateClient, 'get_many', tracking_get_many):
            with patch('xmodule.x_module.XModuleMixin.publish', capture_publish):
                with patch('xblock.core.XBlock.publish', capture_publish):
                    field_data_cache = FieldDataCache.cache_for_block_descendents(
                        course_key,
                        user,
                        vertical,
                    )
        metrics.studentmodule_queries = sum(
            1 for q in connection.queries if 'courseware_studentmodule' in q['sql'].lower()
        )
        metrics.published_events = published
        yield metrics, field_data_cache
    finally:
        connection.force_debug_cursor = old_debug


def run_prefetch_phase(config: TestConfig) -> PrefetchMetrics:
    print('\n=== Phase 1: Prefetch-only (FieldDataCache.cache_for_block_descendents) ===')
    before = snapshot_library_state(config)
    print(f"Before — selected: {before['selected']!r}, problem rows: {before['problem_row_count']}")

    child_counts = _count_modulestore_children(config)
    print(f"Library modulestore children: {child_counts['modulestore_children']}")
    print(f"Library get_child_blocks (if bound): {child_counts['get_child_blocks_count']}")
    print(f"Studio max_count: {child_counts['max_count']}")

    with _instrument_prefetch(config) as (metrics, _cache):
        pass

    after = snapshot_library_state(config)
    print(f"\nPrefetch metrics:")
    print(f"  get_many blocks_requested: {metrics.blocks_requested}")
    print(f"  by block_type: {metrics.block_types_requested}")
    print(f"  StudentModule SQL queries: {metrics.studentmodule_queries}")
    assigned = [
        (evt, data) for _block, evt, data in metrics.published_events
        if evt and evt.endswith('.assigned')
    ]
    print(f"  publish events (assigned): {len(assigned)}")
    for evt, data in assigned:
        print(f"    - {evt}: {data}")

    print(f"\nAfter — selected: {after['selected']!r}, problem rows: {after['problem_row_count']}")
    if before['library_content_modified'] != after['library_content_modified']:
        print('  NOTE: library_content StudentModule modified timestamp changed during prefetch.')
    if before['selected'] != after['selected']:
        print('  NOTE: selected field changed during prefetch (may happen on first visit).')

    # Assertions / guidance
    # With the fix, requested keys should scale with max_count, not library size.
    modulestore_children = child_counts['modulestore_children']
    expected_with_fix = config.expected_max_count * 3 + 5  # lc + problems + vertical overhead
    baseline_threshold = max(modulestore_children + 5, config.expected_max_count * 5)

    if modulestore_children > config.expected_max_count and metrics.blocks_requested >= baseline_threshold:
        print(
            f"\nFAIL (prefetch breadth): blocks_requested={metrics.blocks_requested} suggests "
            f"all modulestore children (~{modulestore_children}) were prefetched."
        )
    elif metrics.blocks_requested <= expected_with_fix:
        print(
            f"\nPASS (prefetch breadth): blocks_requested={metrics.blocks_requested} "
            f"(expected ~≤{expected_with_fix} with fix; library has {modulestore_children} candidates)."
        )
    else:
        print(
            f"\nWARN (prefetch breadth): blocks_requested={metrics.blocks_requested} between "
            f"fix expectation (~{expected_with_fix}) and full library (~{baseline_threshold}). "
            f"Review block_types breakdown."
        )

    if assigned:
        print('WARN (events): assigned events fired during prefetch-only phase — review output above.')
    else:
        print('PASS (events): no assigned events during prefetch-only phase.')

    return metrics


def run_bound_render_prefetch(config: TestConfig) -> PrefetchMetrics:
    """
    Mirrors production more closely: bind block via get_block_for_descriptor, then
    walk descendants the same way block_render does before student_view.
    """
    print('\n=== Phase 2: Bound block prefetch (get_block_for_descriptor path) ===')
    user = User.objects.get(username=config.username)
    course_key = _usage_key(config.vertical_key).course_key
    vertical = modulestore().get_item(_usage_key(config.vertical_key))

    before = snapshot_library_state(config)
    metrics = PrefetchMetrics()
    original_get_many = DjangoXBlockUserStateClient.get_many

    def tracking_get_many(self, username, block_keys, scope=None, fields=None):
        keys = list(block_keys)
        metrics.blocks_requested += len(keys)
        for key in keys:
            block_type = key.block_type
            metrics.block_types_requested[block_type] = (
                metrics.block_types_requested.get(block_type, 0) + 1
            )
        yield from original_get_many(self, username, keys, scope=scope, fields=fields)

    published = []

    def capture_publish(block, event_type, event_data):
        published.append((block, event_type, event_data))

    client = Client()
    client.force_login(user)

    reset_queries()
    old_debug = connection.force_debug_cursor
    connection.force_debug_cursor = True

    try:
        with patch.object(DjangoXBlockUserStateClient, 'get_many', tracking_get_many):
            field_data_cache = FieldDataCache.cache_for_block_descendents(
                course_key,
                user,
                vertical,
            )
            with patch('xmodule.x_module.XModuleMixin.publish', capture_publish):
                instance = get_block_for_descriptor(
                    user,
                    client.request(),
                    vertical,
                    field_data_cache,
                    course_key,
                )
            # Trigger selection/render path (production does this in render_xblock)
            if instance is not None:
                instance.render('student_view', context={})
    finally:
        connection.force_debug_cursor = old_debug

    metrics.studentmodule_queries = sum(
        1 for q in connection.queries if 'courseware_studentmodule' in q['sql'].lower()
    )
    metrics.published_events = published

    after = snapshot_library_state(config)
    assigned = [evt for _b, evt, _d in metrics.published_events if evt and evt.endswith('.assigned')]

    print(f"  get_many blocks_requested: {metrics.blocks_requested}")
    print(f"  by block_type: {metrics.block_types_requested}")
    print(f"  assigned events: {len(assigned)}")
    print(f"  selected after render: {after['selected']!r}")

    selected_len = len(after['selected'] or [])
    if selected_len == config.expected_max_count:
        print(f'PASS (control): selected length == max_count ({config.expected_max_count})')
    else:
        print(
            f'WARN (control): selected length {selected_len} != expected max_count '
            f'{config.expected_max_count}'
        )

    if assigned:
        print('PASS (control): assigned event(s) emitted on first full render (expected).')
    else:
        print('WARN (control): no assigned events on render — learner may already have selection.')

    return metrics


def run_http_smoke(config: TestConfig) -> None:
    print('\n=== Phase 3: HTTP smoke (render_xblock) ===')
    client = Client()
    logged_in = client.login(username=config.username, password=config.password)
    if not logged_in:
        print('FAIL: Could not log in test user. Set FDC_TEST_PASSWORD.')
        return

    url = f"/xblock/{config.vertical_key}"
    params = {
        'view': 'student_view',
        'recheck_access': '1',
        'show_bookmark': '0',
        'show_title': '0',
    }
    response = client.get(url, params)
    print(f"  GET {url} -> {response.status_code}")
    if response.status_code != 200:
        print(f'  FAIL: body snippet: {response.content[:500]!r}')
        return

    # Rough count of CAPA problem wrappers in rendered HTML
    problem_divs = response.content.count(b'xblock-student_view-problem')
    print(f"  problem student_view markers in HTML: {problem_divs}")
    if problem_divs >= config.expected_max_count:
        print('PASS (HTTP): vertical rendered with expected problem markers.')
    else:
        print(
            f'WARN (HTTP): expected at least {config.expected_max_count} problem markers, '
            f'saw {problem_divs}.'
        )


def main() -> None:
    config = _load_config()
    print('FieldDataCache dynamic-children integration validation')
    print(f"  course: {config.course_id}")
    print(f"  vertical: {config.vertical_key}")
    print(f"  library_content: {config.library_content_key}")
    print(f"  user: {config.username}")

    user = User.objects.filter(username=config.username).first()
    if not user:
        print(f'ERROR: user {config.username!r} not found')
        sys.exit(1)

    course_key = _usage_key(config.vertical_key).course_key
    if not CourseEnrollment.is_enrolled(user, course_key):
        print(f'ERROR: {config.username} is not enrolled in {course_key}')
        sys.exit(1)

    run_prefetch_phase(config)
    run_bound_render_prefetch(config)

    if config.run_http:
        run_http_smoke(config)
    else:
        print('\n(Skipping HTTP phase — set FDC_TEST_RUN_HTTP=1 to enable)')

    print('\nDone. Compare blocks_requested between baseline branch and fix branch.')
    print('See scripts/field_data_cache_integration/README.rst for pass criteria.')


if __name__ == '__main__':
    main()
