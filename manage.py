#!/usr/bin/env python
"""
Usage: manage.py {lms|cms} [--settings env] ...

Run django management commands. Because edx-platform contains multiple django projects,
the first argument specifies which project to run (cms [Studio] or lms [Learning Management System]).

By default, those systems run in with a settings file appropriate for development. However,
by passing the --settings flag, you can specify what environment specific settings file to use.

Any arguments not understood by this manage.py will be passed to django-admin.py
"""
# pylint: disable=wrong-import-order, wrong-import-position

from openedx.core.lib.logsettings import log_python_warnings
log_python_warnings()

# Patch the xml libs before anything else.
from openedx.core.lib.safe_lxml import defuse_xml_libs  # isort:skip
defuse_xml_libs()

import os
import sys
from argparse import ArgumentParser
from contextlib import nullcontext

from openedx_filters.tooling import OpenEdxPublicFilter


class ManagementCommandContextmanagerRequested(OpenEdxPublicFilter):
    """
    Filter triggered before a management command is executed.

    Pipeline steps may provide a context manager to wrap command execution.
    """

    filter_type = 'org.openedx.platform.management.command.contextmanager.requested.v1'

    @classmethod
    def run_filter(cls, command_contextmanager, command_name, service_variant):
        """
        Run the management command context manager pipeline.
        """
        pipeline_output = cls.run_pipeline(
            command_contextmanager=command_contextmanager,
            command_name=command_name,
            service_variant=service_variant,
        )

        if isinstance(pipeline_output, dict):
            contextmanager_result = pipeline_output.get('command_contextmanager', command_contextmanager)
            if hasattr(contextmanager_result, '__enter__') and hasattr(contextmanager_result, '__exit__'):
                return contextmanager_result

        return command_contextmanager


def parse_args():
    """
    Parse edx specific arguments to manage.py
    """
    parser = ArgumentParser()
    subparsers = parser.add_subparsers(title='system', description='edX service to run')

    lms = subparsers.add_parser(
        'lms',
        help='Learning Management System',
        add_help=False,
        usage='%(prog)s [options] ...'
    )
    lms.add_argument('-h', '--help', action='store_true', help='show this help message and exit')
    lms.add_argument(
        '--settings',
        help="Which django settings module to use under lms.envs. If not provided, the DJANGO_SETTINGS_MODULE "
             "environment variable will be used if it is set, otherwise it will default to lms.envs.devstack")
    lms.add_argument(
        '--service-variant',
        choices=['lms', 'lms-xml', 'lms-preview'],
        default='lms',
        help='Which service variant to run, when using the production environment')
    lms.set_defaults(
        help_string=lms.format_help(),
        settings_base='lms/envs',
        default_settings='lms.envs.devstack',
    )

    cms = subparsers.add_parser(
        'cms',
        help='Studio',
        add_help=False,
        usage='%(prog)s [options] ...'
    )
    cms.add_argument(
        '--settings',
        help="Which django settings module to use under cms.envs. If not provided, the DJANGO_SETTINGS_MODULE "
             "environment variable will be used if it is set, otherwise it will default to cms.envs.devstack")
    cms.add_argument('-h', '--help', action='store_true', help='show this help message and exit')
    cms.set_defaults(
        help_string=cms.format_help(),
        settings_base='cms/envs',
        default_settings='cms.envs.devstack',
        service_variant='cms',
    )

    edx_args, django_args = parser.parse_known_args()

    if edx_args.help:
        print("edX:")
        print(edx_args.help_string)

    return edx_args, django_args


if __name__ == "__main__":
    edx_args, django_args = parse_args()

    edx_args_base = edx_args.settings_base.replace('/', '.') + '.'
    if edx_args.settings:
        os.environ["DJANGO_SETTINGS_MODULE"] = edx_args_base + edx_args.settings
    elif os.environ.get("EDX_PLATFORM_SETTINGS") and not os.environ.get("DJANGO_SETTINGS_MODULE"):
        os.environ["DJANGO_SETTINGS_MODULE"] = edx_args_base + os.environ["EDX_PLATFORM_SETTINGS"]

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", edx_args.default_settings)
    os.environ.setdefault("SERVICE_VARIANT", edx_args.service_variant)

    if edx_args.help:
        print("Django:")
        # This will trigger django-admin.py to print out its help
        django_args.append('--help')

    from django.core.management import execute_from_command_line

    # django_args contains only the args that argparse did not consume.
    # We treat the first non-option token as the Django command name.
    # Example: django_args=['--verbosity', '2', 'migrate', '--noinput'] -> 'migrate'.
    # If there is no non-option token (for example, django_args=['--help']),
    # default to 'help' because Django will print command help in that case.
    command_name = next((arg for arg in django_args if not arg.startswith('-')), 'help')

    command_contextmanager = ManagementCommandContextmanagerRequested.run_filter(
        command_contextmanager=nullcontext(),
        command_name=command_name,
        service_variant=os.environ.get("SERVICE_VARIANT", edx_args.service_variant),
    )

    with command_contextmanager:
        execute_from_command_line([sys.argv[0]] + django_args)
