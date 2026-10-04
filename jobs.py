"""Independent, cooperative background workers; no shared exclusive scan lock."""
import threading
import time

KINDS = ('scan', 'faces', 'places', 'search')
JOBS = {kind: dict(kind=kind, running=False, phase='idle', processed=0, total=0,
                  discovered=0, errors=[], error_count=0, message='Ready', started_at=None)
        for kind in KINDS}
LOCKS = {kind: threading.Lock() for kind in KINDS}
STOPS = {kind: threading.Event() for kind in KINDS}
LOCAL = threading.local()

def record_error(message, kind=None):
    job = JOBS[kind or getattr(LOCAL, 'kind', 'scan')]
    job['error_count'] += 1
    if len(job['errors']) < 100:
        job['errors'].append(str(message))

def launch(kind, worker):
    lock = LOCKS[kind]
    if not lock.acquire(blocking=False):
        return False
    stop = STOPS[kind]
    stop.clear()
    job = JOBS[kind]
    job.update(running=True, phase='starting', processed=0, total=0, discovered=0,
               errors=[], error_count=0, message='Starting…', started_at=time.time())
    def run():
        LOCAL.kind = kind
        try:
            worker()
            job['phase'] = 'paused' if stop.is_set() else 'complete'
            if stop.is_set():
                job['message'] = 'Paused. Finished files are saved; start again to continue.'
        except Exception as error:
            record_error(error, kind)
            job.update(phase='error', message=str(error))
        finally:
            job['running'] = False
            lock.release()
    threading.Thread(target=run, name=f'memorylane-{kind}', daemon=True).start()
    return True
