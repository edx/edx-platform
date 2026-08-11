"""
Black-box tests of the DjangoUserStateClient against the semantics
defined in edx_user_state_client.
"""

import pytz
from opaque_keys.edx.locator import (
    BlockUsageLocator,
    CourseLocator,
    LibraryLocatorV2,
    LibraryUsageLocatorV2,
)
from xblock.fields import Scope
from datetime import datetime
from unittest import TestCase
from unittest.mock import patch
from collections import defaultdict
from django.db import connections

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.user_state_client import (
    DjangoXBlockUserStateClient,
    LazyUserState,
    XBlockUserStateClient,
    XBlockUserState,
    loads_user_state,
)
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order


class _UserStateClientTestUtils(TestCase):
    """
    Utility methods for implementing blackbox XBlockUserStateClient tests.

    User and Block indexes should provide unique user ids and UsageKeys.
    Course indexes should be assigned to blocks by using integer division by 1000
    (this allows for tests of up to 1000 blocks per course).
    """

    __test__ = False

    scope = Scope.user_state
    client = None

    @staticmethod
    def _user(user):
        """Return the username for user ``user``."""
        return f"user{user}"

    def _block(self, block):
        """Return a UsageKey for the block ``block``."""
        course = block // 1000
        return BlockUsageLocator(
            self._course(course),
            self._block_type(block),
            f'block{block}'
        )

    @staticmethod
    def _block_type(block):  # pylint: disable=unused-argument
        """Return the block type for the specified ``block``."""
        return 'block_type'

    @staticmethod
    def _course(course):
        """Return a CourseKey for the course ``course``"""
        return CourseLocator(
            f'org{course}',
            f'course{course}',
            f'run{course}',
        )

    def get(self, user, block, fields=None):
        """
        Get the state for the specified user and block.

        This wraps :meth:`~XBlockUserStateClient.get`
        to take indexes rather than actual values to make tests easier
        to write concisely.
        """
        return self.client.get(
            username=self._user(user),
            block_key=self._block(block),
            scope=self.scope,
            fields=fields
        )

    def set(self, user, block, state):
        """
        Set the state for the specified user and block.

        This wraps :meth:`~XBlockUserStateClient.set`
        to take indexes rather than actual values to make tests easier
        to write concisely.
        """
        return self.client.set(
            username=self._user(user),
            block_key=self._block(block),
            state=state,
            scope=self.scope,
        )

    def delete(self, user, block, fields=None):
        """
        Delete the state for the specified user and block.

        This wraps :meth:`~XBlockUserStateClient.delete`
        to take indexes rather than actual values to make tests easier
        to write concisely.
        """
        return self.client.delete(
            username=self._user(user),
            block_key=self._block(block),
            scope=self.scope,
            fields=fields
        )

    def get_many(self, user, blocks, fields=None):
        """
        Get the state for the specified user and blocks.

        This wraps :meth:`~XBlockUserStateClient.get_many`
        to take indexes rather than actual values to make tests easier
        to write concisely.
        """
        return self.client.get_many(
            username=self._user(user),
            block_keys=[self._block(block) for block in blocks],
            scope=self.scope,
            fields=fields,
        )

    def set_many(self, user, block_to_state):
        """
        Set the state for the specified user and blocks.

        This wraps :meth:`~XBlockUserStateClient.set_many`
        to take indexes rather than actual values to make tests easier
        to write concisely.
        """
        return self.client.set_many(
            username=self._user(user),
            block_keys_to_state={
                self._block(block): state
                for block, state
                in list(block_to_state.items())
            },
            scope=self.scope,
        )

    def delete_many(self, user, blocks, fields=None):
        """
        Delete the state for the specified user and blocks.

        This wraps :meth:`~XBlockUserStateClient.delete_many`
        to take indexes rather than actual values to make tests easier
        to write concisely.
        """
        return self.client.delete_many(
            username=self._user(user),
            block_keys=[self._block(block) for block in blocks],
            scope=self.scope,
            fields=fields,
        )

    def get_history(self, user, block):
        """
        Return the state history for the specified user and block.

        This wraps :meth:`~XBlockUserStateClient.get_history`
        to take indexes rather than actual values to make tests easier
        to write concisely.
        """
        return self.client.get_history(
            username=self._user(user),
            block_key=self._block(block),
            scope=self.scope,
        )

    def iter_all_for_block(self, block):
        """
        Yield the state for all users for the specified block.

        This wraps :meth:`~XBlockUserStateClient.iter_all_for_blocks`
        to take indexes rather than actual values, to make tests easier
        to write concisely.
        """
        return self.client.iter_all_for_block(
            block_key=self._block(block),
            scope=self.scope,
        )

    def iter_all_for_course(self, course, block_type=None):
        """
        Yield the state for all users for the specified block.

        This wraps :meth:`~XBlockUserStateClient.iter_all_for_blocks`
        to take indexes rather than actual values, to make tests easier
        to write concisely.
        """
        return self.client.iter_all_for_course(
            course_key=self._course(course),
            block_type=block_type,
            scope=self.scope,
        )


