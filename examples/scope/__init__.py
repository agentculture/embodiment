"""ScopeBench — the strategic-scope benchmark, and the scope lane's example hosts.

A **per-architecture subfolder**, not another flat ``arch_*.py`` at the top of
``examples/`` (operator decision, 2026-08-03, recorded in the
``strategic-scope-governor`` frame's assumptions). The bee-hive family lives
flat; the scope family lives here.

What is in here today (plan task ``t9`` — the scaffold, and only the scaffold):

===================  ========================================================
module               what it owns
===================  ========================================================
``episodes.py``      the machine-gradable episode schema, the family catalog,
                     the deterministic generators and the committed seeds
``oracle.py``        the **exact** solver: the optimum, the Pareto frontier,
                     the locally-attractive-but-globally-wrong action and the
                     valid do-not-intervene state — all computed, never claimed
``subordinate.py``   the **deterministic perfect subordinate** (Stage 1) and
                     the scripted planners that are its deterministic controls
``scopebench.py``    arms ``A0``-``A3`` as data, the four disjoint record axes,
                     the grader, and the seven-condition verdict rule
===================  ========================================================

**Nothing here dials a model.** ``t9`` ships the scaffold and its deterministic
controls; the pre-registered live series is ``t11``'s, run under
``docs/live-test-results/scopebench-preregistration.md``, which is committed
before any live result exists. ``tests/test_scopebench.py`` proves the
no-live-dial property structurally rather than leaving it to this sentence.
"""
