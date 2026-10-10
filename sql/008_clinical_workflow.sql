CREATE TABLE IF NOT EXISTS ecg_exams (
    id CHAR(36) PRIMARY KEY,
    encounter_id CHAR(36) NOT NULL UNIQUE,
    recorded_at DATETIME(6) NULL,
    heart_rate_bpm SMALLINT UNSIGNED NULL,
    rhythm VARCHAR(120) NULL,
    pr_ms SMALLINT UNSIGNED NULL,
    qrs_ms SMALLINT UNSIGNED NULL,
    qt_ms SMALLINT UNSIGNED NULL,
    interpretation MEDIUMTEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_ecg_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS lab_results (
    id CHAR(36) PRIMARY KEY,
    encounter_id CHAR(36) NOT NULL UNIQUE,
    collected_at DATETIME(6) NULL,
    nt_probnp_pmol_l DECIMAL(10,2) NULL,
    troponin_i_ng_ml DECIMAL(10,3) NULL,
    bun_mg_dl DECIMAL(10,2) NULL,
    creatinine_mg_dl DECIMAL(10,2) NULL,
    sodium_mmol_l DECIMAL(10,2) NULL,
    potassium_mmol_l DECIMAL(10,2) NULL,
    notes MEDIUMTEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_lab_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS appointments (
    id CHAR(36) PRIMARY KEY,
    patient_id CHAR(36) NOT NULL,
    starts_at DATETIME(6) NOT NULL,
    duration_minutes SMALLINT UNSIGNED NOT NULL DEFAULT 30,
    purpose VARCHAR(240) NOT NULL,
    status ENUM('scheduled', 'arrived', 'completed', 'cancelled', 'no_show') NOT NULL DEFAULT 'scheduled',
    notes TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_appointment_patient FOREIGN KEY (patient_id) REFERENCES patients(id),
    INDEX idx_appointments_starts (starts_at),
    INDEX idx_appointments_patient (patient_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS prescriptions (
    id CHAR(36) PRIMARY KEY,
    encounter_id CHAR(36) NOT NULL UNIQUE,
    issued_at DATETIME(6) NULL,
    medication_text MEDIUMTEXT NULL,
    instructions MEDIUMTEXT NULL,
    status ENUM('draft', 'issued') NOT NULL DEFAULT 'draft',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_prescription_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS billing_records (
    id CHAR(36) PRIMARY KEY,
    encounter_id CHAR(36) NOT NULL UNIQUE,
    consultation_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    diagnostic_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    medication_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    other_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    discount_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    total_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    payment_status ENUM('unpaid', 'paid', 'refunded') NOT NULL DEFAULT 'unpaid',
    payment_method ENUM('cash', 'card', 'transfer', 'other') NULL,
    paid_at DATETIME(6) NULL,
    notes TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_billing_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS echo_video_assets (
    id CHAR(36) PRIMARY KEY,
    encounter_id CHAR(36) NOT NULL,
    storage_key VARCHAR(500) NOT NULL UNIQUE,
    original_name VARCHAR(255) NOT NULL,
    mime_type VARCHAR(80) NOT NULL,
    size_bytes BIGINT UNSIGNED NOT NULL,
    sha256 CHAR(64) NOT NULL,
    recorded_at DATETIME(6) NULL,
    note VARCHAR(500) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_echo_video_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE,
    INDEX idx_echo_video_encounter (encounter_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
