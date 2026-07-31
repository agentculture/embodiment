#!/usr/bin/env python3
"""challenge_coding — the coding rung, and a grader built to the M2 discipline.

Task **t7** of the orchestrator-worker-architectures plan. Problems 1–3b of
``docs/challenge-problems.md`` hand a mind a puzzle and read one answer off its
prose. This rung is a different instrument: **the mind writes a program**, the
program runs in a bounded, network-less container, and the values it returned
are compared against truth computed *on the host*.

The three problems, their verified answers, and the exhaustive searches that
establish them live in ``docs/challenge-problems.md`` §5 — committed **before**
this file, because that document's own rule is that a challenge lands there
first. Everything here recomputes those answers rather than restating them; the
test suite checks the document and the harness still agree.

Three rungs, because a rung everything passes measures nothing
--------------------------------------------------------------
======================  ======  =======================================
``parity_subsets``      easy    problem 1 generalised; ``n=90`` refuses
                                any exponential search
``preimage_count``      medium  two of five routines are lossy, so
                                step-by-step inversion is wrong
``register_recover``    hard    write the solver for problems 2, 3 and
                                3b: exact Hamming, the volatility
                                fault, and *all* solutions
======================  ======  =======================================

The jail is the whole safety property
-------------------------------------
**Model-written code executes only inside the workspace.** That is held by
construction, in four places that have nothing to do with this docstring:

1. **This module contains no execution primitive.** No ``exec``, ``eval``,
   ``compile``, ``__import__``, ``subprocess``, ``os.system``, ``runpy``,
   ``ctypes``. A test walks this file's AST — *and the test file's own* — and
   fails if any of them ever appears. A suite that ran a fixture "just to see
   what it produces" would be the exact hole the criterion names, so the guard
   covers both files.
2. **There is exactly one execution call site**, :func:`run_in_workspace`,
   which hands the program to :meth:`embodiment.workspace.MuseWorkspace.execute`
   as an argv element and nothing else. A test counts the call sites, so
   widening the seam is a reviewable diff rather than a quiet edit.
3. **No container, no run.** When nothing can be provisioned the verdict is
   :data:`VERDICT_NO_WORKSPACE`, the transition is recorded for the host
   (constraint C3), and the program is simply never executed. There is no
   local-fallback branch to reach.
4. **The empirical half.** A committed fixture writes a sentinel file on
   whatever machine runs it; after the whole pipeline has graded it three ways,
   the sentinel does not exist.

The workspace itself reaches nothing — no repository, no eidetic or coherence
store, no network — and that is :mod:`embodiment.workspace`'s property, proved
there. This module adds no policy, no mount and no environment; it passes an
argv.

Why the container is never trusted with a verdict
-------------------------------------------------
The driver appended to the model's source carries **no expected values**. It
calls the function once per graded case and prints what came back; the
comparison happens here, on the host, against :func:`truth_parity_subsets` and
its siblings. So there is nothing inside the container to read an answer off —
a model may read the graded *inputs* out of its own command line if it likes,
and it will find no outputs — and a program that forges a result line still has
to forge *correct* outputs, which means solving the problem.

Two smaller defences ride along. The result line carries a per-run nonce, so
the model's own stdout can never be mistaken for the driver's; and **two** nonce
lines are refused rather than resolved, because a second one means something
other than the driver wrote it.

The M2 grader kit
-----------------
Four graders shipped defective last cycle and every one was caught by a human
reading data, not by a test (``docs/plans/next-cycle-candidates.md`` §M2). All
four requirements ship here:

(a) **adversarial fixtures** — :data:`FIXTURES` carries inputs built to score
    well while being wrong, each with the container output it really produced;
(b) **paraphrase cases** — the same correct content in wording the extractor
    was not written against, plus a structurally different correct solution;
(c) **a vacuity assertion** — :data:`VERDICT_CORRECT` is refused unless the
    workspace ran something, the nonce line was found exactly once, and one row
    came back per graded case. The gate is *recorded* on every result, not
    merely checked;
(d) **committed raw responses** — every result carries the model's full text
    and the extracted source, and :class:`Transcript` rewrites the file after
    every run.

Reproduce
---------
.. code-block:: bash

    # the committed fixtures against a real container — no model needed
    uv run python examples/challenge_coding.py --fixtures --provider docker

    # one live attempt at the easy rung
    COLLEAGUE_API_KEY=... uv run python examples/challenge_coding.py \\
        --rung easy --n 4 --trace-out results/coding-easy.json
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import secrets
import sys
import urllib.request
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment.contract import ModelResponse  # noqa: E402
from embodiment.workspace import (  # noqa: E402
    PROVIDER_DOCKER,
    PROVIDER_FAKE,
    WORKSPACE_TOOL_NAME,
    MuseWorkspace,
)
from examples.challenge_config import write_config_preamble  # noqa: E402

DEFAULT_BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
DEFAULT_CORTEX = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
#: The acting temperature, one constant, so a swapped model runs at the same
#: temperature as the model it is compared against.
DEFAULT_TEMPERATURE = 0.3
#: Per d16: the shipped 2048 default truncated 6.0% of completions with zero
#: degradations recorded, because ``ModelResponse`` carries no ``finish_reason``.
DEFAULT_MAX_TOKENS = 16000

#: Verdicts. Deliberately more than pass/fail: a run that produced no code and a
#: run whose code was wrong are different facts, and folding them together is
#: how ``exit=stopped`` hid two failures under one code for a whole series.
VERDICT_CORRECT = "CORRECT"
VERDICT_WRONG = "WRONG"
#: Nothing that looked like source could be extracted from the response.
VERDICT_NO_CODE = "NO_CODE"
#: Code ran (or should have) but no readable, single, complete result arrived.
VERDICT_NO_RESULT = "NO_RESULT"
#: No container could be provisioned, so nothing was run. **Never** a fallback.
VERDICT_NO_WORKSPACE = "NO_WORKSPACE"

VERDICTS = (
    VERDICT_CORRECT,
    VERDICT_WRONG,
    VERDICT_NO_CODE,
    VERDICT_NO_RESULT,
    VERDICT_NO_WORKSPACE,
)

#: Fixture kinds. An all-negative fixture table is satisfied by a grader that
#: fails everything, so the positives are part of the kit, not decoration.
KIND_ADVERSARIAL = "adversarial"
KIND_PARAPHRASE = "paraphrase"
KIND_CORRECT = "correct"

#: The four M2 requirements and the mechanism that discharges each, named here
#: so the discipline is greppable from the harness rather than only from a plan.
M2_KIT = {
    "adversarial_fixtures": "FIXTURES entries of kind 'adversarial'",
    "paraphrase_case": "FIXTURES entries of kind 'paraphrase', plus extract_code",
    "vacuity_assertion": "the 'vacuity' record on every result; CORRECT needs fired=True",
    "committed_raw_responses": "result['raw_response'] plus Transcript, flushed per run",
}


# ══════════════════════════════════════════════════════════════════════════════
# the truth functions — host-side, exhaustive, never shipped into the container
# ══════════════════════════════════════════════════════════════════════════════


def truth_parity_subsets(n: int) -> int:
    """Subsets of ``{1..n}``: no two consecutive, even element-sum, empty counts.

    The parity-carrying recurrence (method B of the document). Method A —
    enumerating all ``2^n`` subsets — is what the tests check this against for
    ``n <= 16``; it cannot be the shipped implementation because the rung grades
    ``n = 90``.

    The seed is ``E(-1) = 1, O(-1) = 0`` and not ``0, 0``: a subset containing
    ``1`` needs a subset of the empty universe, and there is exactly one of
    those, with sum 0. Seeding it wrong makes this agree with
    :data:`REFERENCE_PARITY` while both are wrong — which is precisely what
    happened during this rung's first live capture, and why the two are written
    in deliberately different styles and why the ``memoised_recursion``
    paraphrase fixture exists. It disagreed, and that is how the defect
    surfaced.
    """
    even, odd = 1, 0
    previous_even, previous_odd = 1, 0
    for k in range(1, n + 1):
        if k % 2 == 0:
            new_even, new_odd = even + previous_even, odd + previous_odd
        else:
            new_even, new_odd = even + previous_odd, odd + previous_even
        even, odd, previous_even, previous_odd = new_even, new_odd, even, odd
    return even


#: The 8-bit routines of problem 3, as lookup tables. Tables rather than lambdas
#: because the tests apply them millions of times.
_OPS8 = {
    "A": tuple((x + 47) % 256 for x in range(256)),
    "B": tuple(x ^ 0xAA for x in range(256)),
    "C": tuple(x >> 1 for x in range(256)),
    "D": tuple(x & 0xDF for x in range(256)),
    "E": tuple((x * 3) % 256 for x in range(256)),
}

#: The 4-bit routines of problem 2.
_OPS4 = {
    "A": tuple((x + 3) % 16 for x in range(16)),
    "B": tuple(x ^ 0b1011 for x in range(16)),
    "C": tuple(((x << 1) | (x >> 3)) & 15 for x in range(16)),
    "D": tuple((x * 5) % 16 for x in range(16)),
    "E": tuple(int(format(x, "04b")[::-1], 2) for x in range(16)),
}


def truth_preimage_count(sequence: str, target: int) -> int:
    """How many of the 256 initial states *sequence* maps onto *target*.

    Forward over every start (method A of the document). The lossy pair — ``C``
    (LSR) and ``D`` (AND 0xDF) — is what makes this more than an inversion, and
    the tests check it against analytically derived backward preimage relations.
    """
    tables = [_OPS8[name] for name in sequence]
    hits = 0
    for start in range(256):
        state = start
        for table in tables:
            state = table[state]
        if state == target:
            hits += 1
    return hits


def truth_register_recover(
    width: int,
    records: Sequence[Sequence[Any]],
    distance: int,
    require_before: Optional[Sequence[str]] = None,
    require_adjacent: Optional[Sequence[str]] = None,
) -> list[tuple[str, str]]:
    """Every ``(initial, order)`` consistent with the recorded values.

    Integer tables, start-major, forward simulation (method A of the document).
    The four things that have to be right at once, each a real failure shape:
    Hamming distance **exactly** *distance* rather than at most; the 8-bit
    volatility fault checked on the state *entering* ``C``, including when ``C``
    runs first; the same initial value legitimately appearing under two orders;
    and an unsatisfiable instance returning ``[]``.
    """
    tables = _OPS4 if width == 4 else _OPS8
    mask = (1 << width) - 1
    wanted = {int(position): int(bits, 2) for position, bits in records}
    orders = []
    for perm in permutations("ABCDE"):
        order = "".join(perm)
        if require_before and order.index(require_before[0]) > order.index(require_before[1]):
            continue
        if require_adjacent and "".join(require_adjacent) not in order:
            continue
        orders.append(order)

    found: list[tuple[str, str]] = []
    for start in range(1 << width):
        for order in orders:
            state = start
            ok = True
            for step, name in enumerate(order, start=1):
                if width == 8 and name == "C" and state % 2 == 0:
                    ok = False
                    break
                state = tables[name][state]
                if step in wanted and bin((state ^ wanted[step]) & mask).count("1") != distance:
                    ok = False
                    break
            if ok:
                found.append((format(start, f"0{width}b"), order))
    return sorted(found)


# ══════════════════════════════════════════════════════════════════════════════
# the problems
# ══════════════════════════════════════════════════════════════════════════════

_PROTOCOL = (
    "Return the complete source of the function in a single Python code block. "
    "Define it at module level. Do not print anything, do not read or write "
    "files, and do not call sys.exit() — your code is imported before it is "
    "called, and anything that ends the process ends the run.\n"
)

STATEMENT_PARITY = (
    "Write a Python function\n\n"
    "    def parity_subsets(n: int) -> int\n\n"
    "returning the number of subsets of {1, 2, ..., n} that contain no two "
    "consecutive integers and have an even element-sum.\n\n"
    "The empty set counts: it contains no two consecutive integers and its sum "
    "is 0, which is even.\n\n"
    "n is between 0 and 200 inclusive. Your function is called with n as large "
    "as 90, so it must not enumerate subsets.\n"
)

STATEMENT_PREIMAGE = (
    "An 8-bit register (values 0..255) has five routines:\n\n"
    "  A: add 47, modulo 256\n"
    "  B: XOR with 10101010 (0xAA)\n"
    "  C: logical shift right by 1\n"
    "  D: bitwise AND with 11011111 (0xDF)\n"
    "  E: multiply by 3, modulo 256\n\n"
    "Write a Python function\n\n"
    "    def preimage_count(sequence: str, target: int) -> int\n\n"
    "returning how many of the 256 possible initial states s satisfy: applying "
    "the routines named in sequence, left to right, to s yields target.\n\n"
    "sequence is a non-empty string over the letters ABCDE; routines may "
    "repeat and need not all appear. There is no fault rule in this problem — "
    "every sequence runs to completion.\n\n"
    'Worked example: preimage_count("BE", 17) == 1.\n'
)

STATEMENT_REGISTER = (
    "Two register families, selected by width.\n\n"
    "width = 4 (values 0..15):\n"
    "  A: add 3, modulo 16\n"
    "  B: XOR with 1011\n"
    "  C: rotate left by one bit\n"
    "  D: multiply by 5, modulo 16\n"
    "  E: reverse the four bits\n"
    "  No fault rule.\n\n"
    "width = 8 (values 0..255):\n"
    "  A: add 47, modulo 256\n"
    "  B: XOR with 10101010 (0xAA)\n"
    "  C: logical shift right by 1\n"
    "  D: bitwise AND with 11011111 (0xDF)\n"
    "  E: multiply by 3, modulo 256\n"
    "  C mandates an odd input: if the state handed to C is even, the run "
    "faults and is not a solution. This applies when C runs first too.\n\n"
    "All five routines execute exactly once each, in some order. The register "
    "was recorded after some of them, and every recorded value is at Hamming "
    "distance EXACTLY distance from the true state at that point — not at "
    "most.\n\n"
    "Write a Python function\n\n"
    "    def register_recover(width, records, distance,\n"
    "                         require_before=None, require_adjacent=None)\n\n"
    "where records is a list of [position, bit_string] pairs, position being "
    "1-based (how many routines have run) and bit_string having length width; "
    "require_before is [X, Y] meaning X runs somewhere before Y, or None; "
    "require_adjacent is [X, Y] meaning Y runs immediately after X, or None.\n\n"
    "Return a list of [initial_bits, order] pairs, initial_bits a bit-string "
    "of length width and order a five-character permutation of ABCDE. The "
    "order of the returned list does not matter. Return [] when nothing is "
    "consistent with the records.\n"
)

#: Written as two explicit arrays rather than as rolling variables — that is,
#: deliberately NOT the shape :func:`truth_parity_subsets` uses. A reference
#: solution that shares an implementation with the truth it is checked against
#: shares its bugs too, and agrees with it while both are wrong.
REFERENCE_PARITY = """\
def parity_subsets(n: int) -> int:
    even = [0] * (n + 1)
    odd = [0] * (n + 1)
    even[0] = 1
    if n >= 1:
        even[1], odd[1] = 1, 1
    for k in range(2, n + 1):
        if k % 2 == 0:
            even[k], odd[k] = even[k - 1] + even[k - 2], odd[k - 1] + odd[k - 2]
        else:
            even[k], odd[k] = even[k - 1] + odd[k - 2], odd[k - 1] + even[k - 2]
    return even[n]
