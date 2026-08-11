"""
Test for lms courseware app, module data (runtime data storage for XBlocks)
"""
import json
from functools import partial
from unittest.mock import Mock, patch
import pytest

from django.db import connections, DatabaseError
from django.test import TestCase
from xblock.core import XBlock
from xblock.exceptions import KeyValueMultiSaveError
from xblock.fields import BlockScope, Scope, ScopeIds

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.model_data import (
    DjangoKeyValueStore,
    FieldDataCache,
    InvalidScopeError,
    UserStateCache,
    _children_for_field_data_cache,
    _discard_staged_user_state,
)
from lms.djangoapps.courseware.user_state_client import LazyUserState
from lms.djangoapps.courseware.models import (
    StudentModule,
    XModuleStudentInfoField,
    XModuleStudentPrefsField,
    XModuleUserStateSummaryField
)
from lms.djangoapps.courseware.tests.factories import COURSE_KEY
from lms.djangoapps.courseware.tests.factories import LOCATION
from lms.djangoapps.courseware.tests.factories import StudentInfoFactory
from lms.djangoapps.courseware.tests.factories import StudentModuleFactory as cmfStudentModuleFactory
from lms.djangoapps.courseware.tests.factories import StudentPrefsFactory
from lms.djangoapps.courseware.tests.factories import UserStateSummaryFactory


def mock_field(scope, name):
    field = Mock()
    field.scope = scope
    field.name = name
    return field


def mock_block(fields=[]):  # lint-amnesty, pylint: disable=dangerous-default-value, missing-function-docstring
    block = Mock(entry_point=XBlock.entry_point)
    block.scope_ids = ScopeIds('user1', 'mock_problem', LOCATION('def_id'), LOCATION('usage_id'))
    block.module_class.fields.values.return_value = fields
    block.fields.values.return_value = fields
    block.module_class.__name__ = 'MockProblemModule'
    return block

# The user ids here are 1 because we make a student in the setUp functions, and
# they get an id of 1.  There's an assertion in setUp to ensure that assumption
# is still true.
user_state_summary_key = partial(DjangoKeyValueStore.Key, Scope.user_state_summary, None, LOCATION('usage_id'))
settings_key = partial(DjangoKeyValueStore.Key, Scope.settings, None, LOCATION('usage_id'))
user_state_key = partial(DjangoKeyValueStore.Key, Scope.user_state, 1, LOCATION('usage_id'))
prefs_key = partial(DjangoKeyValueStore.Key, Scope.preferences, 1, 'mock_problem')
user_info_key = partial(DjangoKeyValueStore.Key, Scope.user_info, 1, None)


class StudentModuleFactory(cmfStudentModuleFactory):
    module_state_key = LOCATION('usage_id')
    course_id = COURSE_KEY


class TestInvalidScopes(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username='user')
        self.field_data_cache = FieldDataCache(
            [mock_block([mock_field(Scope.user_state, 'a_field')])],
            COURSE_KEY,
            self.user,
        )
        self.kvs = DjangoKeyValueStore(self.field_data_cache)

    def test_invalid_scopes(self):
        for scope in (Scope(user=True, block=BlockScope.DEFINITION),
                      Scope(user=False, block=BlockScope.TYPE),
                      Scope(user=False, block=BlockScope.ALL)):
            key = DjangoKeyValueStore.Key(scope, None, None, 'field')

            self.assertRaises(InvalidScopeError, self.kvs.get, key)
            self.assertRaises(InvalidScopeError, self.kvs.set, key, 'value')
            self.assertRaises(InvalidScopeError, self.kvs.delete, key)
            self.assertRaises(InvalidScopeError, self.kvs.has, key)
            self.assertRaises(InvalidScopeError, self.kvs.set_many, {key: 'value'})


class OtherUserFailureTestMixin:
    """
    Mixin class to add test cases for failures when a user trying to use the kvs is not
    the one that instantiated the kvs.
    Doing a mixin rather than modifying StorageTestBase (below) because some scopes don't fail in this case, because
    they aren't bound to a particular user

    assumes that this is mixed into a class that defines other_key_factory and existing_field_name
    """
    def test_other_user_kvs_get_failure(self):
        """
        Test for assert failure when a user who didn't create the kvs tries to get from it it
        """
        with pytest.raises(AssertionError):
            self.kvs.get(self.other_key_factory(self.existing_field_name))

    def test_other_user_kvs_set_failure(self):
        """
        Test for assert failure when a user who didn't create the kvs tries to get from it it
        """
        with pytest.raises(AssertionError):
            self.kvs.set(self.other_key_factory(self.existing_field_name), "new_value")


