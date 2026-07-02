# pylint: disable=no-member, missing-docstring


import logging
from unittest import TestCase
from pytest import mark

from celery import shared_task
from django.test.utils import override_settings
from edx_django_utils.cache import RequestCache


@mark.django_db
class TestClearRequestCache(TestCase):
    """
    Tests _clear_request_cache is called after celery task is run.
    """
    def _get_cache(self):
        return RequestCache("TestClearRequestCache")

    @shared_task
    def _dummy_task(self):
        """ A task that adds stuff to the request cache. """
        self._get_cache().set("cache_key", "blah blah")

    @override_settings(CLEAR_REQUEST_CACHE_ON_TASK_COMPLETION=True)
    def test_clear_cache_celery(self):
        self._dummy_task.apply(args=(self,)).get()
        assert not self._get_cache().get_cached_response('cache_key').is_found


class TestSuppressToggleNoRequestWarningInTasks(TestCase):
    """
    Tests that the "accessed without a request" toggle warning is dropped only
    when logged from inside a celery task, and only that specific message -
    every other message from the same logger, and this message when logged
    outside of a task, must still go through.
    """
    logger = logging.getLogger('edx_toggles.toggles.internal.waffle.flag')

    # shared_task derives its registered name from the plain function name
    # only, not the enclosing class - an unqualified name here would collide
    # with TestClearRequestCache._dummy_task above, since both would register
    # as "_dummy_task" in celery's global task registry.
    @shared_task(name='test_signals.suppress_toggle_warning_dummy_task')
    def _dummy_task(self):
        """ A task that logs the same warning edx_toggles would log with no request bound. """
        self.logger.warning("Flag 'some.flag' accessed without a request, which is likely in the context of a celery task.")  # pylint: disable=line-too-long
        self.logger.warning("some other warning text")

    def test_warning_dropped_inside_task(self):
        with self.assertLogs(self.logger, level='WARNING') as captured:
            self._dummy_task.apply(args=(self,)).get()
            # The matching warning is dropped; an unrelated message from the
            # same logger still goes through untouched.
            self.logger.warning("marker so assertLogs has something to capture")
        messages = [record.getMessage() for record in captured.records]
        assert "some other warning text" in messages
        assert not any("accessed without a request" in message for message in messages)

    def test_warning_not_dropped_outside_task(self):
        with self.assertLogs(self.logger, level='WARNING') as captured:
            self.logger.warning(
                "Flag 'some.flag' accessed without a request, which is likely in the context of a celery task."
            )
        messages = [record.getMessage() for record in captured.records]
        assert any("accessed without a request" in message for message in messages)