class _UserStateClientTestCRUD(_UserStateClientTestUtils):
    """
    Blackbox tests of basic XBlockUserStateClient get/set/delete functionality.
    """

    __test__ = False

    def test_set_get(self):
        self.set(user=0, block=0, state={'a': 'b'})
        self.assertEqual(self.get(user=0, block=0).state, {'a': 'b'})

    def test_set_get_get(self):
        self.set(user=0, block=0, state={'a': 'b'})
        self.assertEqual(self.get(user=0, block=0).state, {'a': 'b'})
        self.assertEqual(self.get(user=0, block=0).state, {'a': 'b'})

    def test_set_set_get(self):
        self.set(user=0, block=0, state={'a': 'b'})
        self.set(user=0, block=0, state={'a': 'c'})
        self.assertEqual(self.get(user=0, block=0).state, {'a': 'c'})

    def test_set_overlay(self):
        self.set(user=0, block=0, state={'a': 'b'})
        self.set(user=0, block=0, state={'b': 'c'})
        self.assertEqual(self.get(user=0, block=0).state, {'a': 'b', 'b': 'c'})

    def test_get_fields(self):
        self.set(user=0, block=0, state={'a': 'b', 'b': 'c'})
        self.assertEqual(self.get(user=0, block=0, fields=['a']).state, {'a': 'b'})
        self.assertEqual(self.get(user=0, block=0, fields=['b']).state, {'b': 'c'})
        self.assertEqual(self.get(user=0, block=0, fields=['a', 'b']).state, {'a': 'b', 'b': 'c'})

    def test_get_missing_block(self):
        self.set(user=0, block=1, state={})
        with self.assertRaises(self.client.DoesNotExist):
            self.get(user=0, block=0)

    def test_get_missing_user(self):
        self.set(user=1, block=0, state={})
        with self.assertRaises(self.client.DoesNotExist):
            self.get(user=0, block=0)

    def test_get_missing_field(self):
        self.set(user=0, block=0, state={'a': 'b'})
        self.assertEqual(self.get(user=0, block=0, fields=['a', 'b']).state, {'a': 'b'})

    def test_set_two_users(self):
        self.set(user=0, block=0, state={'a': 'b'})
        self.set(user=1, block=0, state={'b': 'c'})
        self.assertEqual(self.get(user=0, block=0).state, {'a': 'b'})
        self.assertEqual(self.get(user=1, block=0).state, {'b': 'c'})

    def test_set_two_blocks(self):
        self.set(user=0, block=0, state={'a': 'b'})
        self.set(user=0, block=1, state={'b': 'c'})
        self.assertEqual(self.get(user=0, block=0).state, {'a': 'b'})
        self.assertEqual(self.get(user=0, block=1).state, {'b': 'c'})

    def test_set_many(self):
        self.set_many(user=0, block_to_state={0: {'a': 'b'}, 1: {'b': 'c'}})
        self.assertEqual(self.get(user=0, block=0).state, {'a': 'b'})
        self.assertEqual(self.get(user=0, block=1).state, {'b': 'c'})

    def test_get_many(self):
        self.set_many(user=0, block_to_state={0: {'a': 'b'}, 1: {'b': 'c'}})
        self.assertCountEqual(
            [(entry.username, entry.block_key, entry.state) for entry in self.get_many(user=0, blocks=[0, 1])],
            [
                (self._user(0), self._block(0), {'a': 'b'}),
                (self._user(0), self._block(1), {'b': 'c'})
            ]
        )

    def test_delete(self):
        with self.assertRaises(self.client.DoesNotExist):
            self.get(user=0, block=0)

        self.set(user=0, block=0, state={'a': 'b'})
        self.assertEqual(self.get(user=0, block=0).state, {'a': 'b'})

        self.delete(user=0, block=0)
        with self.assertRaises(self.client.DoesNotExist):
            self.get(user=0, block=0)

    def test_delete_partial(self):
        with self.assertRaises(self.client.DoesNotExist):
            self.get(user=0, block=0)

        self.set(user=0, block=0, state={'a': 'b', 'b': 'c'})
        self.assertEqual(self.get(user=0, block=0).state, {'a': 'b', 'b': 'c'})

        self.delete(user=0, block=0, fields=['a'])
        self.assertEqual(self.get(user=0, block=0).state, {'b': 'c'})

    def test_delete_last_field(self):
        with self.assertRaises(self.client.DoesNotExist):
            self.get(user=0, block=0)

        self.set(user=0, block=0, state={'a': 'b'})
        self.assertEqual(self.get(user=0, block=0).state, {'a': 'b'})

        self.delete(user=0, block=0, fields=['a'])
        with self.assertRaises(self.client.DoesNotExist):
            self.get(user=0, block=0)

    def test_delete_many(self):
        self.assertCountEqual(self.get_many(user=0, blocks=[0, 1]), [])

        self.set_many(user=0, block_to_state={
            0: {'a': 'b'},
            1: {'b': 'c'},
        })

        self.delete_many(user=0, blocks=[0, 1])
        self.assertCountEqual(self.get_many(user=0, blocks=[0, 1]), [])

    def test_delete_many_partial(self):
        self.assertCountEqual(self.get_many(user=0, blocks=[0, 1]), [])

        self.set_many(user=0, block_to_state={
            0: {'a': 'b'},
            1: {'b': 'c'},
        })

        self.delete_many(user=0, blocks=[0, 1], fields=['a'])
        self.assertCountEqual(
            [(entry.block_key, entry.state) for entry in self.get_many(user=0, blocks=[0, 1])],
            [(self._block(1), {'b': 'c'})]
        )

    def test_delete_many_last_field(self):
        self.assertCountEqual(self.get_many(user=0, blocks=[0, 1]), [])

        self.set_many(user=0, block_to_state={
            0: {'a': 'b'},
            1: {'b': 'c'},
        })

        self.delete_many(user=0, blocks=[0, 1], fields=['a', 'b'])
        self.assertCountEqual(self.get_many(user=0, blocks=[0, 1]), [])

    def test_get_mod_date(self):
        start_time = datetime.now(pytz.utc)
        self.set_many(user=0, block_to_state={0: {'a': 'b'}, 1: {'b': 'c'}})
        end_time = datetime.now(pytz.utc)

        mod_dates = self.get(user=0, block=0)

        self.assertCountEqual(list(mod_dates.state.keys()), ["a"])
        self.assertGreater(mod_dates.updated, start_time)
        self.assertLess(mod_dates.updated, end_time)

    def test_get_many_mod_date(self):
        start_time = datetime.now(pytz.utc)
        self.set_many(
            user=0,
            block_to_state={0: {'a': 'b'}, 1: {'a': 'd'}})
        mid_time = datetime.now(pytz.utc)
        self.set_many(
            user=0,
            block_to_state={1: {'a': 'c'}})
        end_time = datetime.now(pytz.utc)

        mod_dates = list(self.get_many(
            user=0,
            blocks=[0, 1],
            fields=["a"]))

        self.assertCountEqual(
            [result.block_key for result in mod_dates],
            [self._block(0), self._block(1)])
        self.assertCountEqual(
            list(mod_dates[0].state.keys()),
            ["a"])
        self.assertGreater(mod_dates[0].updated, start_time)
        self.assertLess(mod_dates[0].updated, mid_time)
        self.assertCountEqual(
            list(mod_dates[1].state.keys()),
            ["a"])
        self.assertGreater(mod_dates[1].updated, mid_time)
        self.assertLess(mod_dates[1].updated, end_time)


