ALTER TABLE index_job_items
    ADD COLUMN progress_phase VARCHAR(32) NOT NULL DEFAULT 'pending' AFTER status,
    ADD COLUMN progress_current INT NOT NULL DEFAULT 0 AFTER progress_phase,
    ADD COLUMN progress_total INT NOT NULL DEFAULT 0 AFTER progress_current;
