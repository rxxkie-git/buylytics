"""Moved to ``tests/check_webhooks.py``.

This file was a scratch checker that had to sit beside the ``app`` package because
it resolved imports from the working directory instead of from its own location.
The real version now lives in ``tests/`` and bootstraps ``sys.path`` from
``__file__``, so it runs from anywhere:

    python3 tests/check_webhooks.py

The placeholder remains only because this environment cannot delete files. Deleting
it is safe and expected.
"""

print(__doc__)