class _UserStateClientTestHistory(_UserStateClientTestUtils):
    """
    Blackbox tests of basic XBlockUserStateClient history functionality.
    """

    __test__ = False

    def test_empty_history(self):
        with self.assertRaises(self.client.DoesNotExist):
            next(self.get_history(user=0, block=0))

    def test_single_history(self):
        self.set(user=0, block=0, state={'a': 'b'})
        self.assertEqual(
            [history.state for history in self.get_history(user=0, block=0)],
            [{'a': 'b'}]
        )

    def test_multiple_history_entries(self):
        for val in range(3):
            self.set(user=0, block=0, state={'a': val})

        history = list(self.get_history(user=0, block=0))

        self.assertEqual(
            [entry.state for entry in history],
            [{'a': 2}, {'a': 1}, {'a': 0}]
        )

        # Assert that the update times are reverse sorted (by
        # actually reverse-sorting them, and then asserting that
        # the sorted version is the same as the initial version)
        self.assertEqual(
            [entry.updated for entry in history],
            sorted((entry.updated for entry in history), reverse=True)
        )

    def test_history_distinct(self):
        self.set(user=0, block=0, state={'a': 0})
        self.set(user=0, block=1, state={'a': 1})

        self.assertEqual(
            [history.state for history in self.get_history(user=0, block=0)],
            [{'a': 0}]
        )
        self.assertEqual(
            [history.state for history in self.get_history(user=0, block=1)],
            [{'a': 1}]
        )

    def test_history_after_delete(self):
        self.set(user=0, block=0, state={str(val): val for val in range(3)})
        for val in range(3):
            self.delete(user=0, block=0, fields=[str(val)])

        self.assertEqual(
            [history.state for history in self.get_history(user=0, block=0)],
            [
                None,
                {'2': 2},
                {'2': 2, '1': 1},
                {'2': 2, '1': 1, '0': 0}
            ]
        )

    def test_set_many_with_history(self):
        self.set_many(user=0, block_to_state={0: {'a': 0}, 1: {'a': 1}})

        self.assertEqual(
            [history.state for history in self.get_history(user=0, block=0)],
            [{'a': 0}]
        )
        self.assertEqual(
            [history.state for history in self.get_history(user=0, block=1)],
            [{'a': 1}]
        )


