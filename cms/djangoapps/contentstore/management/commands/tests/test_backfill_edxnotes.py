"""
Tests for the `backfill_edxnotes` Studio (cms) management command.
"""
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.tabs import CourseTab, CourseTabList

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

LOGGER = 'cms.djangoapps.contentstore.management.commands.backfill_edxnotes.logger'


@override_settings(ENABLE_EDXNOTES=True)
class BackfillEdxNotesTest(ModuleStoreTestCase):
    """
    Test `backfill_edxnotes`.
    """

    def _make_untouched_course(self, **kwargs):
        """
        A course where the Notes setting has never been saved and there is no Notes tab,
        like an old course created before Notes was on by default.
        """
        course = CourseFactory(**kwargs)
        if course.fields['edxnotes'].is_set_on(course):
            del course.edxnotes
        course.tabs = [tab for tab in course.tabs if tab.type != 'edxnotes']
        self.update_course(course, ModuleStoreEnum.UserID.test)
        course = self.store.get_course(course.id)
        assert not course.fields['edxnotes'].is_set_on(course)
        assert not self._has_notes_tab(course)
        return course

    def _make_manual_course(self, enabled, **kwargs):
        """
        A course where Notes was explicitly turned on or off, like the Pages & Resources toggle does.
        """
        course = CourseFactory(**kwargs)
        course.edxnotes = enabled
        course.tabs = [tab for tab in course.tabs if tab.type != 'edxnotes']
        if enabled:
            course.tabs.append(CourseTab.load('edxnotes'))
        self.update_course(course, ModuleStoreEnum.UserID.test)
        course = self.store.get_course(course.id)
        assert course.fields['edxnotes'].is_set_on(course)
        return course

    @staticmethod
    def _has_notes_tab(course):
        return CourseTabList.get_tab_by_id(course.tabs, 'edxnotes') is not None

    def _reload(self, course):
        return self.store.get_course(course.id)

    def test_untouched_course_gets_notes_on(self):
        course = self._make_untouched_course()

        call_command('backfill_edxnotes')

        course = self._reload(course)
        assert course.edxnotes is True
        assert course.fields['edxnotes'].is_set_on(course)
        assert self._has_notes_tab(course)
        assert CourseOverview.get_from_id(course.id).edxnotes is True

    def test_manually_disabled_course_is_unchanged(self):
        course = self._make_manual_course(enabled=False)
        tabs_before = [tab.type for tab in course.tabs]

        call_command('backfill_edxnotes')

        course = self._reload(course)
        assert course.edxnotes is False
        assert not self._has_notes_tab(course)
        assert [tab.type for tab in course.tabs] == tabs_before

    def test_manually_enabled_course_is_unchanged(self):
        course = self._make_manual_course(enabled=True)
        tabs_before = [tab.type for tab in course.tabs]

        with mock.patch.object(self.store, 'update_item', wraps=self.store.update_item) as update_item:
            call_command('backfill_edxnotes')
            update_item.assert_not_called()

        course = self._reload(course)
        assert course.edxnotes is True
        assert [tab.type for tab in course.tabs] == tabs_before

    def test_existing_notes_tab_is_not_duplicated(self):
        course = self._make_untouched_course()
        course.tabs.append(CourseTab.load('edxnotes'))
        self.update_course(course, ModuleStoreEnum.UserID.test)

        call_command('backfill_edxnotes')

        course = self._reload(course)
        assert [tab.type for tab in course.tabs].count('edxnotes') == 1
        assert course.fields['edxnotes'].is_set_on(course)

    def test_mixed_courses(self):
        untouched = self._make_untouched_course()
        manual_off = self._make_manual_course(enabled=False)
        manual_on = self._make_manual_course(enabled=True)

        call_command('backfill_edxnotes')

        assert self._reload(untouched).edxnotes is True
        assert self._has_notes_tab(self._reload(untouched))
        assert self._reload(manual_off).edxnotes is False
        assert not self._has_notes_tab(self._reload(manual_off))
        assert self._reload(manual_on).edxnotes is True

    def test_dry_run_changes_nothing(self):
        course = self._make_untouched_course()

        with mock.patch(LOGGER) as mock_logger:
            call_command('backfill_edxnotes', '--dry-run')

        course = self._reload(course)
        assert not course.fields['edxnotes'].is_set_on(course)
        assert not self._has_notes_tab(course)
        mock_logger.info.assert_any_call(f'[DRY RUN] backfill_edxnotes: would turn on Notes for {course.id}.')

    def test_second_run_changes_nothing(self):
        course = self._make_untouched_course()
        call_command('backfill_edxnotes')
        tabs_after_first_run = [tab.type for tab in self._reload(course).tabs]

        with mock.patch.object(self.store, 'update_item', wraps=self.store.update_item) as update_item:
            call_command('backfill_edxnotes')
            update_item.assert_not_called()

        assert [tab.type for tab in self._reload(course).tabs] == tabs_after_first_run

    def test_org_filter(self):
        in_org = self._make_untouched_course(org='NotesOrg')
        other_org = self._make_untouched_course(org='OtherOrg')

        call_command('backfill_edxnotes', '--org', 'notesorg')

        assert self._has_notes_tab(self._reload(in_org))
        other_org = self._reload(other_org)
        assert not other_org.fields['edxnotes'].is_set_on(other_org)

    def test_course_key_filter(self):
        target = self._make_untouched_course()
        other = self._make_untouched_course()

        call_command('backfill_edxnotes', '--course-key', str(target.id))

        assert self._has_notes_tab(self._reload(target))
        other = self._reload(other)
        assert not other.fields['edxnotes'].is_set_on(other)

    def test_batching(self):
        courses = sorted((self._make_untouched_course() for _ in range(3)), key=lambda c: str(c.id))

        call_command('backfill_edxnotes', '--start-index', '1', '--count', '1')

        updated = [self._reload(c).fields['edxnotes'].is_set_on(self._reload(c)) for c in courses]
        assert updated == [False, True, False]