"""

REFERENCE_PREIMAGE = """\
OPS = {
    "A": lambda x: (x + 47) % 256,
    "B": lambda x: x ^ 0xAA,
    "C": lambda x: x >> 1,
    "D": lambda x: x & 0xDF,
    "E": lambda x: (x * 3) % 256,
}


def preimage_count(sequence: str, target: int) -> int:
    hits = 0
    for start in range(256):
        state = start
        for name in sequence:
            state = OPS[name](state)
        if state == target:
            hits += 1
    return hits
"""

REFERENCE_REGISTER = '''\
from itertools import permutations

OPS4 = {
    "A": lambda x: (x + 3) % 16,
    "B": lambda x: x ^ 0b1011,
    "C": lambda x: ((x << 1) | (x >> 3)) & 15,
    "D": lambda x: (x * 5) % 16,
    "E": lambda x: int(format(x, "04b")[::-1], 2),
}
OPS8 = {
    "A": lambda x: (x + 47) % 256,
    "B": lambda x: x ^ 0xAA,
    "C": lambda x: x >> 1,
    "D": lambda x: x & 0xDF,
    "E": lambda x: (x * 3) % 256,
}


def register_recover(width, records, distance, require_before=None, require_adjacent=None):
    """Every (initial, order) whose recorded values sit at Hamming distance
    exactly `distance` from the truth."""
    ops = OPS4 if width == 4 else OPS8
    mask = (1 << width) - 1
    wanted = {int(pos): int(bits, 2) for pos, bits in records}
    out = []
    for perm in permutations("ABCDE"):
        order = "".join(perm)
        if require_before and order.index(require_before[0]) > order.index(require_before[1]):
            continue
        if require_adjacent and "".join(require_adjacent) not in order:
            continue
        for start in range(1 << width):
            state, ok = start, True
            for step, name in enumerate(order, start=1):
                if width == 8 and name == "C" and state % 2 == 0:
                    ok = False
                    break
                state = ops[name](state)
                if step in wanted and bin((state ^ wanted[step]) & mask).count("1") != distance:
                    ok = False
                    break
            if ok:
                out.append([format(start, "0%db" % width), order])
    return out
'''

#: The two register instances this file's problems 2, 3 and 3b already answered.
RECORDS_4 = [[1, "0010"], [3, "0000"], [5, "1111"]]
RECORDS_8 = [[1, "11000111"], [3, "10101011"], [5, "11101000"]]


def _normalise_int(value: Any) -> Any:
    """An integer answer, or a sentinel that can never equal one."""
    return value


def _normalise_pairs(value: Any) -> Any:
    """A solution set as a sorted list of two-string lists, or ``None``.

    Order is not graded — the statement says so — so both sides are sorted. A
    value that is not a list of two-string pairs normalises to ``None``, which
    equals no expected answer, so a plausible-looking wrong shape fails rather
    than crashing the grader.
    """
    if not isinstance(value, (list, tuple)):
        return None
    rows: list[list[str]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        first, second = item
        if not isinstance(first, str) or not isinstance(second, str):
            return None
        rows.append([first, second])
    return sorted(rows)


@dataclass(frozen=True)
class CodingProblem:
    """One graded coding problem: what to ask, what to run, and what is true.

    Fields
    ------
    id / rung:
        The identifier and its difficulty rung. The rungs exist because a rung
        everything passes measures nothing.
    entry:
        The exact function name the driver calls.
    statement:
        The problem as the mind receives it. It carries **no** graded answer;
        a test asserts that.
    cases:
        The graded argument lists, JSON-shaped so they survive the trip into
        the container unchanged.
    truth:
        The host-side reference. Never shipped into the container.
    value_type:
        The Python type name the driver must report. ``bool`` is not ``int``,
        and a subclass is not its base — that is what stops an object whose
        ``__eq__`` is always ``True``.
    reference_source:
        A committed correct solution, used as the positive control.
    """

    id: str
    rung: str
    entry: str
    statement: str
    cases: list[list[Any]]
    truth: Callable[..., Any]
    value_type: str
    reference_source: str
    normalise: Callable[[Any], Any] = _normalise_int

    def prompt_text(self) -> str:
        """The whole task, exactly as the mind under test receives it."""
        return f"{self.statement}\n{_PROTOCOL}"

    def expected(self) -> list[Any]:
        """Truth for every graded case, computed rather than tabulated."""
        return _expected(self.id)


@functools.lru_cache(maxsize=None)
def _expected_cached(problem_id: str) -> tuple[Any, ...]:
    problem = PROBLEMS[problem_id]
    return tuple(problem.truth(*case) for case in problem.cases)


def _expected(problem_id: str) -> list[Any]:
    return list(_expected_cached(problem_id))


PROBLEMS: dict[str, CodingProblem] = {
    "parity_subsets": CodingProblem(
        id="parity_subsets",
        rung="easy",
        entry="parity_subsets",
        statement=STATEMENT_PARITY,
        cases=[[0], [1], [2], [3], [5], [10], [20], [45], [90]],
        truth=truth_parity_subsets,
        value_type="int",
        reference_source=REFERENCE_PARITY,
    ),
    "preimage_count": CodingProblem(
        id="preimage_count",
        rung="medium",
        entry="preimage_count",
        statement=STATEMENT_PREIMAGE,
        cases=[
            ["ABCDE", 0],
            ["ABCDE", 1],
            ["CADEB", 0],
            ["BCDEA", 47],
            ["ABE", 200],
            ["C", 200],
            ["C", 100],
            ["D", 32],
            ["D", 0],
            ["CCC", 31],
            ["DCDC", 6],
            ["CCCCC", 3],
            ["AAAAA", 235],
            ["EBADC", 128],
        ],
        truth=truth_preimage_count,
        value_type="int",
        reference_source=REFERENCE_PREIMAGE,
    ),
    "register_recover": CodingProblem(
        id="register_recover",
        rung="hard",
        entry="register_recover",
        statement=STATEMENT_REGISTER,
        cases=[
            [4, RECORDS_4, 1, ["A", "D"], None],
            [8, RECORDS_8, 2, None, None],
            [8, RECORDS_8, 2, None, ["C", "A"]],
            [4, RECORDS_4, 1, None, ["C", "B"]],
            [4, [[1, "0011"], [3, "1000"], [5, "1000"]], 0, None, None],
            [4, [[1, "0000"], [3, "0000"], [5, "0000"]], 0, None, None],
            [8, RECORDS_8, 2, None, ["B", "A"]],
        ],
        truth=truth_register_recover,
        value_type="list",
        reference_source=REFERENCE_REGISTER,
        normalise=_normalise_pairs,
    ),
}

PROBLEM_ORDER = ("parity_subsets", "preimage_count", "register_recover")
RUNGS = ("easy", "medium", "hard")


# ══════════════════════════════════════════════════════════════════════════════
# extraction — the grader's fragile half, and so the paraphrase target
# ══════════════════════════════════════════════════════════════════════════════

_FENCE_RE = re.compile(
    r"^[ \t]*(?P<fence>```+|~~~+)[ \t]*(?P<info>[A-Za-z0-9_+-]*)[ \t]*\n"
    r"(?P<body>.*?)"
    r"^[ \t]*(?P=fence)[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

#: Where a bare, unfenced response plausibly starts being source.
_SOURCE_START_RE = re.compile(r"^(?:from |import |def |class |@|#!)", re.MULTILINE)


def extract_code(text: str, *, entry: str) -> Optional[str]:
    """The Python source in *text*, or ``None``.

    Paraphrase-tolerant on purpose: ``` and ``~~~`` fences, any or no language
    tag, prose on either side, several blocks, or no fence at all. Among fenced
    blocks the **last** one defining *entry* wins, then the last one defining
    anything, then the last block — because a model that corrects itself puts
    the good version second, and an earlier grader that took the first block
    graded abandoned attempts.

    A block defining some *other* name is still returned. Discarding it would
    turn a protocol slip into an unreadable ``NO_CODE`` and hide a real answer;
    the driver reports the missing name instead, which is a fact rather than a
    guess.
    """
    if not text:
        return None
    blocks = [match.group("body") for match in _FENCE_RE.finditer(text)]
    for predicate in (lambda b: f"def {entry}" in b, lambda b: "def " in b, lambda b: b.strip()):
        chosen = [block for block in blocks if predicate(block)]
        if chosen:
            return chosen[-1].strip("\n")
    match = _SOURCE_START_RE.search(text)
    if match and ("def " in text[match.start() :]):
        return text[match.start() :].strip("\n")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# the program: the model's source, then a driver that knows no answers
# ══════════════════════════════════════════════════════════════════════════════

#: The driver, appended to the model's source. Substituted with ``replace``
#: rather than ``format`` because it is full of braces.
#:
#: Three properties are load-bearing and each is asserted by test:
#:
#: * it carries the graded **inputs** and no expected value, so the container
#:   holds nothing an answer could be read off;
#: * it writes through ``sys.__stdout__``, so a model that rebinds ``print``
#:   cannot hide the result;
#: * it reports ``type(value).__name__`` and lets the host compare **JSON**, so
#:   an object whose ``__eq__`` is always ``True`` cannot pass by comparison.
DRIVER = """
import json as _j, sys as _s
_N = "__NONCE__"
_E = "__ENTRY__"
_C = _j.loads(__CASES__)
_fn = globals().get(_E)
if not callable(_fn):
    _payload = {"entry": _E, "outputs": None, "error": "no callable named " + _E}
else:
    _rows = []
    for _i, _a in enumerate(_C):
        _row = {"i": _i}
        try:
            _v = _fn(*_a)
        except BaseException as _x:
            _row["error"] = type(_x).__name__
            _rows.append(_row)
            continue
        _row["type"] = type(_v).__name__
        try:
            _j.dumps(_v)
        except BaseException:
            _row["unserialisable"] = True
        else:
            _row["value"] = _v
        _rows.append(_row)
    _payload = {"entry": _E, "outputs": _rows}
_s.__stdout__.write(_N + " " + _j.dumps(_payload, sort_keys=True) + "\\n")
_s.__stdout__.flush()
"""

#: Finds the driver's nonce assignment — anchored to the start of a line, and
#: the LAST match wins, so a model source that mentions ``_N = "…"`` in a string
#: (one committed fixture does exactly that, on purpose) cannot shadow it.
_NONCE_RE = re.compile(r'^_N = "([^"]*)"$', re.MULTILINE)


def mint_nonce() -> str:
    """A fresh per-run nonce. Unguessable, so stdout can never be mistaken for it."""
    return secrets.token_hex(8)


def nonce_of(program: str) -> Optional[str]:
    """The nonce a built program carries, read the way the container would."""
    found = _NONCE_RE.findall(program or "")
    return found[-1] if found else None


def build_program(problem: CodingProblem, code: str, nonce: str) -> str:
    """The model's source followed by the driver. The only thing ever executed."""
    driver = (
        DRIVER.replace("__NONCE__", nonce)
        .replace("__ENTRY__", problem.entry)
        .replace("__CASES__", repr(json.dumps(problem.cases)))
    )
    return code.rstrip("\n") + "\n" + driver


def run_in_workspace(program: str, workspace: MuseWorkspace) -> str:
    """**The only execution path in this harness.**

    One argv, handed to the workspace tool: no policy, no mount, no
    environment, no host path. Everything else about the container's reach is
    :mod:`embodiment.workspace`'s property and is proved there.
    """
    return str(workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["python3", "-c", program]}))


def read_result_lines(text: str, nonce: str) -> list[str]:
    """Every line of container output that claims to be the driver's result."""
    prefix = nonce + " "
    return [line for line in (text or "").splitlines() if line.startswith(prefix)]


# ══════════════════════════════════════════════════════════════════════════════
# grading
# ══════════════════════════════════════════════════════════════════════════════


def grade_payload(problem: CodingProblem, payload: Optional[dict]) -> dict[str, Any]:
    """Compare what the container returned against host-side truth.

    Pure, and the only place a verdict about correctness is formed. Three gates
    per case, in order, because each catches something the next cannot:

    1. the case raised — recorded by exception type, never scored;
    2. the returned type is not the declared one — a ``bool`` is not an ``int``
       and an object with a friendly ``__eq__`` is not either;
    3. the normalised value differs from truth.
    """
    expected = problem.expected()
    rows: list[dict[str, Any]] = []
    by_index = {}
    for row in (payload or {}).get("outputs") or []:
        if isinstance(row, dict) and isinstance(row.get("i"), int):
            by_index[row["i"]] = row

    for index, want in enumerate(expected):
        raw = by_index.get(index) or {}
        record: dict[str, Any] = {
            "i": index,
            "case": problem.cases[index],
            "expected": problem.normalise(want),
            "type": raw.get("type"),
            "error": raw.get("error"),
            "got": None,
            "ok": False,
        }
        if raw.get("error"):
            rows.append(record)
            continue
        if raw.get("unserialisable"):
            record["error"] = "unserialisable"
            rows.append(record)
            continue
        if "value" not in raw:
            record["error"] = record["error"] or "missing"
            rows.append(record)
            continue
        record["got"] = problem.normalise(raw["value"])
        if raw.get("type") != problem.value_type:
            rows.append(record)
            continue
        record["ok"] = record["got"] is not None and record["got"] == record["expected"]
        rows.append(record)

    return {
        "cases": rows,
        "passed": sum(1 for row in rows if row["ok"]),
        "total": len(rows),
    }


def grade_response(
    problem: CodingProblem,
    response_text: str,
    *,
    workspace: MuseWorkspace,
    nonce: Optional[str] = None,
) -> dict[str, Any]:
    """Grade one model response end to end. Never raises for a container problem.

    The raw text is recorded **first and unconditionally** (M2 requirement d):
    a run that produced no code is precisely the one worth re-reading later, and
    last cycle a grader could re-grade 3 of 18 runs because the series stored
    verdicts and not responses.
    """
    nonce = nonce or mint_nonce()
    record: dict[str, Any] = {
        "problem": problem.id,
        "rung": problem.rung,
        "raw_response": response_text,
        "nonce": nonce,
        "code": None,
        "verdict": VERDICT_NO_CODE,
        "cases": [],
        "passed": 0,
        "total": len(problem.cases),
        "payload": None,
        "workspace_output": "",
        "spoof_suspected": False,
        "degradations": [],
        "vacuity": {
            "workspace_runs": 0,
            "nonce_matches": 0,
            "rows_returned": None,
            "cases_expected": len(problem.cases),
            "fired": False,
        },
    }

    code = extract_code(response_text, entry=problem.entry)
    if code is None:
        return record
    record["code"] = code

    # Deltas, not totals: a live lane grades many fixtures through ONE
    # workspace, and a running total would let fixture 2 inherit fixture 1's
    # evidence that something ran.
    runs_before = workspace.counts().runs
    degradations_before = len(workspace.degradations)

    program = build_program(problem, code, nonce)
    output = run_in_workspace(program, workspace)

    record["workspace_output"] = output
    record["degradations"] = [d.to_dict() for d in workspace.degradations[degradations_before:]]
    runs = workspace.counts().runs - runs_before
    lines = read_result_lines(output, nonce)

    payload: Optional[dict] = None
    if len(lines) == 1:
        try:
            candidate = json.loads(lines[0][len(nonce) + 1 :])
        except (TypeError, ValueError):
            candidate = None
        payload = candidate if isinstance(candidate, dict) else None
    record["payload"] = payload
    record["spoof_suspected"] = len(lines) > 1

    outputs = (payload or {}).get("outputs")
    rows_returned = len(outputs) if isinstance(outputs, list) else None
    record["vacuity"] = {
        "workspace_runs": runs,
        "nonce_matches": len(lines),
        "rows_returned": rows_returned,
        "cases_expected": len(problem.cases),
        "fired": bool(runs >= 1 and len(lines) == 1 and rows_returned == len(problem.cases)),
    }

    graded = grade_payload(problem, payload)
    record["cases"] = graded["cases"]
    record["passed"] = graded["passed"]
    record["total"] = graded["total"]

    if runs < 1:
        record["verdict"] = VERDICT_NO_WORKSPACE
    elif not record["vacuity"]["fired"]:
        record["verdict"] = VERDICT_NO_RESULT
    elif record["passed"] == record["total"]:
        record["verdict"] = VERDICT_CORRECT
    else:
        record["verdict"] = VERDICT_WRONG
    return record


_DIGITS_RE = re.compile(r"\d")


def retry_message(problem: CodingProblem, record: dict[str, Any]) -> str:
    """Feedback for a second attempt, carrying **no** value the grader knows.

    The temptation is to say *case 3 returned 72, expected 76*, and that is a
    training signal for the checker rather than for the problem. So the message
    names the failure *shapes* — raised, wrong type, wrong value, no result at
    all — and every digit is stripped on the way out, which makes "no expected
    value leaks" a structural property of the function rather than a promise
    about how carefully it was worded.
    """
    verdict = record.get("verdict")
    if verdict == VERDICT_NO_CODE:
        body = "No Python code block was found in your reply."
    elif verdict == VERDICT_NO_WORKSPACE:
        body = "The sandbox was unavailable, so your code was never run."
    elif verdict == VERDICT_NO_RESULT:
        body = (
            "Your code ran but no complete result came back. Something ended the "
            "process, or wrote to standard output, before every case was called."
        )
    else:
        shapes: list[str] = []
        errors = sorted({row["error"] for row in record["cases"] if row.get("error")})
        if errors:
            shapes.append("raised " + ", ".join(errors))
        if any(row.get("type") and row["type"] != problem.value_type for row in record["cases"]):
            shapes.append(f"returned something that is not a {problem.value_type}")
        if any(not row["ok"] and not row.get("error") for row in record["cases"]):
            shapes.append("returned the wrong value")
        body = "Some graded cases failed: " + "; ".join(shapes or ["reason unavailable"]) + "."
    text = (
        f"{body}\n\nRevise {problem.entry} and reply with the complete corrected "
        "source in a single Python code block."
    )
    return _DIGITS_RE.sub("", text)


# ══════════════════════════════════════════════════════════════════════════════
# the committed fixtures — the M2 kit's evidence
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Fixture:
    """One committed grader input and the container output it really produced.

    ``stdout`` is the captured output a **real docker workspace** printed for
    ``source``, with the per-run nonce written as ``{nonce}``. That is what lets
    the always-on tests grade the shipped grader without executing anything: the
    execution is scripted, every judgement is the real one. The live lane
    (``EMBODIMENT_LIVE_RIG=1``, or ``--fixtures --provider docker``) re-runs each
    one for real and checks this claim.
    """

    name: str
    problem: str
    kind: str
    why: str
    source: str
    stdout: str
    expects: str
    prose: str = ""
    live_skip: str = ""

    @property
    def response(self) -> str:
        """The fixture as a model reply — the shape the grader actually receives."""
        if self.prose:
            return self.prose.replace("{source}", self.source)
        return f"```python\n{self.source}\n```"

    def payload(self) -> Optional[dict]:
        """The result payload this fixture's stdout carries, or ``None``."""
        lines = read_result_lines(self.stdout.replace("{nonce}", "N"), "N")
        if len(lines) != 1:
            return None
        try:
            parsed = json.loads(lines[0][2:])
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None


def _rows(*values: Any) -> str:
    """The outputs section a driver prints for a run that returned *values*."""
    return json.dumps(
        [{"i": i, "type": type(v).__name__, "value": v} for i, v in enumerate(values)],
        sort_keys=True,
    )


def _unserialisable_rows(type_name: str, count: int) -> str:
    """The outputs section for a run whose return value would not encode."""
    return json.dumps(
        [{"i": i, "type": type_name, "unserialisable": True} for i in range(count)],
        sort_keys=True,
    )


def _stdout(entry: str, outputs: str) -> str:
    return '{nonce} {"entry": "' + entry + '", "outputs": ' + outputs + "}\n"


#: What the reference register solver really returned, in the order it returned
#: it — **unsorted**, because the statement says order does not matter and
#: :func:`_normalise_pairs` is what makes that true. Committing the sorted form
#: instead would quietly pin an ordering the grader is supposed to ignore.
_REGISTER_REFERENCE_VALUES: list[list[list[str]]] = [
    [["0101", "CBEAD"]],
    [
        ["11001000", "ADCEB"],
        ["01010100", "ADECB"],
        ["01010110", "ADECB"],
        ["01100000", "ADECB"],
        ["01010100", "AEDCB"],
        ["01010110", "AEDCB"],
        ["10110100", "AEDCB"],
        ["10110110", "AEDCB"],
        ["00001111", "CAEDB"],
        ["10000111", "CDBAE"],
        ["10001011", "CDBEA"],
        ["11010011", "DCEAB"],
        ["11110011", "DCEAB"],
        ["11010011", "DCEBA"],
        ["11110011", "DCEBA"],
        ["11001101", "EDCBA"],
        ["11111101", "EDCBA"],
    ],
    [["00001111", "CAEDB"]],
    [["1001", "CBDAE"], ["0011", "CBDEA"], ["0101", "CBEAD"], ["0101", "CBEDA"]],
    [["0000", "ABDCE"]],
    [],
    [
        ["10000111", "CDBAE"],
        ["11010011", "DCEBA"],
        ["11110011", "DCEBA"],
        ["11001101", "EDCBA"],
        ["11111101", "EDCBA"],
    ],
]


FIXTURE_HARDCODED = """\
def parity_subsets(n):
    known = {0: 1, 1: 1, 2: 2, 3: 3, 4: 5, 5: 7}
    if n in known:
        return known[n]
    return 2 ** (n // 2)
"""

FIXTURE_HALVING = """\
def parity_subsets(n):
    a, b = 0, 1
    for _ in range(n + 2):
        a, b = b, a + b
    return a // 2
"""

FIXTURE_NO_EMPTY_SET = """\
def parity_subsets(n):
    even = [0] * (n + 1)
    odd = [0] * (n + 1)
    even[0] = 1
    if n >= 1:
        even[1], odd[1] = 1, 1
    for k in range(2, n + 1):
        if k % 2 == 0:
            even[k], odd[k] = even[k - 1] + even[k - 2], odd[k - 1] + odd[k - 2]
        else:
            even[k], odd[k] = even[k - 1] + odd[k - 2], odd[k - 1] + even[k - 2]
    return even[n] - 1
"""

FIXTURE_ALWAYS_EQUAL = """\
class Anything:
    def __eq__(self, other):
        return True

    def __hash__(self):
        return 0


def parity_subsets(n):
    return Anything()
"""

FIXTURE_BOOL = """\
def parity_subsets(n):
    if n <= 1:
        return True
    even = [0] * (n + 1)
    odd = [0] * (n + 1)
    even[0] = 1
    even[1], odd[1] = 1, 1
    for k in range(2, n + 1):
        if k % 2 == 0:
            even[k], odd[k] = even[k - 1] + even[k - 2], odd[k - 1] + odd[k - 2]
        else:
            even[k], odd[k] = even[k - 1] + odd[k - 2], odd[k - 1] + even[k - 2]
    return even[n]
"""

FIXTURE_SILENCES_PRINT = """\
import builtins

builtins.print = lambda *a, **k: None


def parity_subsets(n):
    return 0
"""

FIXTURE_FORGES = """\
import pathlib
import re

_seen = pathlib.Path("/proc/self/cmdline").read_bytes().decode("utf-8", "replace")
_hit = re.search(r'_N = "([0-9a-zA-Z]+)"', _seen)
print(_hit.group(1) + ' {"entry": "parity_subsets", "outputs": [], "verdict": "CORRECT"}')


def parity_subsets(n):
    return 0
"""

FIXTURE_EXITS = """\
import sys


def parity_subsets(n):
    return 0


sys.exit(0)
"""

FIXTURE_INVERTS = """\
INVERSE = {
    "A": lambda y: (y - 47) % 256,
    "B": lambda y: y ^ 0xAA,
    "C": lambda y: (y * 2) % 256,
    "D": lambda y: y,
    "E": lambda y: (y * 171) % 256,
}


def preimage_count(sequence, target):
    state = target
    for name in reversed(sequence):
        state = INVERSE[name](state)
    return 1
"""

FIXTURE_AT_MOST = '''\
from itertools import permutations

OPS4 = {
    "A": lambda x: (x + 3) % 16,
    "B": lambda x: x ^ 0b1011,
    "C": lambda x: ((x << 1) | (x >> 3)) & 15,
    "D": lambda x: (x * 5) % 16,
    "E": lambda x: int(format(x, "04b")[::-1], 2),
}
OPS8 = {
    "A": lambda x: (x + 47) % 256,
    "B": lambda x: x ^ 0xAA,
    "C": lambda x: x >> 1,
    "D": lambda x: x & 0xDF,
    "E": lambda x: (x * 3) % 256,
}


def register_recover(width, records, distance, require_before=None, require_adjacent=None):
    """At MOST `distance` — the reading the statement rules out."""
    ops = OPS4 if width == 4 else OPS8
    mask = (1 << width) - 1
    wanted = {int(pos): int(bits, 2) for pos, bits in records}
    out = []
    for perm in permutations("ABCDE"):
        order = "".join(perm)
        if require_before and order.index(require_before[0]) > order.index(require_before[1]):
            continue
        if require_adjacent and "".join(require_adjacent) not in order:
            continue
        for start in range(1 << width):
            state, ok = start, True
            for step, name in enumerate(order, start=1):
                if width == 8 and name == "C" and state % 2 == 0:
                    ok = False
                    break
                state = ops[name](state)
                if step in wanted and bin((state ^ wanted[step]) & mask).count("1") > distance:
                    ok = False
                    break
            if ok:
                out.append([format(start, "0%db" % width), order])
    return out
'''

#: What the at-most-d solver really returned, as ``initial:order`` tokens, one
#: graded case per entry, in the order it returned them. Compact on purpose —
#: the equivalent JSON is 2.7 kB of literal — and never re-derived: this is the
#: captured output of a real container run, which is the whole point of
#: committing it. Note case 0: **twenty-four** solutions where the exact reading
#: has one.
_AT_MOST_RETURNED = (
    (  # case 0
        "1101:ACDBE 1111:ACDBE 1101:ACDEB 1101:ACEBD 1111:ACEBD 1101:ACEDB 0111:ADCBE "
        "1101:ADCBE 1101:ADCEB 0111:ADEBC 1101:ADEBC 1101:ADECB 1101:AECBD 1111:AECBD "
        "1101:AECDB 1101:AEDBC 1111:AEDBC 1101:AEDCB 1011:BCEAD 1001:BECAD 1011:BECAD "
        "0101:CBEAD 0101:CEABD 0101:ECABD"
    ),
    (  # case 1
        "10010100:ACEDB 10101000:ADCEB 11001000:ADCEB 01010100:ADECB 01010110:ADECB "
        "01100000:ADECB 10011000:AECDB 01010100:AEDCB 01010110:AEDCB 10110100:AEDCB "
        "10110110:AEDCB 00001111:CAEDB 10000111:CBEAD 10000111:CBEDA 10000111:CDBAE "
        "10000111:CDBEA 10001011:CDBEA 11000110:DBAEC 11100110:DBAEC 00000111:DCBAE "
        "00100111:DCBAE 01000111:DCBAE 01100111:DCBAE 10000111:DCBAE 10100111:DCBAE "
        "11000011:DCBAE 11100011:DCBAE 01000111:DCBEA 01100111:DCBEA 10000011:DCBEA "
        "10000111:DCBEA 10100011:DCBEA 10100111:DCBEA 11000011:DCEAB 11010011:DCEAB "
        "11100011:DCEAB 11110011:DCEAB 11000011:DCEBA 11010011:DCEBA 11100011:DCEBA "
        "11110011:DCEBA 11000011:DEBCA 11100011:DEBCA 01001101:ECADB 10101101:ECBAD "
        "00101101:ECBDA 01000001:ECBDA 01101101:ECBDA 10000001:ECBDA 10101101:ECBDA "
        "00011101:EDCAB 01101101:EDCAB 11001101:EDCAB 01101101:EDCBA 10011101:EDCBA "
        "11001101:EDCBA 11111101:EDCBA"
    ),
    (  # case 2
        "00001111:CAEDB 11000011:DEBCA 11100011:DEBCA 01001101:ECADB 00011101:EDCAB "
        "01101101:EDCAB 11001101:EDCAB"
    ),
    (  # case 3
        "0111:ADCBE 1101:ADCBE 1101:ADECB 1101:AECBD 1111:AECBD 1101:AEDCB 1001:CBDAE "
        "0011:CBDEA 0101:CBEAD 0101:CBEDA 0000:DECBA 1100:EDACB 0000:EDCBA"
    ),
    "0000:ABDCE",  # case 4
    "",  # case 5
    (  # case 6
        "10000111:CDBAE 11000110:DBAEC 11100110:DBAEC 00000111:DCBAE 00100111:DCBAE "
        "01000111:DCBAE 01100111:DCBAE 10000111:DCBAE 10100111:DCBAE 11000011:DCBAE "
        "11100011:DCBAE 11000011:DCEBA 11010011:DCEBA 11100011:DCEBA 11110011:DCEBA "
        "10101101:ECBAD 01101101:EDCBA 10011101:EDCBA 11001101:EDCBA 11111101:EDCBA"
    ),
)


def _pairs(tokens: str) -> list[list[str]]:
    """``"0101:CBEAD 0011:CBDEA"`` as the list of pairs a driver serialised."""
    return [token.split(":") for token in tokens.split()]


_AT_MOST_VALUES = [_pairs(entry) for entry in _AT_MOST_RETURNED]


FIXTURE_MEMOISED = '''\
from functools import lru_cache


@lru_cache(maxsize=None)
def _counts(limit):
    """(even, odd) over {1..limit}, reached by recursion rather than a loop."""
    if limit == 0:
        return (1, 0)
    if limit == 1:
        return (1, 1)
    near_even, near_odd = _counts(limit - 1)
    far_even, far_odd = _counts(limit - 2)
    if limit % 2 == 0:
        return (near_even + far_even, near_odd + far_odd)
    return (near_even + far_odd, near_odd + far_even)


def parity_subsets(n: int) -> int:
    import sys

    sys.setrecursionlimit(10000)
    return _counts(n)[0]


if __name__ == "__main__":
    pass
'''

FIXTURE_SENTINEL = """\
import pathlib

pathlib.Path("/tmp/embodiment-t7-escaped").write_text("escaped")


def parity_subsets(n):
    return 1
"""

#: A wrapper that makes a fixture a paraphrase case as well: prose on both
#: sides, an abandoned first block, and a tilde fence around the real answer.
_PARAPHRASE_WRAPPER = (
    "Let me think. Halving the total is wrong because the parity classes are "
    "unequal.\n\n"
    "```python\ndef parity_subsets(n):\n    return 0\n```\n\n"
    "No — that was a placeholder. Here is the real one:\n\n"
    "~~~PYTHON\n{source}\n~~~\n\n"
    "It runs in linear time and handles the empty set."
)


FIXTURES: tuple[Fixture, ...] = (
    # ── positives: without these the table below is satisfied by a grader that
    #    fails everything, which is the cheapest possible defect.
    Fixture(
        name="reference_parity",
        problem="parity_subsets",
        kind=KIND_CORRECT,
        why="the positive control for the easy rung: the committed reference solution",
        source=REFERENCE_PARITY,
        stdout=_stdout(
            "parity_subsets",
            _rows(1, 1, 2, 3, 7, 76, 8900, 1485616392, 3770056903291329166),
        ),
        expects=VERDICT_CORRECT,
    ),
    Fixture(
        name="reference_preimage",
        problem="preimage_count",
        kind=KIND_CORRECT,
        why="the positive control for the medium rung: the committed reference solution",
        source=REFERENCE_PREIMAGE,
        stdout=_stdout("preimage_count", _rows(4, 0, 4, 4, 1, 0, 2, 0, 2, 8, 16, 32, 1, 0)),
        expects=VERDICT_CORRECT,
    ),
    Fixture(
        name="reference_register",
        problem="register_recover",
        kind=KIND_CORRECT,
        why="the positive control for the hard rung: the committed reference solution",
        source=REFERENCE_REGISTER,
        stdout=_stdout("register_recover", _rows(*_REGISTER_REFERENCE_VALUES)),
        expects=VERDICT_CORRECT,
    ),
    Fixture(
        name="memoised_recursion",
        problem="parity_subsets",
        kind=KIND_PARAPHRASE,
        why=(
            "the same correct answer in a structurally different implementation, "
            "wrapped in prose with an abandoned first block and a tilde fence — "
            "the wording the grader was NOT written against"
        ),
        source=FIXTURE_MEMOISED,
        stdout=_stdout(
            "parity_subsets",
            _rows(1, 1, 2, 3, 7, 76, 8900, 1485616392, 3770056903291329166),
        ),
        expects=VERDICT_CORRECT,
        prose=_PARAPHRASE_WRAPPER,
    ),
    # ── adversaries: built to score well while being wrong.
    Fixture(
        name="hardcoded_statement_values",
        problem="parity_subsets",
        kind=KIND_ADVERSARIAL,
        why=(
            "memorises the small values any mind can enumerate by hand and guesses "
            "beyond them — it passes a majority of cases and understands nothing, "
            "which is exactly the shape 'cp-west' in plan scored as a hit"
        ),
        source=FIXTURE_HARDCODED,
        stdout=_stdout("parity_subsets", _rows(1, 1, 2, 3, 7, 32, 1024, 4194304, 35184372088832)),
        expects=VERDICT_WRONG,
    ),
    Fixture(
        name="halving_trap",
        problem="parity_subsets",
        kind=KIND_ADVERSARIAL,
        why=(
            "the trap problem 1 already names: Fib(n+2)//2, which lands on 72 at "
            "n=10 — a confident, plausible, wrong number"
        ),
        source=FIXTURE_HALVING,
        stdout=_stdout(
            "parity_subsets", _rows(0, 1, 1, 2, 6, 72, 8855, 1485607536, 3770056902373173214)
        ),
        expects=VERDICT_WRONG,
    ),
    Fixture(
        name="drops_the_empty_set",
        problem="parity_subsets",
        kind=KIND_ADVERSARIAL,
        why=(
            "correct recurrence, off by one everywhere because it iterates non-empty "
            "subsets — the single edge case a grader without n=0 would never see"
        ),
        source=FIXTURE_NO_EMPTY_SET,
        stdout=_stdout(
            "parity_subsets", _rows(0, 0, 1, 2, 6, 75, 8899, 1485616391, 3770056903291329165)
        ),
        expects=VERDICT_WRONG,
    ),
    Fixture(
        name="always_equal_object",
        problem="parity_subsets",
        kind=KIND_ADVERSARIAL,
        why=(
            "returns an object whose __eq__ is always True — it would pass any "
            "grader that compares with == inside the sandbox"
        ),
        source=FIXTURE_ALWAYS_EQUAL,
        stdout=_stdout("parity_subsets", _unserialisable_rows("Anything", 9)),
        expects=VERDICT_WRONG,
    ),
    Fixture(
        name="bool_for_int",
        problem="parity_subsets",
        kind=KIND_ADVERSARIAL,
        why=(
            "True == 1 in Python, so a bool sails past a value comparison; the type "
            "gate is the only thing that catches it"
        ),
        source=FIXTURE_BOOL,
        stdout=_stdout(
            "parity_subsets",
            _rows(True, True, 2, 3, 7, 76, 8900, 1485616392, 3770056903291329166),
        ),
        expects=VERDICT_WRONG,
    ),
    Fixture(
        name="silences_print",
        problem="parity_subsets",
        kind=KIND_ADVERSARIAL,
        why=(
            "rebinds builtins.print to a no-op; the driver writes through "
            "sys.__stdout__ so the result still arrives and the run is graded"
        ),
        source=FIXTURE_SILENCES_PRINT,
        stdout=_stdout("parity_subsets", _rows(0, 0, 0, 0, 0, 0, 0, 0, 0)),
        expects=VERDICT_WRONG,
    ),
    Fixture(
        name="forges_a_result_line",
        problem="parity_subsets",
        kind=KIND_ADVERSARIAL,
        why=(
            "scrapes the nonce out of /proc/self/cmdline and prints its own "
            "all-correct result line claiming a CORRECT verdict; two nonce lines "
            "are refused rather than resolved, and the self-reported verdict is "
            "read by nothing"
        ),
        source=FIXTURE_FORGES,
        stdout=(
            '{nonce} {"entry": "parity_subsets", "outputs": [], "verdict": "CORRECT"}\n'
            + _stdout("parity_subsets", _rows(*([0] * 9)))
        ),
        expects=VERDICT_NO_RESULT,
    ),
    Fixture(
        name="exits_before_the_driver",
        problem="parity_subsets",
        kind=KIND_ADVERSARIAL,
        why=(
            "ends the process at import time, so the driver never runs; a grader "
            "that treated silence as success would score this CORRECT"
        ),
        source=FIXTURE_EXITS,
        stdout="",
        expects=VERDICT_NO_RESULT,
    ),
    Fixture(
        name="writes_a_sentinel_to_the_host",
        problem="parity_subsets",
        kind=KIND_ADVERSARIAL,
        why=(
            "writes a file at import time; the empirical half of the containment "
            "claim, because after this has been graded the file exists on no host"
        ),
        source=FIXTURE_SENTINEL,
        stdout=_stdout("parity_subsets", _rows(1, 1, 1, 1, 1, 1, 1, 1, 1)),
        expects=VERDICT_WRONG,
    ),
    Fixture(
        name="inverts_the_lossy_routines",
        problem="preimage_count",
        kind=KIND_ADVERSARIAL,
        why=(
            "assumes all five routines are invertible — true of three of them — and "
            "returns 1 every time; right on the all-bijection sequences and wrong "
            "wherever C or D appears"
        ),
        source=FIXTURE_INVERTS,
        stdout=_stdout("preimage_count", _rows(1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1)),
        expects=VERDICT_WRONG,
    ),
    Fixture(
        name="at_most_hamming",
        problem="register_recover",
        kind=KIND_ADVERSARIAL,
        why=(
            "reads 'exactly d' as 'at most d' — the single word the statement "
            "emphasises, and the reading that turns a unique answer into a set"
        ),
        source=FIXTURE_AT_MOST,
        stdout=_stdout("register_recover", _rows(*_AT_MOST_VALUES)),
        expects=VERDICT_WRONG,
    ),
)


def fixture(name: str) -> Fixture:
    """One committed fixture by name."""
    for item in FIXTURES:
        if item.name == name:
            return item
    raise KeyError(f"no fixture named {name!r}; have {[f.name for f in FIXTURES]}")


# ══════════════════════════════════════════════════════════════════════════════
# driving a model
# ══════════════════════════════════════════════════════════════════════════════


class Transcript:
    """The committed raw record, rewritten after **every** run (M2 requirement d).

    Not appended at the end: a transient gateway failure 41 minutes into a live
    series once destroyed every turn already recorded, because the transcript
    was written only after the last run completed.
    """

    def __init__(self, path: Any, config: Optional[dict] = None) -> None:
        self.path = Path(path).expanduser()
        self.config = dict(config or {})
        self.runs: list[dict[str, Any]] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._flush()

    def append(self, record: dict[str, Any]) -> None:
        self.runs.append(record)
        self._flush()

    def _flush(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"config": self.config, "runs": self.runs}, handle, indent=2)
            handle.write("\n")


def solve_once(
    complete: Callable[[list[dict[str, Any]]], ModelResponse],
    problem: CodingProblem,
    *,
    workspace: MuseWorkspace,
    attempts: int = 1,
) -> dict[str, Any]:
    """One mind, one problem, up to *attempts* submissions.

    Every attempt is recorded — not only the last — because a run that was wrong
    and then right is a different fact from one that was right immediately, and
    a series that keeps only the final verdict cannot tell them apart. Retry
    feedback goes through :func:`retry_message`, which carries no value the
    grader knows.
    """
    messages = [{"role": "user", "content": problem.prompt_text()}]
    records: list[dict[str, Any]] = []
    for _ in range(max(1, attempts)):
        response = complete(messages)
        text = (response.content or "").strip()
        record = grade_response(problem, text, workspace=workspace)
        records.append(record)
        if record["verdict"] == VERDICT_CORRECT:
            break
        messages = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": retry_message(problem, record)},
        ]
    last = records[-1]
    return {
        "problem": problem.id,
        "rung": problem.rung,
        "verdict": last["verdict"],
        "passed": last["passed"],
        "total": last["total"],
        "attempts": records,
    }


def gateway(
    base_url: str,
    model: str,
    key: str,
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    trace: Optional[list[dict[str, Any]]] = None,
) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """One tools-off completion against the gateway.

    Tools-off on purpose: this rung's instrument is *the program the mind
    writes*, and a tool loop would put a second, uncontrolled variable between
    the prompt and the source. ``trace`` collects ``finish_reason`` per call,
    which :class:`~embodiment.contract.ModelResponse` discards (#37) — a turn
    truncated by the token cap and a mind that simply stopped arrive as the
    same object otherwise.
    """
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    if not endpoint.startswith(("http://", "https://")):
        raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(request, timeout=900) as response:  # nosec B310
            payload = json.load(response)
        choice = payload["choices"][0]
        message = choice["message"]
        answer = ModelResponse(
            content=message.get("content") or "",
            reasoning=message.get("reasoning") or "",
        )
        if trace is not None:
            usage = payload.get("usage") or {}
            trace.append(
                {
                    "finish_reason": choice.get("finish_reason"),
                    "content": answer.content,
                    "reasoning": answer.reasoning,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "max_tokens": max_tokens,
                }
            )
        return answer

    return complete


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════


def selected_problems(problem: str, rung: str) -> list[CodingProblem]:
    """The problems a ``--problem`` / ``--rung`` pair names, in rung order."""
    chosen = [PROBLEMS[name] for name in PROBLEM_ORDER]
    if problem != "all":
        chosen = [item for item in chosen if item.id == problem]
    if rung != "all":
        chosen = [item for item in chosen if item.rung == rung]
    return chosen


def _run_fixtures(workspace: MuseWorkspace, as_json: bool) -> int:
    """Grade every committed fixture, reporting where reality and the table differ.

    With ``--provider docker`` this is the live half of the M2 kit: it is how
    the committed ``stdout`` values were captured, and how a reader checks they
    are still true.
    """
    rows: list[dict[str, Any]] = []
    for item in FIXTURES:
        if item.live_skip:
            rows.append({"fixture": item.name, "verdict": "SKIPPED", "why": item.live_skip})
            continue
        record = grade_response(PROBLEMS[item.problem], item.response, workspace=workspace)
        rows.append(
            {
                "fixture": item.name,
                "kind": item.kind,
                "expects": item.expects,
                "verdict": record["verdict"],
                "agrees": record["verdict"] == item.expects,
                "passed": record["passed"],
                "total": record["total"],
                "captured": record["workspace_output"],
                "payload": record["payload"],
            }
        )
        if not as_json:
            mark = "ok " if rows[-1]["agrees"] else "DIFF"
            print(f"{mark} {item.name:32s} {record['verdict']} (expected {item.expects})")
    if as_json:
        print(json.dumps({"fixtures": rows}, indent=2))
    return 0 if all(row.get("agrees", True) for row in rows) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="The coding rung: three graded problems.")
    parser.add_argument("--problem", default="all", choices=("all",) + PROBLEM_ORDER)
    parser.add_argument("--rung", default="all", choices=("all",) + RUNGS)
    parser.add_argument("--n", type=int, default=1, help="runs per problem")
    parser.add_argument("--attempts", type=int, default=1, help="submissions per run")
    parser.add_argument(
        "--provider", default=PROVIDER_DOCKER, choices=(PROVIDER_DOCKER, PROVIDER_FAKE)
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_CORTEX)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--results", default="results/coding_config.json")
    parser.add_argument("--trace-out", default=None, help="the committed raw transcript")
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="grade the committed fixtures instead of calling a model",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    workspace = MuseWorkspace(provider=args.provider, max_result_chars=0)
    try:
        if args.fixtures:
            return _run_fixtures(workspace, args.json)

        key = os.environ.get("COLLEAGUE_API_KEY", "")
        if not key:
            print("error: COLLEAGUE_API_KEY is not set", file=sys.stderr)
            print("hint: export COLLEAGUE_API_KEY, or pass --fixtures", file=sys.stderr)
            return 1

        problems = selected_problems(args.problem, args.rung)
        results_path = Path(args.results).expanduser()
        results_path.parent.mkdir(parents=True, exist_ok=True)
        config = write_config_preamble(
            str(results_path),
            cortex_model=args.model,
            cortex_temperature=args.temperature,
            n=args.n,
            extra={
                "max_tokens": args.max_tokens,
                "attempts": args.attempts,
                "provider": args.provider,
                "problems": [item.id for item in problems],
                "harness": "examples/challenge_coding.py",
            },
        )
        transcript = Transcript(args.trace_out, config) if args.trace_out else None

        turns: list[dict[str, Any]] = []
        complete = gateway(
            args.base_url,
            args.model,
            key,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            trace=turns,
        )

        results: list[dict[str, Any]] = []
        for problem in problems:
            for index in range(args.n):
                seen = len(turns)
                try:
                    record = solve_once(
                        complete, problem, workspace=workspace, attempts=args.attempts
                    )
                except Exception as exc:  # noqa: BLE001 — an infrastructure event is DATA
                    record = {
                        "problem": problem.id,
                        "rung": problem.rung,
                        "verdict": "ABORTED",
                        "error": f"{type(exc).__name__}: {exc}",
                        "attempts": [],
                    }
                record["run"] = index
                record["turns"] = turns[seen:]
                results.append(record)
                if transcript is not None:
                    transcript.append(record)
                if not args.json:
                    print(f"{problem.id} run {index + 1}: {record['verdict']}")

        if args.json:
            print(json.dumps({"config": config, "results": results}, indent=2))
        return 0
    finally:
        workspace.close()


if __name__ == "__main__":
    raise SystemExit(main())