class TestStudentModuleStorage(OtherUserFailureTestMixin, TestCase):
    """Tests for user_state storage via StudentModule"""
    other_key_factory = partial(DjangoKeyValueStore.Key, Scope.user_state, 2, LOCATION('usage_id'))  # user_id=2, not 1
    existing_field_name = "a_field"
    # Tell Django to clean out all databases, not just default
    databases = set(connections)

    def setUp(self):
        super().setUp()
        student_module = StudentModuleFactory(state=json.dumps({'a_field': 'a_value', 'b_field': 'b_value'}))
        self.user = student_module.student
        assert self.user.id == 1
        # check our assumption hard-coded in the key functions above.

        # There should be only one query to load a single block with a single user_state field
        with self.assertNumQueries(1):
            self.field_data_cache = FieldDataCache(
                [mock_block([mock_field(Scope.user_state, 'a_field')])],
                COURSE_KEY,
                self.user,
            )

        self.kvs = DjangoKeyValueStore(self.field_data_cache)

    def test_get_existing_field(self):
        "Test that getting an existing field in an existing StudentModule works"
        # This should only read from the cache, not the database
        with self.assertNumQueries(0):
            assert 'a_value' == self.kvs.get(user_state_key('a_field'))

    def test_get_missing_field(self):
        "Test that getting a missing field from an existing StudentModule raises a KeyError"
        # This should only read from the cache, not the database
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.get, user_state_key('not_a_field'))

    def test_set_existing_field(self):
        "Test that setting an existing user_state field changes the value"
        # We are updating a problem, so we write to courseware_studentmodulehistory
        # as well as courseware_studentmodule. We also need to read the database
        # to discover if something other than the DjangoXBlockUserStateClient
        # has written to the StudentModule (such as UserStateCache setting the score
        # on the StudentModule).
        with self.assertNumQueries(4, using='default'):
            with self.assertNumQueries(2, using='student_module_history'):
                self.kvs.set(user_state_key('a_field'), 'new_value')
        assert 1 == StudentModule.objects.all().count()
        assert {'b_field': 'b_value', 'a_field': 'new_value'} == json.loads(StudentModule.objects.all()[0].state)
        # lint-amnesty, pylint: disable=line-too-long

    def test_set_missing_field(self):
        "Test that setting a new user_state field changes the value"
        # We are updating a problem, so we write to courseware_studentmodulehistory
        # as well as courseware_studentmodule. We also need to read the database
        # to discover if something other than the DjangoXBlockUserStateClient
        # has written to the StudentModule (such as UserStateCache setting the score
        # on the StudentModule).
        with self.assertNumQueries(4, using='default'):
            with self.assertNumQueries(2, using='student_module_history'):
                self.kvs.set(user_state_key('not_a_field'), 'new_value')
        assert 1 == StudentModule.objects.all().count()
        assert {'b_field': 'b_value', 'a_field': 'a_value', 'not_a_field': 'new_value'} == json.loads(StudentModule.objects.all()[0].state)
        # lint-amnesty, pylint: disable=line-too-long

    def test_delete_existing_field(self):
        "Test that deleting an existing field removes it from the StudentModule"
        # We are updating a problem, so we write to courseware_studentmodulehistory
        # as well as courseware_studentmodule. We also need to read the database
        # to discover if something other than the DjangoXBlockUserStateClient
        # has written to the StudentModule (such as UserStateCache setting the score
        # on the StudentModule).
        with self.assertNumQueries(2, using='default'):
            with self.assertNumQueries(2, using='student_module_history'):
                self.kvs.delete(user_state_key('a_field'))
        assert 1 == StudentModule.objects.all().count()
        self.assertRaises(KeyError, self.kvs.get, user_state_key('not_a_field'))

    def test_delete_missing_field(self):
        "Test that deleting a missing field from an existing StudentModule raises a KeyError"
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.delete, user_state_key('not_a_field'))
        assert 1 == StudentModule.objects.all().count()
        assert {'b_field': 'b_value', 'a_field': 'a_value'} == json.loads(StudentModule.objects.all()[0].state)

    def test_has_existing_field(self):
        "Test that `has` returns True for existing fields in StudentModules"
        with self.assertNumQueries(0):
            assert self.kvs.has(user_state_key('a_field'))

    def test_has_missing_field(self):
        "Test that `has` returns False for missing fields in StudentModule"
        with self.assertNumQueries(0):
            assert not self.kvs.has(user_state_key('not_a_field'))

    def construct_kv_dict(self):
        """Construct a kv_dict that can be passed to set_many"""
        key1 = user_state_key('field_a')
        key2 = user_state_key('field_b')
        new_value = 'new value'
        newer_value = 'newer value'
        return {key1: new_value, key2: newer_value}

    def test_set_many(self):
        "Test setting many fields that are scoped to Scope.user_state"
        kv_dict = self.construct_kv_dict()

        # Scope.user_state is stored in a single row in the database, so we only
        # need to send a single update to that table.
        # We also are updating a problem, so we write to courseware student module history
        # We also need to read the database to discover if something other than the
        # DjangoXBlockUserStateClient has written to the StudentModule (such as
        # UserStateCache setting the score on the StudentModule).
        with self.assertNumQueries(4, using="default"):
            with self.assertNumQueries(2, using="student_module_history"):
                self.kvs.set_many(kv_dict)

        for key in kv_dict:
            assert self.kvs.get(key) == kv_dict[key]

    def test_set_many_failure(self):
        "Test failures when setting many fields that are scoped to Scope.user_state"
        kv_dict = self.construct_kv_dict()
        # because we're patching the underlying save, we need to ensure the
        # fields are in the cache
        for key in kv_dict:
            self.kvs.set(key, 'test_value')

        with patch('django.db.models.Model.save', side_effect=DatabaseError):
            with pytest.raises(KeyValueMultiSaveError) as exception_context:
                self.kvs.set_many(kv_dict)
        assert exception_context.value.saved_field_names == []


