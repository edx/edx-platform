"""
Management command to generate a CSV mapping LTI 1.3 UUIDs to LTI 1.1 user hashes
for a given set of courses.

This is useful when a partner is migrating from LTI 1.1 to LTI 1.3 and needs to
match their existing per-user data (keyed by the LTI 1.1 hash) to the new LTI 1.3
UUID that edX will send after the switch.

Use ``--include-user-id`` to also export the Open edX user ID that was eligible
to be sent as the optional LTI 1.1 ``lis_person_sourcedid`` parameter. User ID
output contains PII and must be written to a file.

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
        --include-user-id \\
        --output berkeley_lti_user_id_mapping.csv

Output columns:
    lti_13_uuid  - The UUID sent to LTI 1.3 tools (from the ExternalId table)
    course       - The course key
    lti_11_hash  - The anonymous user ID sent to LTI 1.1 tools (from AnonymousUserId)
    lti_11_user_id - Optional PII column containing auth_user.username
"""

import csv
import textwrap

from django.core.management import BaseCommand
from django.core.management.base import CommandError
from django.db.models import Max
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import AnonymousUserId
from openedx.core.djangoapps.external_user_ids.models import ExternalIdType


class Command(BaseCommand):
    """
    Export a CSV mapping LTI 1.3 UUIDs to LTI 1.1 user hashes for the given courses.

    Only users who have both identifiers already generated will appear in the output.
    LTI 1.3 UUIDs are created on first LTI 1.3 launch; LTI 1.1 hashes are created
    on first LTI 1.1 launch for each course. In rare cases multiple hashes may exist
    per (user, course) due to historical SECRET_KEY rotation — this command always
    uses the most recently created hash (highest record ID), consistent with the
    behaviour of anonymous_id_for_user().

    Examples:

        ./manage.py lms generate_lti_id_mapping course-v1:BerkeleyX+Data88.1EX+3T2025
        ./manage.py lms generate_lti_id_mapping course-v1:BerkeleyX+Data88.1EX+3T2025 --output mapping.csv

    """

    help = textwrap.dedent(__doc__)

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
            '--include-user-id',
            action='store_true',
            dest='include_user_id',
            help=(
                'Include auth_user.username as lti_11_user_id. This value is PII '
                'and requires --output.'
            ),
        )

    def handle(self, *args, **options):
        course_keys = options['course_keys']
        output_path = options['output']
        include_user_id = options['include_user_id']

        if include_user_id and not output_path:
            raise CommandError(
                '--output is required when --include-user-id is used because the CSV contains PII.'
            )

        output_file = self.stdout
        if output_path:
            output_file = open(output_path, 'w', newline='', encoding='utf-8')  # pylint: disable=consider-using-with

        try:
            writer = csv.writer(output_file)
            header = ['lti_13_uuid', 'course', 'lti_11_hash']
            if include_user_id:
                header.append('lti_11_user_id')
            writer.writerow(header)

            # Single JOIN query at the DB level — no loops, no N+1.
            # Streams results in chunks of 1000 to keep memory flat.
            #
            # User ID is selected only when explicitly requested. No email,
            # name, or internal numeric user ID is selected or written to the output.
            #
            # De-duplicate: in rare cases a user may have multiple
            # AnonymousUserId rows per (user, course) due to historical
            # SECRET_KEY rotation. We pick the most recently created one
            # (highest id) per (user_id, course_id) group — consistent with
            # anonymous_id_for_user(). Using Max('id') + subquery instead of
            # DISTINCT ON because DISTINCT ON is PostgreSQL-only and edX runs
            # MySQL in production.
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
            if include_user_id:
                selected_fields.append('user__username')

            rows = (
                AnonymousUserId.objects.filter(
                    id__in=latest_ids,
                    user__externalid__external_id_type__name=ExternalIdType.LTI,
                )
                .values(*selected_fields)
                .iterator(chunk_size=1000)
            )

            row_count = 0
            for row in rows:
                output_row = [
                    str(row['user__externalid__external_user_id']),
                    str(row['course_id']),
                    row['anonymous_user_id'],
                ]
                if include_user_id:
                    output_row.append(row['user__username'])
                writer.writerow(output_row)
                row_count += 1

            self.stderr.write(self.style.SUCCESS(f'Done. {row_count} rows written.'))
        finally:
            if output_path:
                output_file.close()
