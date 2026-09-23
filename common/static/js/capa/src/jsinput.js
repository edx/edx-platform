/*
 * JSChannel (https://github.com/mozilla/jschannel) will be loaded prior to this
 * script. We will use it use to let JSInput call 'gradeFn', and eventually
 * 'stateGetter' & 'stateSetter' in the iframe's content even if it hasn't the
 * same origin, therefore bypassing SOP:
 * https://developer.mozilla.org/en-US/docs/Web/JavaScript/Same_origin_policy_for_JavaScript
 */

// eslint-disable-next-line no-shadow-restricted-names
var JSInput = (function($, undefined) {
    // Initialize js inputs on current page.
    // N.B.: No library assumptions about the iframe can be made (including,
    // most relevantly, jquery). Keep in mind what happens in which context
    // when modifying this file.

    // When all the problems are first loaded, we want to make sure the
    // constructor only runs once for each iframe; but we also want to make
    // sure that if part of the page is reloaded (e.g., a problem is
    // submitted), the constructor is called again.

    /*                      Utils                               */

    // Take a string and find the nested object that corresponds to it. E.g.:
    //    _deepKey(obj, "an.example") -> obj["an"]["example"]
    function _deepKey(obj, path) {
        for (var i = 0, p = path.split('.'), len = p.length; i < len; i++) {
            obj = obj[p[i]];
        }
        return obj;
    }

    // Course assets are served with `Content-Security-Policy: sandbox` (see
    // contentserver/views.py) so an uploaded HTML file can never script
    // against the LMS/Studio session. That also disables scripts in a
    // same-origin html_file, so its grade/state functions never exist and
    // submitting the problem hangs silently.
    //
    // For such a file, re-load its markup via srcdoc in an iframe WITHOUT
    // `allow-same-origin`. Its scripts run again, but in an opaque origin: it
    // cannot reach this page's DOM, cookies, storage or same-origin APIs,
    // which is the isolation the sandbox header exists to provide. The
    // problem then talks to it only through postMessage.
    // Only scripts are re-enabled, and only in an opaque origin; forms,
    // popups, downloads and navigation stay blocked as under the asset's
    // own CSP sandbox.
    var OPAQUE_SANDBOX_FLAGS = 'allow-scripts';
    var BRIDGE_TIMEOUT_MS = 1000;

    // Runs inside the opaque html_file and answers the grade/state calls a
    // same-origin (sop) problem used to make by reaching into its window.
    function _bridgeScript(parentOrigin) {
        return '(function() {' +
            'var parentOrigin = ' + JSON.stringify(parentOrigin) + ';' +
            'window.addEventListener("message", function(e) {' +
                'var msg = e.data, reply, fn, i, p, result;' +
                'if (e.source !== window.parent || e.origin !== parentOrigin ||' +
                    ' !msg || msg.jsinputBridge !== "call") { return; }' +
                'reply = {jsinputBridge: "reply", id: msg.id};' +
                'try {' +
                    'fn = window;' +
                    'for (i = 0, p = msg.fn.split("."); i < p.length; i++) { fn = fn[p[i]]; }' +
                    'result = fn.apply(null, msg.args || []);' +
                    'reply.ok = true;' +
                    'reply.result = (result === undefined || result === null) ? result : String(result);' +
                '} catch (err) {' +
                    'reply.ok = false;' +
                    'reply.error = String(err);' +
                '}' +
                'window.parent.postMessage(reply, parentOrigin);' +
            '});' +
        '}());';
    }

    // Also runs inside the opaque html_file, before any of its own scripts.
    // Many html_files read or fill the problem's own fields through
    // `window.parent.document`, which the opaque origin (correctly) blocks.
    // Give them a stand-in `parent` whose `document` is an inert copy of just
    // this problem's markup and field values, sent by the page before each
    // call; field values the file writes are sent back. The real page, its
    // cookies and session stay unreachable: this is a convenience, not the
    // security boundary (the opaque origin is).
    function _parentShimScript(parentOrigin) {
        return '(function() {' +
            'var parentOrigin = ' + JSON.stringify(parentOrigin) + ';' +
            'var realParent = window.parent;' +
            'var doc = document.implementation.createHTMLDocument("");' +
            'var fields = [];' +
            'function values() {' +
                'return fields.map(function(f) {' +
                    'return (f.type === "checkbox" || f.type === "radio") ? f.checked : f.value;' +
                '});' +
            '}' +
            'var sent = "[]";' +
            'var fakeParent = new Proxy({' +
                'document: doc,' +
                'postMessage: function() { return realParent.postMessage.apply(realParent, arguments); }' +
            '}, {' +
                'get: function(t, k) {' +
                    'if (k in t) { return t[k]; }' +
                    // Functions on the real page can't be called from here.
                    'return function() {};' +
                '}' +
            '});' +
            'try { window.parent = fakeParent; } catch (e) { return; }' +
            // JSChannel matches replies by window identity, so it must be
            // handed the real parent window, not the stand-in.
            'var RealChannel;' +
            'Object.defineProperty(window, "Channel", {' +
                'configurable: true,' +
                'get: function() { return RealChannel; },' +
                'set: function(v) {' +
                    'RealChannel = Object.create(v);' +
                    'RealChannel.build = function(cfg) {' +
                        'if (cfg && cfg.window === fakeParent) { cfg.window = realParent; }' +
                        'return v.build(cfg);' +
                    '};' +
                '}' +
            '});' +
            'window.addEventListener("message", function(e) {' +
                'var msg = e.data;' +
                'if (e.source !== realParent || e.origin !== parentOrigin) { return; }' +
                'if (msg && msg.jsinputPage === "snapshot") {' +
                    'doc.body.innerHTML = msg.html;' +
                    'fields = Array.prototype.slice.call(doc.querySelectorAll("input, textarea, select"));' +
                    'fields.forEach(function(f, i) {' +
                        'if (f.type === "checkbox" || f.type === "radio") { f.checked = msg.values[i] === true; }' +
                        'else if (typeof msg.values[i] === "string") { f.value = msg.values[i]; }' +
                    '});' +
                    'sent = JSON.stringify(values());' +
                    'return;' +
                '}' +
                // After any call from the page, send back fields it changed.
                'setTimeout(function() {' +
                    'var now = JSON.stringify(values());' +
                    'if (now !== sent) {' +
                        'sent = now;' +
                        'realParent.postMessage({jsinputPage: "values", values: values()}, parentOrigin);' +
                    '}' +
                '}, 0);' +
            '});' +
        '}());';
    }

    // The live fields of the problem `elem` belongs to, in document order.
    function _problemOf(elem) {
        return $(elem).closest('.problems-wrapper').get(0) || $(elem).parent().get(0);
    }

    function _problemFields(problem) {
        return $(problem).find('input, textarea, select').not('.jsinput iframe').get();
    }

    // Returns sendSnapshot(): posts the problem's markup and field values to
    // the shim above, and applies field values the shim sends back.
    function _pageShim(iframe, problem) {
        var loads = 0;

        // The first load is the srcdoc set up above. Any later load means the
        // html_file navigated its frame elsewhere; never send page data there.
        iframe.addEventListener('load', function() { loads += 1; });

        window.addEventListener('message', function(e) {
            var msg = e.data;
            if (e.source !== iframe.contentWindow || !msg || msg.jsinputPage !== 'values'
                || !Array.isArray(msg.values)) {
                return;
            }
            _problemFields(problem).forEach(function(f, i) {
                if (f.type === 'checkbox' || f.type === 'radio') {
                    if (typeof msg.values[i] === 'boolean') {
                        f.checked = msg.values[i];
                    }
                } else if (typeof msg.values[i] === 'string') {
                    f.value = msg.values[i];
                }
            });
        });

        return function() {
            var copy;
            if (loads > 1) {
                return;
            }
            copy = problem.cloneNode(true);
            // Never hand over other frames (including this html_file's own
            // srcdoc) or scripts; only markup and the learner's field values.
            $(copy).find('iframe, script').remove();
            $(copy).find('[data-content]').addBack('[data-content]').removeAttr('data-content');
            iframe.contentWindow.postMessage({
                jsinputPage: 'snapshot',
                html: copy.innerHTML,
                values: _problemFields(problem).map(function(f) {
                    return (f.type === 'checkbox' || f.type === 'radio') ? f.checked : f.value;
                })
            }, '*');
        };
    }

    // Returns call(fnPath, args) -> Promise, talking to the bridge above.
    function _bridgeCaller(iframe) {
        var pending = {},
            nextId = 0;

        window.addEventListener('message', function(e) {
            var msg = e.data;
            if (e.source !== iframe.contentWindow || !msg || msg.jsinputBridge !== 'reply'
                || !pending[msg.id]) {
                return;
            }
            clearTimeout(pending[msg.id].timer);
            if (msg.ok) {
                pending[msg.id].resolve(msg.result);
            } else {
                pending[msg.id].reject(new Error(msg.error));
            }
            delete pending[msg.id];
        });

        return function(fn, args) {
            return new Promise(function(resolve, reject) {
                var id = ++nextId;
                pending[id] = {
                    resolve: resolve,
                    reject: reject,
                    timer: setTimeout(function() {
                        delete pending[id];
                        reject(new Error('JSInput: no reply from html_file for ' + fn));
                    }, BRIDGE_TIMEOUT_MS)
                };
                // An opaque origin can't be named as a targetOrigin; the
                // recipient is pinned by posting to this iframe's window.
                iframe.contentWindow.postMessage(
                    {jsinputBridge: 'call', id: id, fn: fn, args: args || []}, '*'
                );
            });
        };
    }

    // Resolves to true if the html_file is a sandboxed course asset and has
    // been re-loaded in an opaque origin, false if the iframe was left alone.
    function _loadSandboxedHtmlFile(iframe, path, sop) {
        var src = new URL(iframe.src, window.location.href);

        if (src.origin !== window.location.origin || !window.fetch || !window.DOMParser) {
            return Promise.resolve(false);
        }

        return window.fetch(src.href, {credentials: 'same-origin'}).then(function(response) {
            var csp = response.headers.get('Content-Security-Policy') || '';
            if (!response.ok || !/(^|[\s;,])sandbox([\s;,]|$)/i.test(csp)) {
                return null;
            }
            return response.text();
        }).then(function(html) {
            var doc, base, shim, bridge;
            if (html === null) {
                return false;
            }
            doc = new DOMParser().parseFromString(html, 'text/html');
            // srcdoc documents resolve relative URLs against the parent page,
            // so point them back at the asset's directory.
            base = doc.createElement('base');
            base.setAttribute('href', path);
            doc.head.insertBefore(base, doc.head.firstChild);
            shim = doc.createElement('script');
            shim.textContent = _parentShimScript(window.location.origin);
            doc.head.insertBefore(shim, base.nextSibling);
            if (sop) {
                bridge = doc.createElement('script');
                bridge.textContent = _bridgeScript(window.location.origin);
                doc.head.insertBefore(bridge, shim.nextSibling);
            }
            // Sandbox flags apply at the next navigation, which setting
            // srcdoc triggers, so this must come first.
            iframe.setAttribute('sandbox', OPAQUE_SANDBOX_FLAGS);
            iframe.srcdoc = '<!DOCTYPE html>' + doc.documentElement.outerHTML;
            return true;
        }).catch(function(err) {
            console.debug('JSInput: could not load html_file', err);
            return false;
        });
    }

    /*      END     Utils                                   */

    function jsinputConstructor(elem) {
        // Define an class that will be instantiated for each jsinput element
        // of the DOM

        /*                      Private methods                          */

        var jsinputContainer = $(elem).parent().find('.jsinput'),
            jsinputAttr = function(e) { return $(jsinputContainer).attr(e); },
            iframe = $(elem).find('iframe[name^="iframe_"]').get(0),
            cWindow = iframe.contentWindow,
            path = iframe.src.substring(0, iframe.src.lastIndexOf('/') + 1),
            // Get the hidden input field to pass to customresponse
            inputField = $(elem).parent().find('input[id^="input_"]'),
            // Get the grade function name
            gradeFn = jsinputAttr('data'),
            // Get state getter
            stateGetter = jsinputAttr('data-getstate'),
            // Get state setter
            stateSetter = jsinputAttr('data-setstate'),
            // Get stored state
            storedState = jsinputAttr('data-stored'),
            // Get initial state
            initialState = jsinputAttr('data-initial-state'),
            // Bypass single-origin policy only if this attribute is "false"
            // In that case, use JSChannel to do so.
            sop = jsinputAttr('data-sop'),
            channel,
            // Set when the html_file runs in an opaque origin and a sop
            // problem has to reach it through postMessage.
            bridgeCall,
            // Set when the html_file runs in an opaque origin; sends it a
            // copy of this problem's markup and field values.
            sendSnapshot = function() {},
            isReady = false,
            ready;

        sop = (sop !== 'false');

        // Only once we know whether the html_file had to be re-loaded in an
        // opaque origin can we choose how to talk to it.
        ready = _loadSandboxedHtmlFile(iframe, path, sop).then(function(opaque) {
            if (opaque) {
                sendSnapshot = _pageShim(iframe, _problemOf(elem));
                iframe.addEventListener('load', function() { sendSnapshot(); });
            }
            if (opaque && sop) {
                bridgeCall = _bridgeCaller(iframe);
            } else if (!sop) {
                channel = Channel.build({
                    window: cWindow,
                    // An opaque html_file posts from origin "null"; jschannel
                    // still only accepts messages from this iframe's window.
                    origin: opaque ? '*' : path,
                    scope: 'JSInput'
                });
            }
            isReady = true;
        });

        // Called when the html_file's grade/state function fails (e.g. it
        // tries to reach into this page from its sandbox). The submission is
        // not sent; the reason is only logged.
        function gradeFailed(err, message) {
            console.debug('JSInput: html_file could not be graded', err, message || '');
        }

        /*                       Public methods                     */

        // Only one public method that updates the hidden input field.
        var update = function(callback) {
            var answer, state, store;

            if (!isReady) {
                ready.then(function() { update(callback); });
                return;
            }

            sendSnapshot();

            if (bridgeCall) {
                bridgeCall(gradeFn).then(function(val) {
                    answer = val;
                    if (stateGetter && stateSetter) {
                        return bridgeCall(stateGetter).then(function(val) { // eslint-disable-line no-shadow
                            state = unescape(val); // xss-lint: disable=javascript-escape
                            inputField.val(JSON.stringify({answer: answer, state: state}));
                        });
                    }
                    inputField.val(answer);
                    return undefined;
                }).then(callback, gradeFailed);
            } else if (sop) {
                answer = _deepKey(cWindow, gradeFn)();
                // Setting state presumes getting state, so don't get state
                // unless set state is defined.
                if (stateGetter && stateSetter) {
                    state = unescape(_deepKey(cWindow, stateGetter)()); // xss-lint: disable=javascript-escape
                    store = {
                        answer: answer,
                        state: state
                    };
                    inputField.val(JSON.stringify(store));
                } else {
                    inputField.val(answer);
                }
                callback();
            } else {
                channel.call({
                    method: 'getGrade',
                    params: '',
                    success: function(val) {
                        answer = decodeURI(val.toString());

                        // Setting state presumes getting state, so don't get
                        // state unless set state is defined.
                        if (stateGetter && stateSetter) {
                            channel.call({
                                method: 'getState',
                                params: '',
                                // eslint-disable-next-line no-shadow
                                success: function(val) {
                                    state = decodeURI(val.toString());
                                    store = {
                                        answer: answer,
                                        state: state
                                    };
                                    inputField.val(JSON.stringify(store));
                                    callback();
                                },
                                error: gradeFailed
                            });
                        } else {
                            inputField.val(answer);
                            callback();
                        }
                    },
                    error: gradeFailed
                });
            }
        };

        /*                      Initialization                          */

        // Put the update function as the value of the inputField's "waitfor"
        // attribute so that it is called when the check button is clicked.
        inputField.data('waitfor', update);

        // Check whether application takes in state and there is a saved
        // state to give it. If stateSetter is specified but calling it
        // fails, wait and try again, since the iframe might still be
        // loading.
        if (stateSetter && (storedState || initialState)) {
            var stateValue, jsonValue;

            if (storedState) {
                try {
                    jsonValue = JSON.parse(storedState);
                } catch (err) {
                    jsonValue = storedState;
                }

                if (typeof jsonValue === 'object') {
                    stateValue = jsonValue.state;
                } else {
                    stateValue = jsonValue;
                }
            } else {
                // use initial_state string as the JSON string for stateValue.
                stateValue = initialState;
            }

            // Try calling setstate every 200ms while it throws an exception,
            // up to five times; give up after that.
            // (Functions in the iframe may not be ready when we first try
            // calling it, but might just need more time. Give the functions
            // more time.)
            // 200 ms and 5 times are arbitrary but this has functioned with the
            // only application that has ever used JSInput, jsVGL. Something
            // more sturdy should be put in place.
            // eslint-disable-next-line no-inner-declarations
            function whileloop(n) {
                if (n > 0) {
                    try {
                        sendSnapshot();
                        if (bridgeCall) {
                            bridgeCall(stateSetter, [stateValue]).catch(function() {
                                setTimeout(function() { whileloop(n - 1); }, 200);
                            });
                        } else if (sop) {
                            _deepKey(cWindow, stateSetter)(stateValue);
                        } else {
                            channel.call({
                                method: 'setState',
                                params: stateValue,
                                success: function() {
                                },
                                error: function(err, message) {
                                    console.debug('JSInput: could not set state', err, message || '');
                                }
                            });
                        }
                    } catch (err) {
                        setTimeout(function() { whileloop(n - 1); }, 200);
                    }
                } else {
                    console.debug('Error: could not set state');
                }
            }
            ready.then(function() { whileloop(5); });
        }
    }

    function walkDOM() {
        var $jsinputContainers = $('.jsinput');
        // When a JSInput problem loads, its data-processed attribute is false,
        // so the jsconstructor will be called for it.
        // The constructor will not be called again on subsequent reruns of
        // this file by other JSInput. Only if it is reloaded, either with the
        // rest of the page or when it is submitted, will this constructor be
        // called again.
        $jsinputContainers.each(function(index, value) {
            var dataProcessed = ($(value).attr('data-processed') === 'true');
            if (!dataProcessed) {
                jsinputConstructor(value);
                $(value).attr('data-processed', 'true');
            }
        });
    }

    // This is ugly, but without a timeout pages with multiple/heavy jsinputs
    // don't load properly.
    // 300 ms is arbitrary but this has functioned with the only application
    // that has ever used JSInput, jsVGL. Something more sturdy should be put in
    // place.
    if ($.isReady) {
        setTimeout(walkDOM, 300);
    } else {
        $(document).ready(setTimeout(walkDOM, 300));
    }

    return {
        jsinputConstructor: jsinputConstructor,
        walkDOM: walkDOM
    };
}(window.jQuery));