class _UserStateClientTestIterAll(_UserStateClientTestUtils):
    """
    Blackbox tests of basic XBlockUserStateClient global iteration functionality.
    """

    __test__ = False

    def test_iter_blocks_empty(self):
        self.assertCountEqual(
            self.iter_all_for_block(block=0),
            []
        )

    def test_iter_blocks_single_user(self):
        self.set_many(user=0, block_to_state={0: {'a': 'b'}, 1: {'c': 'd'}})

        self.assertCountEqual(
            (item.state for item in self.iter_all_for_block(block=0)),
            [{'a': 'b'}]
        )

        self.assertCountEqual(
            (item.state for item in self.iter_all_for_block(block=1)),
            [{'c': 'd'}]
        )

    def test_iter_blocks_many_users(self):
        for user in range(3):
            self.set_many(user, {0: {'a': user}, 1: {'c': user}})

        self.assertCountEqual(
            ((item.username, item.state) for item in self.iter_all_for_block(block=0)),
            [
                (self._user(0), {'a': 0}),
                (self._user(1), {'a': 1}),
                (self._user(2), {'a': 2}),
            ]
        )

    def test_iter_blocks_deleted_block(self):
        for user in range(3):
            self.set_many(user, {0: {'a': user}, 1: {'c': user}})

        self.delete(user=1, block=0)

        self.assertCountEqual(
            ((item.username, item.state) for item in self.iter_all_for_block(block=0)),
            [
                (self._user(0), {'a': 0}),
                (self._user(2), {'a': 2}),
            ]
        )

    def test_iter_course_empty(self):
        self.assertCountEqual(
            self.iter_all_for_course(course=0),
            []
        )

    def test_iter_course_single_user(self):
        self.set_many(user=0, block_to_state={0: {'a': 'b'}, 1001: {'c': 'd'}})

        self.assertCountEqual(
            (item.state for item in self.iter_all_for_course(course=0)),
            [{'a': 'b'}]
        )

        self.assertCountEqual(
            (item.state for item in self.iter_all_for_course(course=1)),
            [{'c': 'd'}]
        )

    def test_iter_course_many_users(self):
        for user in range(2):
            for course in range(2):
                self.set_many(
                    user,
                    block_to_state={
                        course * 1000 + 0: {'course': course},
                        course * 1000 + 1: {'user': user}
                    }
                )

        self.assertCountEqual(
            ((item.username, item.block_key, item.state) for item in self.iter_all_for_course(course=1)),
            [
                (self._user(0), self._block(1000), {'course': 1}),
                (self._user(0), self._block(1001), {'user': 0}),
                (self._user(1), self._block(1000), {'course': 1}),
                (self._user(1), self._block(1001), {'user': 1}),
            ]
        )

    def test_iter_course_deleted_block(self):
        for user in range(2):
            for course in range(2):
                self.set_many(
                    user,
                    block_to_state={
                        course * 1000 + 0: {'course': user},
                        course * 1000 + 1: {'user': user}
                    }
                )

        self.delete(user=1, block=0)
        self.delete(user=1, block=1001)

        self.assertCountEqual(
            ((item.username, item.block_key, item.state) for item in self.iter_all_for_course(course=0)),
            [
                (self._user(0), self._block(0), {'course': 0}),
                (self._user(0), self._block(1), {'user': 0}),
                (self._user(1), self._block(1), {'user': 1}),
            ]
        )

        self.assertCountEqual(
            ((item.username, item.block_key, item.state) for item in self.iter_all_for_course(course=1)),
            [
                (self._user(0), self._block(1000), {'course': 0}),
                (self._user(0), self._block(1001), {'user': 0}),
                (self._user(1), self._block(1000), {'course': 1}),
            ]
        )


