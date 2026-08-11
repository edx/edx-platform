curl / Postman examples for Phase B1 shell + batch child API.

Prerequisites
-------------
- LMS running (devstack or staging)
- Waffle flag ``courseware.render_xblock.lazy_library_content`` enabled for the course
- Vertical / library_content with estimated problems > LARGE_VERTICAL_PROBLEM_THRESHOLD (default 20)
- Learner enrolled; session cookie or JWT available

1) Shell render (placeholders + xblock.lazy.ready)
-------------------------------------------------

.. code-block:: bash

   # Replace USAGE_KEY with the vertical or library_content usage key.
   curl -sS -c cookies.txt -b cookies.txt \\
     -H "Accept: text/html" \\
     "https://LMS_HOST/xblock/USAGE_KEY?view=student_view&render_mode=shell&recheck_access=1" \\
     | tee shell.html

   # Expect:
   # - HTTP 200
   # - HTML containing class="vert-mod-lazy" and vert-lazy-placeholder divs
   # - Inline script posting {type: "xblock.lazy.ready", child_usage_keys: [...]}
   # - Custom attribute render_mode=shell in Datadog / New Relic when instrumented

   # Full mode (default) must still work when flag is off or render_mode omitted:
   curl -sS -c cookies.txt -b cookies.txt \\
     "https://LMS_HOST/xblock/USAGE_KEY?view=student_view"

2) Batch children API
---------------------

.. code-block:: bash

   # child_usage_keys from the shell HTML data-usage-key attributes (comma-separated, max 10).
   curl -sS -c cookies.txt -b cookies.txt \\
     -H "Accept: application/json" \\
     "https://LMS_HOST/api/courseware/v1/xblock_children/?parent_usage_key=PARENT_KEY&child_usage_keys=CHILD1,CHILD2"

   # Expect JSON:
   # {
   #   "parent_usage_key": "...",
   #   "results": [{"usage_key": "...", "html": "<div>...</div>", "resources": [...]}],
   #   "errors": []
   # }

   # Forbidden child (not in learner selected set) → errors[].error == "forbidden"
   # (HTTP 403 if *all* requested children are forbidden)

   curl -sS -c cookies.txt -b cookies.txt \\
     "https://LMS_HOST/api/courseware/v1/xblock_children/?parent_usage_key=PARENT_KEY&child_usage_keys=UNSELECTED_KEY"

3) Auth notes
-------------
- Browser session: log in via LMS, reuse cookies (``-c/-b cookies.txt``).
- JWT: ``Authorization: JWT <token>`` (same stack as other /api/courseware/ endpoints).
- Enrollment required (``get_course_with_access``).

4) Feature flag
---------------
Enable in Django admin / waffle:

- Flag: ``courseware.render_xblock.lazy_library_content``
- Optional setting: ``LARGE_VERTICAL_PROBLEM_THRESHOLD`` (default 20)
- Optional setting: ``XBLOCK_CHILDREN_BATCH_MAX`` (default 10)
