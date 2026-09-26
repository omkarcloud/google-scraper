"""The one helper google_search/identity.py needs from a browser pool: a lock
held while a browser is being launched, so two launches never race."""
import threading
from contextlib import contextmanager

_create_lock = threading.Lock()


@contextmanager
def create_scope():
    with _create_lock:
        yield