class TestMissingStudentModule(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    # Tell Django to clean out all databases, not just default
    databases = set(connections)

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create(username='user')
        assert self.user.id == 1
        # check our assumption hard-coded in the key functions above.

        # The block has no fields, so FDC shouldn't send any queries
        with self.assertNumQueries(0):
            self.field_data_cache = FieldDataCache(
                [mock_block()],
                COURSE_KEY,
                self.user,
            )
        self.kvs = DjangoKeyValueStore(self.field_data_cache)

    def test_get_field_from_missing_student_module(self):
        "Test that getting a field from a missing StudentModule raises a KeyError"
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.get, user_state_key('a_field'))

    def test_set_field_in_missing_student_module(self):
        "Test that setting a field in a missing StudentModule creates the student module"
        assert 0 == len(self.field_data_cache)
        assert 0 == StudentModule.objects.all().count()

        # We are updating a problem, so we write to courseware_studentmodulehistoryextended
        # as well as courseware_studentmodule. We also need to read the database
        # to discover if something other than the DjangoXBlockUserStateClient
        # has written to the StudentModule (such as UserStateCache setting the score
        # on the StudentModule).
        # Django 1.8 also has a number of other BEGIN and SAVESTATE queries.
        with self.assertNumQueries(4, using='default'):
            with self.assertNumQueries(2, using='student_module_history'):
                self.kvs.set(user_state_key('a_field'), 'a_value')

        assert 1 == sum(len(cache) for cache in self.field_data_cache.cache.values())
        assert 1 == StudentModule.objects.all().count()

        student_module = StudentModule.objects.all()[0]
        assert {'a_field': 'a_value'} == json.loads(student_module.state)
        assert self.user == student_module.student
        assert LOCATION('usage_id').replace(run=None) == student_module.module_state_key
        assert COURSE_KEY == student_module.course_id

    def test_delete_field_from_missing_student_module(self):
        "Test that deleting a field from a missing StudentModule raises a KeyError"
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.delete, user_state_key('a_field'))

    def test_has_field_for_missing_student_module(self):
        "Test that `has` returns False for missing StudentModules"
        with self.assertNumQueries(0):
            assert not self.kvs.has(user_state_key('a_field'))


