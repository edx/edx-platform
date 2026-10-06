"""
Signal handler for exceptions.
"""
# pylint: disable=unused-argument


import logging

from celery import current_task
from celery.signals import task_postrun
from django.conf import settings
from django.core.signals import got_request_exception
from django.dispatch import receiver
from edx_django_utils.cache import RequestCache


class _SuppressToggleNoRequestWarningInTasks(logging.Filter):
    """
    edx_toggles logs a WARNING whenever a CourseWaffleFlag/WaffleFlag is checked
    with no request bound (see
    edx_toggles.toggles.internal.waffle.flag._get_flag_active_no_request). That's
    expected and harmless inside a Celery task, since tasks are invoked off a
    queue and never have an HTTP request attached.

    This drops only that specific message, and only when `celery.current_task`
    confirms we're actually executing inside a task right now - every other
    message from this logger, and this same message when it fires outside of a
    task (a genuine signal something is wrong), is left untouched.

    Implemented as a per-record filter, evaluated fresh on every log call,
    rather than toggling the logger's level around task_prerun/task_postrun:
    that approach relied on shared module state, which isn't safe across
    concurrently running tasks under non-prefork execution pools (e.g.
    eventlet/gevent/threads), and would have hidden every message from this
    logger - not just this one - for the duration of any task.
    """

    MESSAGE_FRAGMENT = "accessed without a request"

    def filter(self, record):
        # `current_task` is a celery Proxy, not the actual task object - it is
        # never literally `None` even when unbound, so check its truthiness
        # rather than `is not None`.
        if self.MESSAGE_FRAGMENT in record.getMessage() and current_task:
            return False
        return True


logging.getLogger('edx_toggles.toggles.internal.waffle.flag').addFilter(
    _SuppressToggleNoRequestWarningInTasks()
)


@receiver(got_request_exception)
def record_request_exception(sender, **kwargs):
    """
    Logs the stack trace whenever an exception
    occurs in processing a request.
    """
    logging.exception("Uncaught exception from {sender}".format(
        sender=sender
    ))


@task_postrun.connect
def _clear_request_cache(**kwargs):
    """
    Once a celery task completes, clear the request cache to
    prevent memory leaks.
    """
    if getattr(settings, 'CLEAR_REQUEST_CACHE_ON_TASK_COMPLETION', True):
        RequestCache.clear_all_namespaces()
