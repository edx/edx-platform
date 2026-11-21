"""
Management command to turn on edX Notes for existing courses that never configured it.

Courses where the Notes setting was explicitly saved (on or off, from Pages & Resources,
Advanced Settings, an OLX import or a rerun) are left exactly as they are. Only courses
where ``edxnotes`` has never been set get ``edxnotes = True`` saved explicitly and the
Notes tab added, which is what turning Notes on in Pages & Resources does
(see ``EdxNotesCourseApp.set_enabled``).

Running the command a second time changes nothing, because the first run saves the value
explicitly and those courses are skipped from then on.

This should be invoked from the Studio process:

    python manage.py cms backfill_edxnotes --dry-run
    python manage.py cms backfill_edxnotes --org edX --org HarvardX
    python manage.py cms backfill_edxnotes --course-key course-v1:edX+DemoX+Demo_Course
    python manage.py cms backfill_edxnotes --start-index 0 --count 500

Note: errors on single courses are logged and do not stop the run. Search the logs for
"backfill_edxnotes" to see the summary.
"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.tabs import CourseTab, CourseTabList

logger = logging.getLogger(__name__)

NOTES_TAB_TYPE = 'edxnotes'


class Command(BaseCommand):
    """
    Turn on edX Notes (setting + tab) for courses that never set ``edxnotes``.
    """
    help = (
        'Turn on edX Notes for existing courses where the Notes setting has never been set. '
        'Courses where Notes was explicitly turned on or off are left unchanged.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Log what would change without saving anything.',
        )
        parser.add_argument(
            '--org',
            action='append',
            dest='orgs',
            default=[],
            help='Only process courses in this org. Can be given more than once.',
        )
        parser.add_argument(
            '--course-key',
            action='append',
            dest='course_keys',
            default=[],
            help='Only process this course. Can be given more than once.',
        )
        parser.add_argument(
            '--start-index',
            type=int,
            default=0,
            help='Index (in the sorted list of matching courses) to start from, for batching.',
        )
        parser.add_argument(
            '--count',
            type=int,
            default=0,
            help='Number of courses to process from --start-index. 0 means all remaining.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        prefix = '[DRY RUN] ' if dry_run else ''

        if not settings.FEATURES.get('ENABLE_EDXNOTES', False):
            logger.warning(
                'backfill_edxnotes: ENABLE_EDXNOTES is off on this server, so learners will not see Notes '
                'even in courses this command updates.'
            )

        course_keys = self._get_course_keys(options)
        logger.info(f'{prefix}backfill_edxnotes: processing {len(course_keys)} courses.')

        store = modulestore()
        updated, skipped, errored = [], [], []
        for course_key in course_keys:
            try:
                course = store.get_course(course_key, depth=0)
                if course is None:
                    logger.warning(f'{prefix}backfill_edxnotes: {course_key} not found, skipping.')
                    errored.append(course_key)
                    continue

                if course.fields['edxnotes'].is_set_on(course):
                    # Someone (or an import/rerun) chose a value for this course: respect it.
                    logger.info(
                        f'{prefix}backfill_edxnotes: skipping {course_key}, Notes explicitly set to {course.edxnotes}.'
                    )
                    skipped.append(course_key)
                    continue

                # Don't read course.edxnotes before this line. XBlock ignores an assignment
                # that equals the cached value, and the default is True, so reading it first
                # would mean nothing gets saved and the course stays "never set".
                course.edxnotes = True
                if CourseTabList.get_tab_by_id(course.tabs, NOTES_TAB_TYPE) is None:
                    course.tabs.append(CourseTab.load(NOTES_TAB_TYPE))

                if dry_run:
                    logger.info(f'{prefix}backfill_edxnotes: would turn on Notes for {course_key}.')
                else:
                    # Publishing the course also refreshes its CourseOverview.
                    store.update_item(course, ModuleStoreEnum.UserID.mgmt_command)
                    logger.info(f'backfill_edxnotes: turned on Notes for {course_key}.')
                updated.append(course_key)
            except Exception:  # pylint: disable=broad-except
                logger.exception(f'{prefix}backfill_edxnotes: error while processing {course_key}.')
                errored.append(course_key)

        logger.info(
            f'{prefix}backfill_edxnotes: done. '
            f'{"would update" if dry_run else "updated"}={len(updated)} '
            f'skipped_explicitly_set={len(skipped)} errors={len(errored)}'
        )
        if errored:
            error_list = '\n'.join(f' - {key}' for key in errored)
            logger.error(f'{prefix}backfill_edxnotes: these courses had errors and were not updated:\n{error_list}')

    def _get_course_keys(self, options):
        """
        Return the sorted list of course keys to process, after filters and batching.
        """
        if options['course_keys']:
            try:
                course_keys = [CourseKey.from_string(key) for key in options['course_keys']]
            except InvalidKeyError as err:
                raise CommandError(f'Invalid course key: {err}') from err
        else:
            course_keys = [summary.id for summary in modulestore().get_course_summaries()]

        if options['orgs']:
            orgs = {org.lower() for org in options['orgs']}
            course_keys = [key for key in course_keys if key.org.lower() in orgs]

        course_keys = sorted(course_keys, key=str)

        start = max(options['start_index'], 0)
        count = options['count']
        end = start + count if count > 0 else len(course_keys)
        return course_keys[start:end]