class StorageTestBase:
    """
    A base class for that gets subclassed when testing each of the scopes.
    """
    # Disable pylint warnings that arise because of the way the child classes call
    # this base class -- pylint's static analysis can't keep up with it.
    # pylint: disable=no-member, not-callable

    factory = None
    scope = None
    key_factory = None
    storage_class = None

    def setUp(self):
        field_storage = self.factory.create()
        if hasattr(field_storage, 'student'):
            self.user = field_storage.student
        else:
            self.user = UserFactory.create()
        self.mock_block = mock_block([
            mock_field(self.scope, 'existing_field'),
            mock_field(self.scope, 'other_existing_field')])
        # Each field is stored as a separate row in the table,
        # but we can query them in a single query
        with self.assertNumQueries(1):
            self.field_data_cache = FieldDataCache(
                [self.mock_block],
                COURSE_KEY,
                self.user,
            )
        self.kvs = DjangoKeyValueStore(self.field_data_cache)

    def test_set_and_get_existing_field(self):
        with self.assertNumQueries(1):
            self.kvs.set(self.key_factory('existing_field'), 'test_value')
        with self.assertNumQueries(0):
            assert 'test_value' == self.kvs.get(self.key_factory('existing_field'))

    def test_get_existing_field(self):
        "Test that getting an existing field in an existing Storage Field works"
        with self.assertNumQueries(0):
            assert 'old_value' == self.kvs.get(self.key_factory('existing_field'))

    def test_get_missing_field(self):
        "Test that getting a missing field from an existing Storage Field raises a KeyError"
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.get, self.key_factory('missing_field'))

    def test_set_existing_field(self):
        "Test that setting an existing field changes the value"
        with self.assertNumQueries(1):
            self.kvs.set(self.key_factory('existing_field'), 'new_value')
        assert 1 == self.storage_class.objects.all().count()
        assert 'new_value' == json.loads(self.storage_class.objects.all()[0].value)

    def test_set_missing_field(self):
        "Test that setting a new field changes the value"
        with self.assertNumQueries(1):
            self.kvs.set(self.key_factory('missing_field'), 'new_value')
        assert 2 == self.storage_class.objects.all().count()
        assert 'old_value' == json.loads(self.storage_class.objects.get(field_name='existing_field').value)
        assert 'new_value' == json.loads(self.storage_class.objects.get(field_name='missing_field').value)

    def test_delete_existing_field(self):
        "Test that deleting an existing field removes it"
        with self.assertNumQueries(1):
            self.kvs.delete(self.key_factory('existing_field'))
        assert 0 == self.storage_class.objects.all().count()

    def test_delete_missing_field(self):
        "Test that deleting a missing field from an existing Storage Field raises a KeyError"
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.delete, self.key_factory('missing_field'))
        assert 1 == self.storage_class.objects.all().count()

    def test_has_existing_field(self):
        "Test that `has` returns True for an existing Storage Field"
        with self.assertNumQueries(0):
            assert self.kvs.has(self.key_factory('existing_field'))

    def test_has_missing_field(self):
        "Test that `has` return False for an existing Storage Field"
        with self.assertNumQueries(0):
            assert not self.kvs.has(self.key_factory('missing_field'))

    def construct_kv_dict(self):
        """Construct a kv_dict that can be passed to set_many"""
        key1 = self.key_factory('existing_field')
        key2 = self.key_factory('other_existing_field')
        new_value = 'new value'
        newer_value = 'newer value'
        return {key1: new_value, key2: newer_value}

    def test_set_many(self):
        """Test that setting many regular fields at the same time works"""
        kv_dict = self.construct_kv_dict()

        # Each field is a separate row in the database, hence
        # a separate query
        with self.assertNumQueries(len(kv_dict)):
            self.kvs.set_many(kv_dict)
        for key in kv_dict:
            assert self.kvs.get(key) == kv_dict[key]

    def test_set_many_failure(self):
        """Test that setting many regular fields with a DB error """
        kv_dict = self.construct_kv_dict()
        for key in kv_dict:
            with self.assertNumQueries(1):
                self.kvs.set(key, 'test value')

        with patch('django.db.models.Model.save', side_effect=[None, DatabaseError]):
            with pytest.raises(KeyValueMultiSaveError) as exception_context:
                self.kvs.set_many(kv_dict)

        exception = exception_context.value
        assert exception.saved_field_names == ['existing_field', 'other_existing_field']


class TestUserStateSummaryStorage(StorageTestBase, TestCase):
    """Tests for UserStateSummaryStorage"""
    factory = UserStateSummaryFactory
    scope = Scope.user_state_summary
    key_factory = user_state_summary_key
    storage_class = XModuleUserStateSummaryField


