ALTER TABLE patients
    ADD COLUMN current_medications TEXT NULL AFTER notes,
    ADD COLUMN allergies TEXT NULL AFTER current_medications,
    ADD COLUMN preventive_care TEXT NULL AFTER allergies;

CREATE TABLE IF NOT EXISTS clinic_tasks (
    id CHAR(36) PRIMARY KEY,
    patient_id CHAR(36) NULL,
    encounter_id CHAR(36) NULL,
    due_at DATETIME(6) NOT NULL,
    task_type ENUM('result_review', 'callback', 'general') NOT NULL DEFAULT 'general',
    title VARCHAR(240) NOT NULL,
    status ENUM('open', 'done') NOT NULL DEFAULT 'open',
    notes TEXT NULL,
    completed_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_task_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE SET NULL,
    CONSTRAINT fk_task_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE SET NULL,
    INDEX idx_tasks_due_status (due_at, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
