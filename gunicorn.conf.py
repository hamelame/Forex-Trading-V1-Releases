"""Start the mobile scanner in the serving worker, including replacement workers."""


def post_worker_init(worker):
    from app import get_runtime

    if worker.cfg.workers != 1:
        raise RuntimeError("The mobile PAPER ledger requires exactly one worker")
    get_runtime()
    worker.log.info("PAPER scanner initialized at worker boot, before HTTP traffic")
