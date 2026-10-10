CREATE TABLE clinical_result_revisions (
    id CHAR(36) PRIMARY KEY,
    encounter_id CHAR(36) NOT NULL,
    result_type VARCHAR(32) NOT NULL,
    result_id CHAR(36) NOT NULL,
    revision_no INT NOT NULL,
    change_kind ENUM('created', 'updated', 'corrected', 'migration') NOT NULL,
    snapshot_json MEDIUMTEXT NOT NULL,
    snapshot_sha256 CHAR(64) NOT NULL,
    changed_fields_json TEXT NULL,
    created_by CHAR(36) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_clinical_revision_encounter
        FOREIGN KEY (encounter_id) REFERENCES encounters(id),
    CONSTRAINT fk_clinical_revision_admin
        FOREIGN KEY (created_by) REFERENCES admins(id) ON DELETE SET NULL,
    UNIQUE KEY uq_clinical_revision (result_type, result_id, revision_no),
    INDEX idx_clinical_revision_encounter (encounter_id, created_at),
    INDEX idx_clinical_revision_result (result_type, result_id, revision_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE cardiac_monitoring_logs (
    id CHAR(36) PRIMARY KEY,
    patient_id CHAR(36) NOT NULL,
    encounter_id CHAR(36) NULL,
    correction_of_id CHAR(36) NULL,
    measured_at DATETIME(6) NOT NULL,
    source ENUM('clinic', 'owner_report', 'device') NOT NULL DEFAULT 'owner_report',
    body_weight_kg DECIMAL(7,2) NULL,
    home_rr_rpm SMALLINT UNSIGNED NULL,
    systolic_bp_mmhg SMALLINT UNSIGNED NULL,
    heart_rate_bpm SMALLINT UNSIGNED NULL,
    cough BOOLEAN NOT NULL DEFAULT FALSE,
    dyspnea BOOLEAN NOT NULL DEFAULT FALSE,
    syncope BOOLEAN NOT NULL DEFAULT FALSE,
    appetite ENUM('normal', 'reduced', 'none', 'unknown') NOT NULL DEFAULT 'unknown',
    medication_adherence ENUM('all', 'partial', 'missed', 'unknown') NOT NULL DEFAULT 'unknown',
    adverse_effects VARCHAR(2000) NULL,
    notes VARCHAR(4000) NULL,
    created_by CHAR(36) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_monitoring_patient
        FOREIGN KEY (patient_id) REFERENCES patients(id),
    CONSTRAINT fk_monitoring_encounter
        FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE SET NULL,
    CONSTRAINT fk_monitoring_correction
        FOREIGN KEY (correction_of_id) REFERENCES cardiac_monitoring_logs(id),
    CONSTRAINT fk_monitoring_admin
        FOREIGN KEY (created_by) REFERENCES admins(id) ON DELETE SET NULL,
    INDEX idx_monitoring_patient_time (patient_id, measured_at),
    INDEX idx_monitoring_encounter (encounter_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO clinical_result_revisions
    (id, encounter_id, result_type, result_id, revision_no, change_kind,
     snapshot_json, snapshot_sha256, changed_fields_json, created_at)
SELECT UUID(), ce.encounter_id, 'cardiac', ce.id, 1, 'migration',
       JSON_OBJECT(
           'id', ce.id, 'encounter_id', ce.encounter_id,
           'body_weight_kg', ce.body_weight_kg, 'heart_rate_bpm', ce.heart_rate_bpm,
           'respiratory_rate_rpm', ce.respiratory_rate_rpm,
           'systolic_bp_mmhg', ce.systolic_bp_mmhg, 'home_rr_rpm', ce.home_rr_rpm,
           'vhs', ce.vhs, 'vlas', ce.vlas, 'la_ao', ce.la_ao,
           'lvidd_cm', ce.lvidd_cm, 'lviddn', ce.lviddn,
           'lvids_cm', ce.lvids_cm, 'fs_percent', ce.fs_percent,
           'e_velocity_ms', ce.e_velocity_ms, 'a_velocity_ms', ce.a_velocity_ms,
           'e_a_ratio', ce.e_a_ratio, 'tr_vmax_ms', ce.tr_vmax_ms,
           'pr_vmax_ms', ce.pr_vmax_ms, 'mr_severity', ce.mr_severity,
           'sam', ce.sam, 'lvoto', ce.lvoto, 'sec_present', ce.sec_present,
           'la_thrombus', ce.la_thrombus,
           'pericardial_effusion', ce.pericardial_effusion,
           'findings', ce.findings, 'assessment', ce.assessment,
           'acvim_stage', ce.acvim_stage, 'ph_risk', ce.ph_risk,
           'chf_status', ce.chf_status, 'follow_up_months', ce.follow_up_months
       ),
       SHA2(JSON_OBJECT('id', ce.id, 'encounter_id', ce.encounter_id,
                        'updated_at', ce.updated_at), 256),
       JSON_ARRAY(), ce.updated_at
FROM cardiac_exams ce;

INSERT INTO clinical_result_revisions
    (id, encounter_id, result_type, result_id, revision_no, change_kind,
     snapshot_json, snapshot_sha256, changed_fields_json, created_at)
SELECT UUID(), ecg.encounter_id, 'ecg', ecg.id, 1, 'migration',
       JSON_OBJECT(
           'id', ecg.id, 'encounter_id', ecg.encounter_id,
           'recorded_at', ecg.recorded_at, 'heart_rate_bpm', ecg.heart_rate_bpm,
           'rhythm', ecg.rhythm, 'pr_ms', ecg.pr_ms, 'qrs_ms', ecg.qrs_ms,
           'qt_ms', ecg.qt_ms, 'interpretation', ecg.interpretation
       ),
       SHA2(JSON_OBJECT('id', ecg.id, 'encounter_id', ecg.encounter_id,
                        'updated_at', ecg.updated_at), 256),
       JSON_ARRAY(), ecg.updated_at
FROM ecg_exams ecg;

INSERT INTO clinical_result_revisions
    (id, encounter_id, result_type, result_id, revision_no, change_kind,
     snapshot_json, snapshot_sha256, changed_fields_json, created_at)
SELECT UUID(), lab.encounter_id, 'lab', lab.id, 1, 'migration',
       JSON_OBJECT(
           'id', lab.id, 'encounter_id', lab.encounter_id,
           'collected_at', lab.collected_at,
           'nt_probnp_pmol_l', lab.nt_probnp_pmol_l,
           'troponin_i_ng_ml', lab.troponin_i_ng_ml,
           'bun_mg_dl', lab.bun_mg_dl, 'creatinine_mg_dl', lab.creatinine_mg_dl,
           'sodium_mmol_l', lab.sodium_mmol_l,
           'potassium_mmol_l', lab.potassium_mmol_l, 'notes', lab.notes
       ),
       SHA2(JSON_OBJECT('id', lab.id, 'encounter_id', lab.encounter_id,
                        'updated_at', lab.updated_at), 256),
       JSON_ARRAY(), lab.updated_at
FROM lab_results lab;

INSERT INTO clinical_result_revisions
    (id, encounter_id, result_type, result_id, revision_no, change_kind,
     snapshot_json, snapshot_sha256, changed_fields_json, created_at)
SELECT UUID(), x.encounter_id, 'xray', x.id, 1, 'migration',
       JSON_OBJECT(
           'id', x.id, 'encounter_id', x.encounter_id,
           'original_name', x.original_name, 'mime_type', x.mime_type,
           'size_bytes', x.size_bytes, 'sha256', x.sha256,
           'taken_at', x.taken_at, 'body_region', x.body_region,
           'reading_text', x.reading_text
       ),
       SHA2(JSON_OBJECT('id', x.id, 'encounter_id', x.encounter_id,
                        'sha256', x.sha256, 'updated_at', x.updated_at), 256),
       JSON_ARRAY(), x.updated_at
FROM xray_assets x;

UPDATE clinical_result_revisions
SET snapshot_sha256=SHA2(snapshot_json, 256)
WHERE change_kind='migration';
