import signal
import time

from app import create_app
from app.db import execute, transaction
from app.indexing import claim_next_job, run_job


app = create_app()
running = True


def stop(_signum, _frame):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


with app.app_context():
    with transaction():
        execute(
            """
            UPDATE index_jobs
            SET status = 'queued', started_at = NULL,
                error_message = 'Worker 재시작 후 작업을 재개했습니다.'
            WHERE status = 'running'
            """
        )

    while running:
        job_id = claim_next_job()
        if job_id:
            app.logger.info("Starting knowledge index job %s", job_id)
            run_job(job_id)
            app.logger.info("Finished knowledge index job %s", job_id)
        else:
            time.sleep(app.config["WORKER_POLL_SECONDS"])
