"""
Unit tests for Phase B1 shell/batch lazy render helpers.
"""
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

from common.djangoapps.edxmako.shortcuts import render_to_string

from lms.djangoapps.courseware.block_render import (
    estimate_problem_descendant_count,
    field_data_cache_depth_for_shell,
    should_use_shell_render,
)
from lms.djangoapps.courseware.toggles import COURSEWARE_LAZY_LIBRARY_CONTENT
from opaque_keys.edx.locator import CourseLocator


class TestShellRenderHelpers(TestCase):
    """Tests for shell-mode eligibility helpers."""

    def test_estimate_problem_count_uses_max_count_for_dynamic_blocks(self):
        block = Mock()
        block.has_dynamic_children.return_value = True
        block.max_count = 90
        block.children = [Mock()] * 200
        assert estimate_problem_descendant_count(block) == 90

    def test_estimate_problem_count_walks_static_vertical(self):
        problem = Mock()
        problem.has_dynamic_children.return_value = False
        problem.location.block_type = 'problem'
        problem.get_children.return_value = []

        html = Mock()
        html.has_dynamic_children.return_value = False
        html.location.block_type = 'html'
        html.get_children.return_value = []

        vertical = Mock()
        vertical.has_dynamic_children.return_value = False
        vertical.location.block_type = 'vertical'
        vertical.get_children.return_value = [html, problem, problem]

        assert estimate_problem_descendant_count(vertical) == 2

    def test_field_data_cache_depth_for_shell(self):
        vertical = Mock()
        vertical.location.block_type = 'vertical'
        assert field_data_cache_depth_for_shell(vertical) == 1

        library = Mock()
        library.location.block_type = 'library_content'
        assert field_data_cache_depth_for_shell(library) == 0

    @override_settings(LARGE_VERTICAL_PROBLEM_THRESHOLD=20)
    def test_should_use_shell_render_requires_flag_mode_and_threshold(self):
        course_key = CourseLocator('org', 'course', 'run')
        block = Mock()
        block.has_dynamic_children.return_value = True
        block.max_count = 90

        with patch.object(COURSEWARE_LAZY_LIBRARY_CONTENT, 'is_enabled', return_value=False):
            assert should_use_shell_render(course_key, 'shell', block) is False

        with patch.object(COURSEWARE_LAZY_LIBRARY_CONTENT, 'is_enabled', return_value=True):
            assert should_use_shell_render(course_key, 'full', block) is False
            assert should_use_shell_render(course_key, 'shell', block) is True
            block.max_count = 5
            assert should_use_shell_render(course_key, 'shell', block) is False


class TestLazyShellTrustedOrigin(TestCase):
    """
    Tests that the shell template pins the origin allowed to fill its placeholders.

    render_xblock is xframe_options_exempt, so any page can embed the shell iframe and
    become its window.parent. The template must therefore check event.origin against a
    server-supplied value rather than anything the browser controls.
    """

    def _render(self):
        """Render the lazy shell template with one placeholder."""
        return render_to_string('vert_module_lazy.html', {
            'items': [{'id': 'block-v1:org+course+run+type@problem+block@abc', 'content': ''}],
            'xblock_context': {},
            'show_bookmark_button': False,
            'parent_usage_key': 'block-v1:org+course+run+type@library_content+block@parent',
            'reset_button': False,
            'unit_title': None,
            'show_title': False,
        })

    @override_settings(LEARNING_MICROFRONTEND_URL='https://learning.example.com/some/path')
    def test_emits_configured_mfe_origin(self):
        html = self._render()
        assert 'var expectedParentOrigin = "https://learning.example.com";' in html

    @override_settings(LEARNING_MICROFRONTEND_URL='')
    def test_emits_empty_origin_when_unconfigured(self):
        # With nothing configured there is no trustworthy sender, and the handler refuses
        # every children message rather than falling back to browser-supplied data.
        html = self._render()
        assert 'var expectedParentOrigin = "";' in html
