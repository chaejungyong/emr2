ALTER TABLE patients
    ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE AFTER notes,
    ADD COLUMN archived_at DATETIME(6) NULL AFTER is_archived,
    ADD INDEX idx_patients_archived_updated (is_archived, updated_at);
