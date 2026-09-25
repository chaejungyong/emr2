CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS admins (
    id CHAR(36) PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    failed_login_count INT NOT NULL DEFAULT 0,
    locked_until DATETIME(6) NULL,
    last_login_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS patients (
    id CHAR(36) PRIMARY KEY,
    chart_number VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    species VARCHAR(80) NOT NULL,
    breed VARCHAR(120) NULL,
    sex ENUM('male', 'female', 'unknown') NOT NULL DEFAULT 'unknown',
    neutered BOOLEAN NULL,
    birth_date DATE NULL,
    weight_kg DECIMAL(7,2) NULL,
    notes TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX idx_patients_name (name),
    INDEX idx_patients_chart (chart_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS diseases (
    id CHAR(36) PRIMARY KEY,
    name VARCHAR(180) NOT NULL UNIQUE,
    category VARCHAR(120) NOT NULL DEFAULT '심장질환',
    description TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS encounters (
    id CHAR(36) PRIMARY KEY,
    patient_id CHAR(36) NOT NULL,
    disease_id CHAR(36) NULL,
    visit_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    chief_complaint TEXT NULL,
    history_text MEDIUMTEXT NULL,
    physical_exam MEDIUMTEXT NULL,
    status ENUM('draft', 'completed') NOT NULL DEFAULT 'draft',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_encounters_patient FOREIGN KEY (patient_id) REFERENCES patients(id),
    CONSTRAINT fk_encounters_disease FOREIGN KEY (disease_id) REFERENCES diseases(id) ON DELETE SET NULL,
    INDEX idx_encounters_patient_visit (patient_id, visit_at),
    INDEX idx_encounters_disease (disease_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS xray_assets (
    id CHAR(36) PRIMARY KEY,
    encounter_id CHAR(36) NOT NULL,
    storage_key VARCHAR(500) NOT NULL UNIQUE,
    original_name VARCHAR(255) NOT NULL,
    mime_type VARCHAR(80) NOT NULL,
    size_bytes BIGINT UNSIGNED NOT NULL,
    sha256 CHAR(64) NOT NULL,
    taken_at DATETIME(6) NULL,
    body_region VARCHAR(120) NULL,
    reading_text MEDIUMTEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_xray_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE,
    INDEX idx_xray_encounter (encounter_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS soap_documents (
    id CHAR(36) PRIMARY KEY,
    encounter_id CHAR(36) NOT NULL UNIQUE,
    status ENUM('draft', 'completed') NOT NULL DEFAULT 'draft',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_soap_document_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS soap_sections (
    id CHAR(36) PRIMARY KEY,
    soap_document_id CHAR(36) NOT NULL,
    stage ENUM('S', 'O', 'A', 'P') NOT NULL,
    status ENUM('pending', 'generated', 'confirmed', 'stale') NOT NULL DEFAULT 'pending',
    current_text MEDIUMTEXT NULL,
    confirmed_at DATETIME(6) NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_soap_section_document FOREIGN KEY (soap_document_id) REFERENCES soap_documents(id) ON DELETE CASCADE,
    UNIQUE KEY uq_soap_section_stage (soap_document_id, stage)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ai_generation_runs (
    id CHAR(36) PRIMARY KEY,
    soap_document_id CHAR(36) NOT NULL,
    stage ENUM('S', 'O', 'A', 'P') NOT NULL,
    status ENUM('running', 'completed', 'failed') NOT NULL DEFAULT 'running',
    provider VARCHAR(80) NOT NULL,
    model VARCHAR(180) NOT NULL,
    prompt_version VARCHAR(80) NOT NULL,
    kb_collection VARCHAR(255) NULL,
    input_snapshot LONGTEXT NULL,
    error_code VARCHAR(100) NULL,
    error_message TEXT NULL,
    latency_ms INT NULL,
    input_tokens INT NULL,
    output_tokens INT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    completed_at DATETIME(6) NULL,
    CONSTRAINT fk_generation_document FOREIGN KEY (soap_document_id) REFERENCES soap_documents(id) ON DELETE CASCADE,
    INDEX idx_generation_document_stage (soap_document_id, stage, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ai_candidates (
    id CHAR(36) PRIMARY KEY,
    run_id CHAR(36) NOT NULL,
    rank_no INT NOT NULL,
    content MEDIUMTEXT NOT NULL,
    is_selected BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_candidate_run FOREIGN KEY (run_id) REFERENCES ai_generation_runs(id) ON DELETE CASCADE,
    UNIQUE KEY uq_candidate_rank (run_id, rank_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS soap_section_revisions (
    id CHAR(36) PRIMARY KEY,
    section_id CHAR(36) NOT NULL,
    revision_no INT NOT NULL,
    source_candidate_id CHAR(36) NULL,
    content MEDIUMTEXT NOT NULL,
    confirmed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_revision_section FOREIGN KEY (section_id) REFERENCES soap_sections(id) ON DELETE CASCADE,
    CONSTRAINT fk_revision_candidate FOREIGN KEY (source_candidate_id) REFERENCES ai_candidates(id) ON DELETE SET NULL,
    UNIQUE KEY uq_section_revision (section_id, revision_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS candidate_evidence (
    id CHAR(36) PRIMARY KEY,
    candidate_id CHAR(36) NOT NULL,
    chunk_id CHAR(36) NOT NULL,
    document_name VARCHAR(255) NOT NULL,
    page_start INT NULL,
    page_end INT NULL,
    score DOUBLE NULL,
    excerpt TEXT NOT NULL,
    CONSTRAINT fk_evidence_candidate FOREIGN KEY (candidate_id) REFERENCES ai_candidates(id) ON DELETE CASCADE,
    INDEX idx_evidence_candidate (candidate_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS kb_documents (
    id CHAR(36) PRIMARY KEY,
    source_key VARCHAR(500) NOT NULL UNIQUE,
    display_name VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS kb_document_versions (
    id CHAR(36) PRIMARY KEY,
    document_id CHAR(36) NOT NULL,
    sha256 CHAR(64) NOT NULL,
    file_size BIGINT UNSIGNED NOT NULL,
    page_count INT NULL,
    embedding_model VARCHAR(180) NOT NULL,
    chunker_version VARCHAR(80) NOT NULL,
    status ENUM('processing', 'ready', 'failed') NOT NULL DEFAULT 'processing',
    error_message TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_kb_version_document FOREIGN KEY (document_id) REFERENCES kb_documents(id) ON DELETE CASCADE,
    INDEX idx_kb_version_document_active (document_id, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS kb_chunks (
    id CHAR(36) PRIMARY KEY,
    document_version_id CHAR(36) NOT NULL,
    chunk_index INT NOT NULL,
    page_start INT NULL,
    page_end INT NULL,
    heading VARCHAR(500) NULL,
    content MEDIUMTEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    token_estimate INT NOT NULL,
    CONSTRAINT fk_kb_chunk_version FOREIGN KEY (document_version_id) REFERENCES kb_document_versions(id) ON DELETE CASCADE,
    UNIQUE KEY uq_kb_chunk_index (document_version_id, chunk_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS index_jobs (
    id CHAR(36) PRIMARY KEY,
    job_type ENUM('incremental', 'full') NOT NULL,
    status ENUM('queued', 'running', 'completed', 'failed') NOT NULL DEFAULT 'queued',
    requested_by CHAR(36) NULL,
    embedding_model VARCHAR(180) NOT NULL,
    chunker_version VARCHAR(80) NOT NULL,
    total_files INT NOT NULL DEFAULT 0,
    processed_files INT NOT NULL DEFAULT 0,
    skipped_files INT NOT NULL DEFAULT 0,
    failed_files INT NOT NULL DEFAULT 0,
    new_collection VARCHAR(255) NULL,
    error_message TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,
    CONSTRAINT fk_index_job_admin FOREIGN KEY (requested_by) REFERENCES admins(id) ON DELETE SET NULL,
    INDEX idx_index_jobs_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS index_job_items (
    id CHAR(36) PRIMARY KEY,
    job_id CHAR(36) NOT NULL,
    source_key VARCHAR(500) NOT NULL,
    status ENUM('pending', 'processing', 'completed', 'skipped', 'failed') NOT NULL DEFAULT 'pending',
    chunk_count INT NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,
    CONSTRAINT fk_index_item_job FOREIGN KEY (job_id) REFERENCES index_jobs(id) ON DELETE CASCADE,
    UNIQUE KEY uq_index_job_source (job_id, source_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_events (
    id CHAR(36) PRIMARY KEY,
    admin_id CHAR(36) NULL,
    action VARCHAR(120) NOT NULL,
    entity_type VARCHAR(80) NULL,
    entity_id CHAR(36) NULL,
    metadata_json TEXT NULL,
    ip_address VARCHAR(64) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_audit_admin FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE SET NULL,
    INDEX idx_audit_entity (entity_type, entity_id),
    INDEX idx_audit_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