class UserStateClientTestBase(_UserStateClientTestCRUD,
                              _UserStateClientTestHistory,
                              _UserStateClientTestIterAll):
    """
    Blackbox tests for XBlockUserStateClient implementations.
    """

    __test__ = False


class DictUserStateClient(XBlockUserStateClient):
    """
    The simplest possible in-memory implementation of DictUserStateClient,
    for testing the tests.
    """
    def __init__(self):
        self._history = {}

    def _add_state(self, username, block_key, scope, state):
        """
        Add the specified state to the state history of this block.
        """
        history_list = self._history.setdefault((username, block_key, scope), [])
        history_list.insert(0, XBlockUserState(username, block_key, state, datetime.now(pytz.utc), scope))

    def get_many(self, username, block_keys, scope=Scope.user_state, fields=None):
        for key in block_keys:
            if (username, key, scope) not in self._history:
                continue

            entry = self._history[(username, key, scope)][0]

            if entry.state is None:
                continue

            if fields is None:
                current_fields = list(entry.state.keys())
            else:
                current_fields = fields

            yield entry._replace(state={
                field: entry.state[field]
                for field in current_fields
                if field in entry.state
            })

    def set_many(self, username, block_keys_to_state, scope=Scope.user_state):
        for key, state in list(block_keys_to_state.items()):
            if (username, key, scope) in self._history:
                current_state = self._history[(username, key, scope)][0].state.copy()
                current_state.update(state)
                self._add_state(username, key, scope, current_state)
            else:
                self._add_state(username, key, scope, state)

    def delete_many(self, username, block_keys, scope=Scope.user_state, fields=None):
        for key in block_keys:
            if (username, key, scope) not in self._history:
                continue

            if fields is None:
                self._add_state(username, key, scope, None)
            else:
                state = self._history[(username, key, scope)][0].state.copy()
                for field in fields:
                    if field in state:
                        del state[field]
                if not state:
                    self._add_state(username, key, scope, None)
                else:
                    self._add_state(username, key, scope, state)

    def get_history(self, username, block_key, scope=Scope.user_state):
        """
        Retrieve history of state changes for a given block for a given
        student.  We don't guarantee that history for many blocks will be fast.

        If the specified block doesn't exist, raise :class:`~DoesNotExist`.

        Arguments:
            username: The name of the user whose history should be retrieved.
            block_key (UsageKey): The UsageKey identifying which xblock history to retrieve.
            scope (Scope): The scope to load data from.

        Yields:
            UserStateHistory entries for each modification to the specified XBlock, from latest
            to earliest.
        """
        if (username, block_key, scope) not in self._history:
            raise self.DoesNotExist(username, block_key, scope)

        yield from self._history[(username, block_key, scope)]

    def iter_all_for_block(self, block_key, scope=Scope.user_state):
        """
        You get no ordering guarantees. If you're using this method, you should be running in an
        async task.
        """
        for (_, key, one_scope), entries in list(self._history.items()):
            if entries[0].state is None:
                continue

            if key == block_key and one_scope == scope:
                yield entries[0]

    def iter_all_for_course(self, course_key, block_type=None, scope=Scope.user_state):
        """
        You get no ordering guarantees. If you're using this method, you should be running in an
        async task.
        """
        for (_, key, one_scope), entries in list(self._history.items()):
            if entries[0].state is None:
                continue

            if (
                    key.course_key == course_key and
                    one_scope == scope and
                    (block_type is None or key.block_type == block_type)
            ):

                yield entries[0]