class TestStudentPrefsStorage(OtherUserFailureTestMixin, StorageTestBase, TestCase):
    """Tests for StudentPrefStorage"""
    factory = StudentPrefsFactory
    scope = Scope.preferences
    key_factory = prefs_key
    storage_class = XModuleStudentPrefsField
    other_key_factory = partial(DjangoKeyValueStore.Key, Scope.preferences, 2, 'mock_problem')  # user_id=2, not 1
    existing_field_name = "existing_field"


class TestStudentInfoStorage(OtherUserFailureTestMixin, StorageTestBase, TestCase):
    """Tests for StudentInfoStorage"""
    factory = StudentInfoFactory
    scope = Scope.user_info
    key_factory = user_info_key
    storage_class = XModuleStudentInfoField
    other_key_factory = partial(DjangoKeyValueStore.Key, Scope.user_info, 2, 'mock_problem')  # user_id=2, not 1
    existing_field_name = "existing_field"


class TestFieldDataCacheDynamicChildren(TestCase):
    """Tests for dynamic-child handling in FieldDataCache descendant prefetch."""

    def test_children_for_field_data_cache_uses_get_child_blocks(self):
        """
        Dynamic blocks that are already bound to a learner should only expose
        that learner's selected children for prefetch.
        """
        selected_child = Mock(name='selected_child')
        dynamic_block = Mock(name='dynamic_block')
        dynamic_block.has_dynamic_children.return_value = True
        dynamic_block.get_child_blocks.return_value = [selected_child]
        dynamic_block.get_children.side_effect = AssertionError(
            'get_children should not be called for dynamic blocks'
        )

        assert _children_for_field_data_cache(dynamic_block, Mock(is_authenticated=False)) == [selected_child]
        dynamic_block.get_child_blocks.assert_called_once_with()
        dynamic_block.get_children.assert_not_called()

    def test_children_for_field_data_cache_uses_get_children_for_static_blocks(self):
        """
        Static blocks should continue to prefetch all modulestore children.
        """
        static_child = Mock(name='static_child')
        required_child = Mock(name='required_child')
        static_block = Mock(name='static_block')
        static_block.has_dynamic_children.return_value = False
        static_block.get_children.return_value = [static_child]
        static_block.get_required_block_descriptors.return_value = [required_child]

        assert _children_for_field_data_cache(static_block, Mock(is_authenticated=False)) == \
            [static_child, required_child]
        static_block.get_children.assert_called_once_with()
        static_block.get_required_block_descriptors.assert_called_once_with()

    def test_children_for_field_data_cache_ignores_unbound_dynamic_children_without_persisted_selection(self):
        """
        An unbound block cannot see the learner's ``selected`` via get_child_blocks() (it would
        invent a fresh selection instead), so we don't call it. We fall back to a persisted-
        selection database read instead; with no saved selection either (this test), we fall
        back further to the full pool.
        """
        static_child = Mock(name='static_child')
        dynamic_block = Mock(name='dynamic_block')
        dynamic_block.has_dynamic_children.return_value = True
        dynamic_block.scope_ids.user_id = None
        dynamic_block.get_children.return_value = [static_child]
        dynamic_block.get_required_block_descriptors.return_value = []

        assert _children_for_field_data_cache(dynamic_block, Mock(is_authenticated=False)) == [static_child]
        dynamic_block.get_child_blocks.assert_not_called()

    def test_children_for_field_data_cache_falls_back_and_clears_staged_state(self):
        """
        A selection that cannot be saved during prefetch should fall back to all
        children *and* leave no dirty user-scoped field behind: whatever it staged
        would otherwise make the next block.save() raise the same error, far from here
        (see bind_for_student).
        """
        # pylint: disable=protected-access
        staged_field = Mock(name='selected_field')
        staged_field.name = 'selected'
        staged_field.scope = Scope.user_state
        settings_field = Mock(name='display_name_field')
        settings_field.name = 'display_name'
        settings_field.scope = Scope.settings

        static_child = Mock(name='static_child')
        dynamic_block = Mock(name='dynamic_block')
        dynamic_block.has_dynamic_children.return_value = True
        dynamic_block.get_child_blocks.side_effect = InvalidScopeError('user_state not supported')
        dynamic_block.get_children.return_value = [static_child]
        dynamic_block.get_required_block_descriptors.return_value = []
        dynamic_block._dirty_fields = {staged_field: ['a', 'b'], settings_field: 'Quiz'}

        assert _children_for_field_data_cache(dynamic_block, Mock(is_authenticated=False)) == [static_child]

        # The doomed user-scoped value is gone; the settings value is untouched.
        assert list(dynamic_block._dirty_fields) == [settings_field]
        staged_field._del_cached_value.assert_called_once_with(dynamic_block)
        settings_field._del_cached_value.assert_not_called()

    def test_discard_staged_user_state_without_dirty_fields(self):
        """
        Blocks with nothing staged are left alone and report nothing discarded.
        """
        # pylint: disable=protected-access
        block = Mock(name='block')
        block._dirty_fields = {}
        assert _discard_staged_user_state(block) == []

    def _configure_leaf_block(self, block, user_state_field):
        """Configure a non-dynamic leaf mock used in descendant-walk tests."""
        block.get_children.return_value = []
        block.get_required_block_descriptors.return_value = []
        block.has_dynamic_children.return_value = False
        block.fields.values.return_value = [user_state_field]
        block.has_score = False
        block.location = LOCATION('usage_id')

    @patch('lms.djangoapps.courseware.model_data.modulestore')
    def test_add_block_descendents_prefetches_only_selected_dynamic_children(self, mock_modulestore):
        """
        add_block_descendents should not walk unselected modulestore children.
        """
        mock_modulestore.return_value.bulk_operations.return_value.__enter__ = Mock(return_value=None)
        mock_modulestore.return_value.bulk_operations.return_value.__exit__ = Mock(return_value=False)

        user_state_field = mock_field(Scope.user_state, 'state')

        unselected_children = [Mock(name=f'unselected_{index}') for index in range(3)]
        selected_children = [Mock(name='selected_0'), Mock(name='selected_1')]
        for child in selected_children + unselected_children:
            self._configure_leaf_block(child, user_state_field)

        library_content = Mock(name='library_content')
        library_content.has_dynamic_children.return_value = True
        library_content.get_child_blocks.return_value = selected_children
        library_content.get_children.return_value = unselected_children + selected_children
        library_content.get_required_block_descriptors.return_value = []
        library_content.fields.values.return_value = [user_state_field]
        library_content.has_score = False
        library_content.location = LOCATION('library_content')

        vertical = Mock(name='vertical')
        vertical.has_dynamic_children.return_value = False
        vertical.get_children.return_value = [library_content]
        vertical.get_required_block_descriptors.return_value = []
        vertical.fields.values.return_value = [user_state_field]
        vertical.has_score = False
        vertical.location = LOCATION('vertical')

        user = UserFactory.create(username='dynamic_children_user')
        field_data_cache = FieldDataCache([], COURSE_KEY, user)

        cached_blocks = []

        def capture_cache_fields(fields, blocks, aside_types):  # lint-amnesty, pylint: disable=unused-argument
            cached_blocks.extend(blocks)

        with patch.object(UserStateCache, 'cache_fields', side_effect=capture_cache_fields):
            field_data_cache.add_block_descendents(vertical)

        cached_block_names = {block._mock_name for block in cached_blocks}  # pylint: disable=protected-access
        assert 'vertical' in cached_block_names
        assert 'library_content' in cached_block_names
        assert 'selected_0' in cached_block_names
        assert 'selected_1' in cached_block_names
        assert 'unselected_0' not in cached_block_names
        assert 'unselected_1' not in cached_block_names
        assert 'unselected_2' not in cached_block_names
        library_content.get_child_blocks.assert_called_once_with()

    @patch('lms.djangoapps.courseware.model_data.modulestore')
    def test_add_block_descendents_recurses_nested_dynamic_and_static(self, mock_modulestore):
        """
        Nested static containers under selected dynamic children should still be walked.
        """
        mock_modulestore.return_value.bulk_operations.return_value.__enter__ = Mock(return_value=None)
        mock_modulestore.return_value.bulk_operations.return_value.__exit__ = Mock(return_value=False)

        user_state_field = mock_field(Scope.user_state, 'state')

        nested_problem = Mock(name='nested_problem')
        self._configure_leaf_block(nested_problem, user_state_field)

        nested_vertical = Mock(name='nested_vertical')
        nested_vertical.has_dynamic_children.return_value = False
        nested_vertical.get_children.return_value = [nested_problem]
        nested_vertical.get_required_block_descriptors.return_value = []
        nested_vertical.fields.values.return_value = [user_state_field]
        nested_vertical.has_score = False
        nested_vertical.location = LOCATION('nested_vertical')

        unselected_nested = Mock(name='unselected_nested')
        self._configure_leaf_block(unselected_nested, user_state_field)

        library_content = Mock(name='library_content')
        library_content.has_dynamic_children.return_value = True
        library_content.get_child_blocks.return_value = [nested_vertical]
        library_content.get_children.return_value = [nested_vertical, unselected_nested]
        library_content.get_required_block_descriptors.return_value = []
        library_content.fields.values.return_value = [user_state_field]
        library_content.has_score = False
        library_content.location = LOCATION('library_content')

        vertical = Mock(name='vertical')
        vertical.has_dynamic_children.return_value = False
        vertical.get_children.return_value = [library_content]
        vertical.get_required_block_descriptors.return_value = []
        vertical.fields.values.return_value = [user_state_field]
        vertical.has_score = False
        vertical.location = LOCATION('vertical')

        user = UserFactory.create(username='nested_dynamic_children_user')
        field_data_cache = FieldDataCache([], COURSE_KEY, user)
        cached_blocks = []

        def capture_cache_fields(fields, blocks, aside_types):  # lint-amnesty, pylint: disable=unused-argument
            cached_blocks.extend(blocks)

        with patch.object(UserStateCache, 'cache_fields', side_effect=capture_cache_fields):
            field_data_cache.add_block_descendents(vertical)

        cached_block_names = {block._mock_name for block in cached_blocks}  # pylint: disable=protected-access
        assert cached_block_names == {
            'vertical',
            'library_content',
            'nested_vertical',
            'nested_problem',
        }
        library_content.get_child_blocks.assert_called_once_with()
        nested_vertical.get_children.assert_called_once_with()


