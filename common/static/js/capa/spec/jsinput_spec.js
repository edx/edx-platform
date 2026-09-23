describe('JSInput', function() {
    var $jsinputContainers;
    var $inputFields;

    beforeEach(function() {
        loadFixtures('js/capa/fixtures/jsinput.html');
        $jsinputContainers = $('.jsinput');
        $inputFields = $('input[id^="input_"]');
        JSInput.walkDOM();
    });

    it('sets all data-processed attributes to true on first load', function() {
        $jsinputContainers.each(function(index, item) {
            expect(item).toHaveData('processed', true);
        });
    });

    it('sets the waitfor attribute to its update function', function() {
        $inputFields.each(function(index, item) {
            expect(item).toHaveAttr('waitfor');
        });
    });

    it('tests the correct number of jsinput instances', function() {
        expect($jsinputContainers.length).toEqual(2);
        expect($jsinputContainers.length).toEqual($inputFields.length);
    });
});

describe('JSInput sandboxed html_file', function() {
    var assetDir = window.location.origin + '/asset-v1:edX+DemoX+Demo+type@asset+block/';
    // getGrade reports whether it could reach the parent page: under the
    // LP-1255 asset sandbox it must never run same-origin with the LMS.
    var html = '<html><head></head><body><script src="grade.js"></script><script>' +
        'window.getGrade = function() {' +
        '  try { return window.parent.document ? "leaked" : "leaked"; } catch (e) { return "isolated"; }' +
        '};' +
        '</script></body></html>';
    var iframe, $inputField;

    function loadWith(headers, done) {
        spyOn(window, 'fetch').and.returnValue(Promise.resolve(new Response(html, {headers: headers})));
        spyOn(Channel, 'build').and.callThrough();
        JSInput.walkDOM();
        // Let the fetch/text promise chain settle and the srcdoc load.
        setTimeout(done, 300);
    }

    beforeEach(function() {
        loadFixtures('js/capa/fixtures/jsinput.html');
        iframe = $('iframe[name="iframe_1"]').get(0);
        iframe.src = assetDir + 'jsinput_problem.html';
        $inputField = $('#input_1');
        // Leave only the first problem in play, so fetch sees one request.
        $('#inputtype_2').attr('data-processed', 'true');
    });

    describe('when the asset is served with a sandbox CSP', function() {
        describe('for a same-origin (sop) problem', function() {
            beforeEach(function(done) {
                $('#inputtype_1').removeAttr('data-sop').removeAttr('data-setstate');
                loadWith({'Content-Security-Policy': 'sandbox'}, done);
            });

            it('fetches only the same-origin html_file', function() {
                expect(window.fetch.calls.count()).toEqual(1);
                expect(window.fetch.calls.argsFor(0)[0]).toEqual(assetDir + 'jsinput_problem.html');
            });

            it('loads its markup via srcdoc with a base pointing at the asset directory', function() {
                expect(iframe.srcdoc).toContain('<base href="' + assetDir + '">');
                expect(iframe.srcdoc).toContain('<script src="grade.js"></script>');
            });

            it('keeps the html_file in an opaque origin', function() {
                expect(iframe.getAttribute('sandbox')).toContain('allow-scripts');
                expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin');
            });

            it('grades through postMessage without the html_file reaching the parent', function(done) {
                $inputField.data('waitfor')(function() {
                    expect($inputField.val()).toEqual('isolated');
                    done();
                });
            });
        });

        describe('for a JSChannel (sop=false) problem', function() {
            beforeEach(function(done) {
                loadWith({'Content-Security-Policy': 'sandbox'}, done);
            });

            it('keeps the html_file in an opaque origin', function() {
                expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin');
            });

            it('accepts messages from the opaque origin, pinned to the iframe window', function() {
                expect(Channel.build).toHaveBeenCalledWith(jasmine.objectContaining({
                    window: iframe.contentWindow,
                    origin: '*'
                }));
            });

            it('does not inject the sop bridge', function() {
                expect(iframe.srcdoc).not.toContain('jsinputBridge');
            });
        });
    });

    describe('when the asset is not sandboxed', function() {
        beforeEach(function(done) {
            loadWith({}, done);
        });

        it('leaves the iframe alone', function() {
            expect(iframe.hasAttribute('srcdoc')).toBe(false);
        });

        it('builds the channel against the asset origin as before', function() {
            expect(Channel.build).toHaveBeenCalledWith(jasmine.objectContaining({
                origin: assetDir
            }));
        });
    });
});