class TestDictUserStateClient(UserStateClientTestBase):
    """
    Tests of the DictUserStateClient backend.
    """
    __test__ = True

    def setUp(self):
        super().setUp()
        self.client = DictUserStateClient()


class TestDjangoUserStateClient(UserStateClientTestBase, ModuleStoreTestCase):
    """
    Tests of the DjangoUserStateClient backend.
    It reuses all tests from :class:`~UserStateClientTestBase`.
    """
    __test__ = True
    # Tell Django to clean out all databases, not just default
    databases = set(connections)

    def _user(self, user_idx):  # lint-amnesty, pylint: disable=arguments-differ
        return self.users[user_idx].username

    def _block_type(self, block):  # pylint: disable=arguments-differ
        # We only record block state history in DjangoUserStateClient
        # when the block type is 'problem'
        return 'problem'

    def setUp(self):
        super().setUp()
        self.client = DjangoXBlockUserStateClient()
        self.users = defaultdict(UserFactory.create)

    def test_history_after_delete(self):
        """
        Changes made in the edx-platform repo broke this test in the edx-user-state-client repo.
        Getting the tests and code in sync is a three step process:
            1. Override the test here to make it a no-op and merge this code
            2. Update the test in the other repo to align with the new functionality
            3. Remove this override to re-enable the working test
        """

    def test_multiple_history_entries(self):
        """
        Changes made in the edx-platform repo broke this test in the edx-user-state-client repo.
        Getting the tests and code in sync is a three step process:
            1. Override the test here to make it a no-op and merge this code
            2. Update the test in the other repo to align with the new functionality
            3. Remove this override to re-enable the working test
        """