class TestFieldDataCachePersistedSelectionFallback(TestCase):
    """
    Tests that prefetch narrows to a question bank's saved per-learner picks
    (read straight from the database) when the block *isn't* bound to the
    learner yet -- the common case, since FieldDataCache prefetch normally
    runs before any block in the tree gets bound. This is the fallback layer
    underneath TestFieldDataCacheDynamicChildren's bound-block path above:
    that path only helps once a block is bound (e.g. a shell-mode render of
    just that block); this path helps everywhere else, as long as the
    learner has visited before and a selection was already persisted.

    Note the fake "block" objects below are explicitly marked unbound
    (mock_block() sets a real scope_ids.user_id by default, so we clear it)
    and never need to claim they know who the learner is -- the DB read
    doesn't ask the block at all, which is the point.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.parent_location = COURSE_KEY.make_usage_key('library_content', 'pool')
        self.pool_locations = [
            COURSE_KEY.make_usage_key('problem', f'p{i}') for i in range(6)
        ]

    def _make_parent_block(self):
        """
        Fake stand-in for a question-bank block that has 6 possible problems in
        its pool (self.pool_locations), any of which might get picked for a
        given learner. Unbound, so add_block_descendents can't just ask it via
        get_child_blocks() -- it has to fall back to the persisted-selection
        database read.
        """
        pool_blocks = {}
        for loc in self.pool_locations:
            child = mock_block()
            child.location = loc
            child.has_dynamic_children.return_value = False
            child.get_children.return_value = []
            child.get_required_block_descriptors.return_value = []
            pool_blocks[loc] = child

        parent = mock_block()
        parent.location = self.parent_location
        parent.scope_ids.user_id = None
        parent.has_dynamic_children.return_value = True
        parent.get_children.return_value = list(pool_blocks.values())
        parent.get_required_block_descriptors.return_value = []
        parent.get_child.side_effect = pool_blocks.get
        return parent

    def test_narrows_prefetch_to_persisted_selection(self):
        """
        With 3 saved picks out of a 6-problem pool, prefetch loads only those 3
        (via get_child()) and never calls get_children().
        """
        StudentModule.objects.create(
            student=self.user,
            course_id=COURSE_KEY,
            module_state_key=self.parent_location,
            module_type='library_content',
            state=json.dumps({'selected': [['problem', 'p0'], ['problem', 'p2'], ['problem', 'p4']]}),
        )
        parent = self._make_parent_block()

        cache = FieldDataCache([], COURSE_KEY, self.user)
        cache.add_block_descendents(parent)

        called_keys = {call.args[0] for call in parent.get_child.call_args_list}
        assert called_keys == {
            COURSE_KEY.make_usage_key('problem', 'p0'),
            COURSE_KEY.make_usage_key('problem', 'p2'),
            COURSE_KEY.make_usage_key('problem', 'p4'),
        }
        parent.get_children.assert_not_called()

    def test_falls_back_to_full_pool_when_no_selection_saved(self):
        """
        With no saved picks (no StudentModule row for this block/user), prefetch
        falls back to loading the whole pool, same as before this fix.
        """
        parent = self._make_parent_block()

        cache = FieldDataCache([], COURSE_KEY, self.user)
        cache.add_block_descendents(parent)

        parent.get_children.assert_called_once()
        parent.get_child.assert_not_called()

    def test_ignores_empty_selection(self):
        """
        A saved row with an empty pick list is treated the same as no row at
        all -- falls back to the full pool, not down to zero problems.
        """
        StudentModule.objects.create(
            student=self.user,
            course_id=COURSE_KEY,
            module_state_key=self.parent_location,
            module_type='library_content',
            state=json.dumps({'selected': []}),
        )
        parent = self._make_parent_block()

        cache = FieldDataCache([], COURSE_KEY, self.user)
        cache.add_block_descendents(parent)

        parent.get_children.assert_called_once()
        parent.get_child.assert_not_called()

    def test_falls_back_on_malformed_selection(self):
        """
        A saved `selected` value that isn't a list of (block_type, block_id)
        pairs shouldn't blow up the render -- fall back to the full pool, same
        as any other case where we can't make sense of what's saved.
        """
        StudentModule.objects.create(
            student=self.user,
            course_id=COURSE_KEY,
            module_state_key=self.parent_location,
            module_type='library_content',
            state=json.dumps({'selected': ['not-a-pair', 'also-not-a-pair']}),
        )
        parent = self._make_parent_block()

        cache = FieldDataCache([], COURSE_KEY, self.user)
        cache.add_block_descendents(parent)

        parent.get_children.assert_called_once()
        parent.get_child.assert_not_called()


class TestUserStateCacheLazyParse(TestCase):
    """Tests for lazy JSON handling inside UserStateCache."""
    databases = set(connections)

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username='lazy_cache_user')
        assert self.user.id  # ensure persisted
        self.usage_key = LOCATION('usage_id')
        StudentModuleFactory(
            student=self.user,
            module_state_key=self.usage_key,
            state=json.dumps({'a_field': 'a_value', 'b_field': 'b_value'}),
        )
        self.block = mock_block([
            mock_field(Scope.user_state, 'a_field'),
            mock_field(Scope.user_state, 'b_field'),
        ])
        self.cache = UserStateCache(self.user, COURSE_KEY)

    def test_cache_fields_stores_lazy_state(self):
        self.cache.cache_fields(
            [mock_field(Scope.user_state, 'a_field')],
            [self.block],
            [],
        )
        stored = self.cache._cache[self.usage_key]  # pylint: disable=protected-access
        assert isinstance(stored, LazyUserState)
        assert not stored.is_parsed

    def test_get_parses_on_field_read(self):
        self.cache.cache_fields([], [self.block], [])
        stored = self.cache._cache[self.usage_key]  # pylint: disable=protected-access
        assert not stored.is_parsed

        value = self.cache.get(user_state_key('a_field'))
        assert value == 'a_value'
        assert stored.is_parsed
        assert self.cache.get(user_state_key('b_field')) == 'b_value'

    def test_has_parses_for_membership(self):
        self.cache.cache_fields([], [self.block], [])
        assert self.cache.has(user_state_key('a_field'))
        assert not self.cache.has(user_state_key('missing_field'))

    def test_set_many_overlays_without_dropping_sibling_fields(self):
        self.cache.cache_fields([], [self.block], [])
        self.cache.set(user_state_key('a_field'), 'new_value')

        assert self.cache.get(user_state_key('a_field')) == 'new_value'
        # Sibling field must remain readable after partial write overlay.
        assert self.cache.get(user_state_key('b_field')) == 'b_value'

    def test_delete_after_lazy_load(self):
        self.cache.cache_fields([], [self.block], [])
        self.cache.delete(user_state_key('b_field'))
        assert not self.cache.has(user_state_key('b_field'))
        assert self.cache.get(user_state_key('a_field')) == 'a_value'
