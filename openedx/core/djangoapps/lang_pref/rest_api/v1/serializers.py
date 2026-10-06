"""
Serializers for the language preference v1 API.
"""

from rest_framework import serializers


class SiteLanguageSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for a language that a user may select as their site language.
    """
    code = serializers.CharField(
        help_text='The language code, for example "es-419".',
    )
    name = serializers.CharField(
        help_text='The name of the language, written in that language.',
    )
    released = serializers.BooleanField(
        help_text='False if the language is only beta released.',
    )