class TestDjangoUserStateClientQueryShape(ModuleStoreTestCase):
    """
    Tests for A2 get_many query shaping: single-query vs chunk fallback and field projection.
    """
    # Tell Django to clean out all databases, not just default
    databases = set(connections)

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.client = DjangoXBlockUserStateClient(user=self.user)
        self.course_key = CourseLocator('orgQ', 'courseQ', 'runQ')

    def _usage_key(self, index):
        return BlockUsageLocator(self.course_key, 'problem', f'block{index}')

    def test_get_many_accepts_keys_without_a_version_concept(self):
        """
        V2 library blocks reach this client too, and LibraryLocatorV2 has no
        for_version(): grouping must strip only what a key type actually supports.
        """
        library_key = LibraryLocatorV2(org='orgQ', slug='libQ')
        keys = [
            LibraryUsageLocatorV2(lib_key=library_key, block_type='problem', usage_id='libblock'),
            self._usage_key(0),
        ]

        # No state stored: this asserts the grouping does not raise, which is where
        # version stripping happens.
        assert not list(self.client.get_many(self.user.username, keys))

    def test_get_many_uses_single_query_under_threshold(self):
        """Moderate key counts should use one StudentModule query, not chunked_filter."""
        keys = [self._usage_key(i) for i in range(3)]
        for index, key in enumerate(keys):
            self.client.set(self.user.username, key, {'a': index})

        with self.assertNumQueries(1):
            results = list(self.client.get_many(self.user.username, keys))

        assert len(results) == 3
        by_key = {entry.block_key: entry.state for entry in results}
        for index, key in enumerate(keys):
            assert by_key[key] == {'a': index}

    def test_get_many_chunks_when_over_threshold(self):
        """Large key counts should fall back to multiple chunked queries."""
        threshold = 3
        keys = [self._usage_key(i) for i in range(threshold + 2)]
        for key in keys:
            self.client.set(self.user.username, key, {'v': 1})

        with self.settings(STUDENTMODULE_BULK_QUERY_THRESHOLD=threshold):
            # Two chunks: [0,1,2] and [3,4] → two SELECT queries
            with self.assertNumQueries(2):
                results = list(self.client.get_many(self.user.username, keys))

        assert len(results) == len(keys)

    def test_get_many_field_projection_parity(self):
        """Projected .only() query must still return full state and modified."""
        key = self._usage_key(0)
        self.client.set(self.user.username, key, {'answer': 42, 'attempts': 1})

        results = list(self.client.get_many(self.user.username, [key]))
        assert len(results) == 1
        assert results[0].state == {'answer': 42, 'attempts': 1}
        assert results[0].updated is not None
        assert results[0].block_key == key

    def test_get_many_fields_filter_still_works(self):
        """API fields= filter remains unchanged with the optimized read path."""
        key = self._usage_key(1)
        self.client.set(self.user.username, key, {'keep': True, 'drop': False})

        results = list(self.client.get_many(self.user.username, [key], fields=['keep']))
        assert len(results) == 1
        assert results[0].state == {'keep': True}

    def test_delete_many_still_persists_with_full_model_load(self):
        """
        delete_many must not use the read_only projection path, so saves remain safe.
        """
        key = self._usage_key(2)
        self.client.set(self.user.username, key, {'x': 1})
        self.client.delete_many(self.user.username, [key])
        assert not list(self.client.get_many(self.user.username, [key]))

    def test_get_many_uses_read_only_student_module_path(self):
        """get_many should request the read_only projection; delete_many should not."""
        key = self._usage_key(0)
        self.client.set(self.user.username, key, {'x': 1})

        with patch.object(
            self.client,
            '_student_module_query',
            wraps=self.client._student_module_query,  # pylint: disable=protected-access
        ) as wrapped:
            list(self.client.get_many(self.user.username, [key]))
            wrapped.assert_called()
            assert wrapped.call_args.kwargs.get('read_only') is True

            wrapped.reset_mock()
            self.client.delete_many(self.user.username, [key])
            wrapped.assert_called()
            assert not wrapped.call_args.kwargs.get('read_only', False)

    def test_student_module_query_only_projects_get_many_fields(self):
        """read_only querysets should SELECT the projected columns, not the full row."""
        keys = [self._usage_key(0)]
        read_qs = self.client._student_module_query(  # pylint: disable=protected-access
            self.user.username, self.course_key, keys, read_only=True,
        )
        write_qs = self.client._student_module_query(  # pylint: disable=protected-access
            self.user.username, self.course_key, keys, read_only=False,
        )
        read_sql = str(read_qs.query)
        write_sql = str(write_qs.query)
        # Projected columns (module_state_key is stored as module_id).
        assert 'state' in read_sql
        assert 'modified' in read_sql
        assert 'module_id' in read_sql
        assert 'course_id' in read_sql
        # Full-row path should still include columns omitted from the projection.
        assert 'grade' in write_sql
        assert 'module_type' in write_sql
        # read_only path should not pull unused score columns.
        assert 'grade' not in read_sql
        assert 'module_type' not in read_sql


