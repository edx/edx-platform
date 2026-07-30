/*
 * jQuery plugin that waits until all elements are loaded before executing the specified function.
 *
 * Adapted from http://stackoverflow.com/a/35777807/592820.
 *
 * Example:
 *
 *  $('iframe').allLoaded(function () {
 *      window.alert('All iframes loaded!');
 *  });
 *
 */

;(function ($) {
    'use strict';

    $.fn.extend({
        allLoaded: function (fn) {
            var $elems = this;
            var waiting = this.length;

            return $elems.one('load', function () {
                --waiting;
                if (!waiting) {
                    fn.call(window);
                }
            });
        }
    });
})(jQuery);
