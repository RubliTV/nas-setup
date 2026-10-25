"""Only translated activity identifiers cross the job/UI boundary, never logs."""
from contextlib import contextmanager
from contextvars import ContextVar

ACTIVITIES = frozenset({'preparing_tools', 'preparing_storage', 'creating_users',
    'creating_shares', 'applying_configuration', 'installing_docker',
    'downloading_app', 'starting_app', 'checking_app', 'installing_tailscale', 'checking_services'})
_callback = ContextVar('nas_progress_callback', default=None)


@contextmanager
def reporting(callback):
    token = _callback.set(callback)
    try:
        yield
    finally:
        _callback.reset(token)


def report(code):
    if code not in ACTIVITIES:
        raise ValueError('Unknown activity identifier')
    callback = _callback.get()
    if callback is not None:
        callback(code)
