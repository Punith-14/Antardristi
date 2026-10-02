"""
"What is it doing now?" - progress steps for a background job.

The pipeline calls step("Detecting water") at each stage. Inside a job the
step is recorded and the website shows it; outside one (a direct API call, a
test, a notebook) it does nothing. A context variable carries the job, so the
pipeline never has to be told whether it is running in one.
"""

from contextvars import ContextVar

_reporter = ContextVar("antardrishti_progress", default=None)


def step(text):
    reporter = _reporter.get()
    if reporter is not None:
        try:
            reporter(text)
        except Exception:                        # noqa: BLE001 - progress must never break work
            pass


def bind(reporter):
    """Set the reporter for the current context. Returns a token for reset."""
    return _reporter.set(reporter)


def unbind(token):
    _reporter.reset(token)
