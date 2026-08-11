"""
Tests for module-level helpers defined in xmodule/x_module.py
"""
from unittest import TestCase
from unittest.mock import Mock

from xblock.fields import Scope

from xmodule.x_module import dirty_fields_are_safe_to_discard


def _field(name, scope):
    """Build a mock XBlock field with the given name and scope."""
    field = Mock()
    field.name = name
    field.scope = scope
    return field


class DirtyFieldsAreSafeToDiscardTests(TestCase):
    """
    Tests for the predicate guarding the tolerated InvalidScopeError in bind_for_student.
    """

    def test_user_state_only(self):
        assert dirty_fields_are_safe_to_discard([_field('selected', Scope.user_state)])

    def test_user_state_alongside_children(self):
        # The real case: resolving dynamic children stages `selected` and marks
        # `children` dirty. Scope.children is a Sentinel with no `user` attribute,
        # it saves fine anywhere, and it survives the rebind.
        assert dirty_fields_are_safe_to_discard([
            _field('selected', Scope.user_state),
            _field('children', Scope.children),
        ])

    def test_every_per_user_scope_qualifies(self):
        # These are exactly the scopes bind_for_student discards when it rebinds,
        # so tolerating a failed save of them loses nothing.
        assert dirty_fields_are_safe_to_discard([
            _field('selected', Scope.user_state),
            _field('theme', Scope.preferences),
            _field('last_seen', Scope.user_info),
        ])

    def test_settings_scope_does_not_qualify(self):
        # A dirty settings value would be silently dropped, so surface the error.
        assert not dirty_fields_are_safe_to_discard([
            _field('selected', Scope.user_state),
            _field('display_name', Scope.settings),
        ])

    def test_shared_user_state_does_not_qualify(self):
        # user_state_summary is shared across users and is *not* discarded on rebind.
        assert not dirty_fields_are_safe_to_discard([_field('votes', Scope.user_state_summary)])

    def test_content_scope_does_not_qualify(self):
        assert not dirty_fields_are_safe_to_discard([_field('data', Scope.content)])

    def test_structural_fields_alone_do_not_qualify(self):
        # Nothing per-user is staged, so this is not the case we understand.
        assert not dirty_fields_are_safe_to_discard([
            _field('children', Scope.children),
            _field('parent', Scope.parent),
        ])

    def test_nothing_dirty_does_not_qualify(self):
        assert not dirty_fields_are_safe_to_discard([])

    def test_field_without_scope_does_not_qualify(self):
        assert not dirty_fields_are_safe_to_discard([_field('mystery', None)])
