"""ScopeBench — the strategic-scope benchmark, and the scope lane's example hosts.

A **per-architecture subfolder**, not another flat ``arch_*.py`` at the top of
``examples/`` (operator decision, 2026-08-03, recorded in the
``strategic-scope-governor`` frame's assumptions). The bee-hive family lives
flat; the scope family lives here.

What is in here today:

===================  ========================================================
module               what it owns
===================  ========================================================
``episodes.py``      the machine-gradable episode schema, the family catalog,
                     the deterministic generators and the committed seeds
                     (``t9``)
``oracle.py``        the **exact** solver: the optimum, the Pareto frontier,
                     the locally-attractive-but-globally-wrong action and the
                     valid do-not-intervene state — all computed, never
                     claimed (``t9``)
``subordinate.py``   the **deterministic perfect subordinate** (Stage 1) and
                     the scripted planners that are its deterministic
                     controls (``t9``)
``scopebench.py``    arms ``A0``-``A3`` as data, the four disjoint record
                     axes, the grader, and the seven-condition verdict rule
                     (``t9``)
``seats.py``         explicit seat-to-role configuration — strategy rides
                     the lobes ``cortex`` role, operation rides ``worker``,
                     interaction rides ``senses`` — resolved by role name from
                     a ``/capabilities``-shaped payload, degrading to
                     actor-only on a missing or not-ready role (``t7``)
===================  ========================================================

**Nothing here dials a model.** The scaffold (``t9``) and the seat wiring
(``t7``) are both hermetic: the pre-registered live series is ``t11``'s, run
under ``docs/live-test-results/scopebench-preregistration.md``, which is
committed before any live result exists. ``tests/test_scopebench.py`` and
``tests/test_scope_seats.py`` each prove the no-live-dial property
structurally rather than leaving it to this sentence.
"""