class TestLazyUserState(TestCase):
    """Unit tests for LazyUserState and loads_user_state helpers."""

    def test_loads_user_state_parses_object(self):
        assert loads_user_state('{"a": 1, "b": "x"}') == {'a': 1, 'b': 'x'}

    def test_lazy_state_defers_parse_until_field_access(self):
        lazy = LazyUserState('{"answer": 42, "attempts": 2}')
        assert not lazy.is_parsed
        assert lazy['answer'] == 42
        assert lazy.is_parsed
        assert lazy['attempts'] == 2
        assert lazy == {'answer': 42, 'attempts': 2}

    def test_lazy_state_has_and_delete_force_parse(self):
        lazy = LazyUserState('{"keep": true, "drop": false}')
        assert 'keep' in lazy
        assert lazy.is_parsed
        del lazy['drop']
        assert lazy == {'keep': True}


class TestDjangoUserStateClientLazyParse(ModuleStoreTestCase):
    """Integration tests for lazy JSON parse on the Django get_many path."""
    databases = set(connections)

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.client = DjangoXBlockUserStateClient(user=self.user)
        self.course_key = CourseLocator('orgL', 'courseL', 'runL')

    def _usage_key(self, index):
        return BlockUsageLocator(self.course_key, 'problem', f'lazy{index}')

    def test_get_many_yields_lazy_state_until_read(self):
        key = self._usage_key(0)
        self.client.set(self.user.username, key, {'student_answers': {'1': 'A'}, 'score': 1})

        results = list(self.client.get_many(self.user.username, [key]))
        assert len(results) == 1
        state = results[0].state
        assert isinstance(state, LazyUserState)
        assert not state.is_parsed
        assert state['score'] == 1
        assert state.is_parsed
        assert state['student_answers'] == {'1': 'A'}

    def test_get_many_with_fields_parses_eagerly(self):
        key = self._usage_key(1)
        self.client.set(self.user.username, key, {'keep': 1, 'drop': 2})

        results = list(self.client.get_many(self.user.username, [key], fields=['keep']))
        assert len(results) == 1
        assert results[0].state == {'keep': 1}
        assert not isinstance(results[0].state, LazyUserState)

    def test_get_many_skips_empty_deleted_state_without_lazy_wrap(self):
        """Deleted state should yield no rows and must not wrap as LazyUserState."""
        key = self._usage_key(2)
        self.client.set(self.user.username, key, {'x': 1})
        self.client.delete_many(self.user.username, [key])

        assert not list(self.client.get_many(self.user.username, [key]))
