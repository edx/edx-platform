Studio warning sample — Phase C (large library / item bank Count)
=================================================================

When ``max_count`` (Studio field **Count**) exceeds
``LIBRARY_CONTENT_MAX_COUNT_WARNING_THRESHOLD`` (default **25**), Studio shows:

**Type:** Warning (publish still allowed)

**Message text:**

    This block is configured to show 90 problems to each learner. Large counts
    in a single unit can cause slow loads or timeouts for learners. Split the
    quiz across multiple units or verticals, or lower Count to 25 or fewer.

**Action button:** Edit the configuration.

Optional org hard-cap
---------------------
With course waffle ``contentstore.hard_cap_library_content_max_count`` enabled,
the same case becomes an **Error** summary and appends:

    Your organization requires Count to be at most 25.

Optional runbook URL
--------------------
If ``LIBRARY_CONTENT_LARGE_MAX_COUNT_HELP_URL`` is set in Django settings, that
URL is appended to the message body.
