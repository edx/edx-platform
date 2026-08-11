"""
Tests for methods defined in builtin_assets.py
"""
from unittest import TestCase
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from web_fragments.fragment import Fragment, FragmentResource

from xmodule.util import builtin_assets


class AddCssToFragmentTests(TestCase):
    """
    Tests for add_css_to_fragment.
    """

    def test_absolute_path_raises_value_error(self):
        fragment = Fragment()
        with self.assertRaises(ValueError):
            builtin_assets.add_css_to_fragment(
                fragment,
                "/openedx/edx-platform/xmodule/assets/VideoBlockEditor.css",
            )

    def test_not_css_raises_value_error(self):
        fragment = Fragment()
        with self.assertRaises(ValueError):
            builtin_assets.add_css_to_fragment(
                fragment,
                "vertical/public/js/vertical_student_view.js"
            )

    def test_misspelled_path_raises_not_found(self):
        fragment = Fragment()
        with self.assertRaises(FileNotFoundError):
            builtin_assets.add_css_to_fragment(
                fragment,
                "VideoBlockEditorrrrr.css",
            )

    def test_static_file_missing_raises_improperly_configured(self):
        fragment = Fragment()
        with patch.object(builtin_assets, 'get_static_file_url', lambda _path: None):
            with self.assertRaises(ImproperlyConfigured):
                builtin_assets.add_css_to_fragment(
                    fragment,
                    "VideoBlockEditor.css",
                )

    def test_happy_path(self):
        fragment = Fragment()
        builtin_assets.add_css_to_fragment(fragment, "VideoBlockEditor.css")
        assert fragment.resources[0] == FragmentResource(
            kind='url',
            data='/static/css-builtin-blocks/VideoBlockEditor.css',
            mimetype='text/css',
            placement='head',
        )


class AddWebpackJsToFragmentTests(TestCase):
    """
    Tests for add_webpack_js_to_fragment.
    """

    def _add_bundle(self, chunks):
        """
        Call add_webpack_js_to_fragment with get_files stubbed to return ``chunks``.
        """
        fragment = Fragment()
        with patch('webpack_loader.utils.get_files', return_value=chunks):
            builtin_assets.add_webpack_js_to_fragment(fragment, 'XModuleShim')
        return fragment

    def test_chunk_named_by_file(self):
        """django-webpack-loader 1.x reports the asset's file name."""
        fragment = self._add_bundle([{'name': 'XModuleShim.js', 'url': '/static/bundles/XModuleShim.js'}])
        assert fragment.resources == [FragmentResource(
            kind='url',
            data='/static/bundles/XModuleShim.js',
            mimetype='application/javascript',
            placement='foot',
        )]

    def test_chunk_named_by_key(self):
        """django-webpack-loader 2.x/3.x reports the chunk key, which has no extension."""
        fragment = self._add_bundle([{'name': 'XModuleShim', 'url': '/static/bundles/XModuleShim.js'}])
        assert fragment.resources == [FragmentResource(
            kind='url',
            data='/static/bundles/XModuleShim.js',
            mimetype='application/javascript',
            placement='foot',
        )]

    def test_non_js_chunks_are_skipped(self):
        fragment = self._add_bundle([
            {'name': 'XModuleShim', 'url': '/static/bundles/XModuleShim.css'},
            {'name': 'XModuleShim', 'url': '/static/bundles/XModuleShim.js.map'},
        ])
        assert fragment.resources == []
