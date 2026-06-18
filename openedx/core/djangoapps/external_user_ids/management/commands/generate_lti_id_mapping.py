"""
Generate a CSV mapping LTI 1.3 UUIDs to LTI 1.1 identifiers.

This command helps a partner migrate per-user data from LTI 1.1 to LTI 1.3.
By default it exports the anonymous value sent in the LTI 1.1 ``user_id``
parameter. Use ``--include-username`` to also export the Open edX username
that was eligible to be sent as the optional LTI 1.1
``lis_person_sourcedid`` parameter.

Username output contains PII and must be written to a file.

Usage:
    ./manage.py lms generate_lti_id_mapping \\
        course-v1:BerkeleyX+Data88.1EX+3T2025 \\
        course-v1:BerkeleyX+Data88.2EX+3T2025 \\
        course-v1:BerkeleyX+Data88.3EX+3T2025 \\
        --output berkeley_lti_mapping.csv

    ./manage.py lms generate_lti_id_mapping \\
        course-v1:BerkeleyX+Data88.1EX+3T2025 \\
        course-v1:BerkeleyX+Data88.2EX+3T2025 \\
        course-v1:BerkeleyX+Data88.3EX+3T2025 \\
        --include-username \\
        --output berkeley_lti_username_mapping.csv

Output columns:
    lti_13_uuid      - UUID sent to LTI 1.3 tools
    course           - Course key
    lti_11_hash      - Anonymous value sent as the LTI 1.1 user_id
    lti_11_username  - Optional PII column containing auth_user.username
"""

import csv
import os
import textwrap
from contextlib import nullcontext

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import AnonymousUserId
from openedx.core.djangoapps.external_user_ids.models import ExternalIdType


class Command(BaseCommand):
    """
    Export existing LTI 1.3 and LTI 1.1 identifiers for the given courses.

    Only users who already have both an LTI ExternalId and a course-specific
    AnonymousUserId appear in the output. In rare cases multiple anonymous IDs
    may exist for a user/course pair. The command uses the highest record ID,
    matching ``anonymous_id_for_user()``.
    """

    help = textwrap.dedent(__doc__)
    USERNAME_OUTPUT_REQUIRES_FILE = (
        '--output is required when --include-username is used because the CSV contains PII.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'course_keys',
            type=CourseKey.from_string,
            nargs='+',
            help='One or more course keys to include in the mapping.',
        )
        parser.add_argument(
            '--output',
            type=str,
            default=None,
            help='Path to write the CSV file. Defaults to stdout.',
        )
        parser.add_argument(
            '--include-username',
            action='store_true',
            help=(
                'Include auth_user.username as lti_11_username. This value is PII '
                'and requires --output.'
            ),
        )

    @staticmethod
    def _open_pii_output(output_path):
        """
        Exclusively create a PII output file with owner-only permissions.
        """
        try:
            file_descriptor = os.open(
                output_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except OSError as exc:
            raise CommandError(f'Unable to create output file {output_path}: {exc}') from exc

        try:
            return os.fdopen(file_descriptor, 'w', newline='', encoding='utf-8')
        except BaseException:
            os.close(file_descriptor)
            try:
                os.remove(output_path)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _get_rows(course_keys, include_username):
        """
        Return a streaming queryset for the requested mapping rows.
        """
        latest_ids = (
            AnonymousUserId.objects.filter(course_id__in=course_keys)
            .values('user_id', 'course_id')
            .annotate(latest_id=Max('id'))
            .values_list('latest_id', flat=True)
        )

        selected_fields = [
            'user__externalid__external_user_id',
            'course_id',
            'anonymous_user_id',
        ]
        if include_username:
            selected_fields.append('user__username')

        return (
            AnonymousUserId.objects.filter(
                id__in=latest_ids,
                user__externalid__external_id_type__name=ExternalIdType.LTI,
            )
            .values(*selected_fields)
            .order_by(
                'user__externalid__external_user_id',
                'course_id',
            )
            .iterator(chunk_size=1000)
        )

    def handle(self, *args, **options):
        course_keys = options['course_keys']
        output_path = options['output']
        include_username = options['include_username']

        if include_username and not output_path:
            raise CommandError(self.USERNAME_OUTPUT_REQUIRES_FILE)

        pii_output_created = False
        if output_path and include_username:
            output_context = self._open_pii_output(output_path)
            pii_output_created = True
        elif output_path:
            output_context = open(  # pylint: disable=consider-using-with
                output_path,
                'w',
                newline='',
                encoding='utf-8',
            )
        else:
            output_context = nullcontext(self.stdout)

        try:
            with output_context as output_file:
                writer = csv.writer(output_file)
                header = ['lti_13_uuid', 'course', 'lti_11_hash']
                if include_username:
                    header.append('lti_11_username')
                writer.writerow(header)

                row_count = 0
                for row in self._get_rows(course_keys, include_username):
                    output_row = [
                        str(row['user__externalid__external_user_id']),
                        str(row['course_id']),
                        row['anonymous_user_id'],
                    ]
                    if include_username:
                        output_row.append(row['user__username'])
                    writer.writerow(output_row)
                    row_count += 1

                if include_username:
                    output_file.flush()
                    os.fsync(output_file.fileno())
        except BaseException:
            if pii_output_created:
                try:
                    os.remove(output_path)
                except FileNotFoundError:
                    pass
            raise

        if include_username:
            message = f'Done. {row_count} rows written to {output_path}.'
        else:
            message = f'Done. {row_count} rows written.'
        self.stderr.write(self.style.SUCCESS(message))
