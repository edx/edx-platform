:orphan:

Plans
#####

Documents in this directory describe work that is in flight, or temporary
infrastructure that is expected to be removed again.

Lifecycle
*********

* **A plan lives exactly as long as the thing it describes.** Finishing or
  dismantling the thing includes deleting its plan, in the same pull request
  where practical.
* Name the file after its ticket, for example ``LP-1148-ulmo4-upgrade.md``.
  Supporting material for the same ticket (such as an audit) uses the same
  prefix, for example ``LP-1148-ulmo4-merge-audit.md``.
* Link the ticket at the top of the plan. Do not duplicate the plan into the
  ticket or into pull request descriptions: the plan is the single copy.
* Anything expected to outlive its ticket does not belong here. Put decisions in
  ``docs/decisions/`` and durable procedures in ``docs/how-tos/``.

Plans themselves are Markdown, which the Sphinx build ignores; only this README
follows the ``.rst`` convention used elsewhere in ``docs/``. It is marked
``:orphan:`` so it is not expected in any toctree.

Current plans
*************

* ``LP-1148-ulmo4-upgrade.md`` -- landing the ulmo.4+ upgrade (Django 5.2) on
  ``release-ulmo``. Delete when the upgrade is in production and stable.
* ``LP-1148-ulmo4-merge-audit.md`` -- merge audit of the upgrade branch,
  including the scripts used. Delete together with the plan.
