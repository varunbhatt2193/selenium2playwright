"""One place that makes rich's output the same everywhere it is asserted on.

Typer prints usage errors through rich, which picks colour and width from the
environment — and rich deliberately treats GitHub Actions as a colour-capable
terminal so that logs come out in colour. The first CI run turned four passing
assertions red because of it: the sentence was there, wrapped at a different
column and interrupted by escape codes.

Step 9.1 already pinned `cli.console`'s width for exactly this reason. Typer's
usage-error panel is a *different* console, built per error from the
environment, so pinning ours was never going to reach it. This does.

Not named test_*.py on purpose: it holds no tests and unittest must not collect it.
"""

from __future__ import annotations

import os
from unittest.mock import patch


def deterministic_console():
    """A patcher: no colour, a fixed width, nothing that varies by who runs it."""
    return patch.dict(os.environ, {"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "100"})


def pin_console(case) -> None:
    """Start that patcher for one test case and undo it afterwards."""
    patcher = deterministic_console()
    patcher.start()
    case.addCleanup(patcher.stop)
