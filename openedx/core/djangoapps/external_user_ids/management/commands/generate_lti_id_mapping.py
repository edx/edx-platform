"""
Management command to generate a CSV mapping LTI 1.3 UUIDs to LTI 1.1 user hashes
for a given set of courses.

This is useful when a partner is migrating from LTI 1.1 to LTI 1.3 and needs to
match their existing per-user data (keyed by the LTI 1.1 hash) to the new LTI 1.3
UUID that edX will send after the switch.

Usage:
    ./manage.py lms generate_lti_id_mapping \\
        course-v1:BerkeleyX+Data88.1EX+3T2025 \\
        course-v1:BerkeleyX+Data88.2EX+3T2025 \\
        course-v1:BerkeleyX+Data88.3EX+3T2025 \\
        --output berkeley_lti_mapping.csv

Output columns:
    lti_13_uuid  - The UUID sent to LTI 1.3 tools (from the ExternalId table)
    course       - The course key
    lti_11_hash  - The anonymous user ID sent to LTI 1.1 tools (from AnonymousUserId)
"""

import csv
import textwrap

from django.core.management import BaseCommand
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

    def handle(self, *args, **options):
        course_keys = options['course_keys']
        output_path = options['output']

        output_file = self.stdout
        if output_path:
            output_file = open(output_path, 'w', newline='', encoding='utf-8')  # pylint: disable=consider-using-with

        try:
            writer = csv.writer(output_file)
            writer.writerow(['lti_13_uuid', 'course', 'lti_11_hash'])

            # Single JOIN query at the DB level — no loops, no N+1.
            # Streams results in chunks of 1000 to keep memory flat.
            #
            # PII note: .values() explicitly restricts the fetched fields to
            # only anonymous identifiers (UUID and hash) and the course key.
            # No username, email, name, or internal user ID is selected or
            # written to the output.
            # Use ExternalIdType.LTI constant instead of a hardcoded string
            # to avoid drift if the underlying type name ever changes.
            # Order by -id so that in the rare case of multiple hashes per
            # (user, course) — caused by historical SECRET_KEY rotation —
            # we consistently pick the most recently created one, matching
            # the behaviour of anonymous_id_for_user().
            rows = AnonymousUserId.objects.filter(
                course_id__in=course_keys,
                user__externalid__external_id_type__name=ExternalIdType.LTI,
            ).values(
                'user__externalid__external_user_id',
                'course_id',
                'anonymous_user_id',
            ).order_by(
                'user__externalid__external_user_id',
                'course_id',
                '-id',
            ).distinct(
                'user__externalid__external_user_id',
                'course_id',
            ).iterator(chunk_size=1000)

            row_count = 0
            for row in rows:
                writer.writerow([
                    str(row['user__externalid__external_user_id']),
                    str(row['course_id']),
                    row['anonymous_user_id'],
                ])
                row_count += 1

            self.stderr.write(self.style.SUCCESS(f'Done. {row_count} rows written.'))
        finally:
            if output_path:
                output_file.close()
