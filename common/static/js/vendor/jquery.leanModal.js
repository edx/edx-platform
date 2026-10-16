// leanModal v1.1 by Ray Stone - http://finelysliced.com.au
// Dual licensed under the MIT and GPL

// Updated to prevent divs with duplicate IDs from being rendered.

(function($) {
    $.fn.extend({
        leanModal: function(options) {
            var defaults = {
                top: 100,
                overlay: 0.5,
                closeButton: null
            };

            // Only append the overlay element if it isn't already present.
            if ($("#lean_overlay").length == 0) {
                var overlay = $("<div id='lean_overlay'></div>");
                $("body").append(overlay);
            }

            options = $.extend(defaults, options);
            return this.each(function() {
                var o = options;
                $(this).click(function(e) {
                    var modal_id = $(this).attr("href");
                    $("#lean_overlay").click(function() {
                        close_modal(modal_id)
                    });
                    $(o.closeButton).click(function() {
                        close_modal(modal_id)
                    });
                    var modal_height = $(modal_id).outerHeight();
                    var modal_width = $(modal_id).outerWidth();
                    $("#lean_overlay").css({
                        "display": "block",
                        opacity: 0
                    });
                    $("#lean_overlay").fadeTo(200, o.overlay);
                    $(modal_id).css({
                        "display": "block",
                        "position": "fixed",
                        "opacity": 0,
                        "z-index": 11000,
                        "left": 50 + "%",
                        "margin-left": -(modal_width / 2) + "px",
                        "top": o.top + "px"
                    });
                    $(modal_id).fadeTo(200, 1);
                    if ($("body").hasClass("view-in-mfe")) {
                        // Inside the learning MFE the iframe is stretched to its full content
                        // height and never scrolls, so a fixed top lands at the top of the page.
                        // IntersectionObserver reports the part of this document visible in the
                        // parent viewport (even cross-origin); center the modal in that region.
                        new IntersectionObserver(function(entries, observer) {
                            observer.disconnect();
                            var visible = entries[0].intersectionRect;
                            if (visible.height > 0) {
                                $(modal_id).css({
                                    "top": visible.top + 40 + "px",
                                    "max-height": visible.height - 80 + "px"
                                });
                            }
                        }).observe(document.body);
                    }
                    e.preventDefault()
                })
            });

            function close_modal(modal_id) {
                $("#lean_overlay").fadeOut(200);
                $(modal_id).css({
                    "display": "none"
                })
            }
        }
    })
})(jQuery);
