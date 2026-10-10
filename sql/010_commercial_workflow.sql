ALTER TABLE admins
    ADD COLUMN role ENUM('veterinarian', 'staff') NOT NULL DEFAULT 'veterinarian' AFTER username;

ALTER TABLE appointments
    ADD COLUMN encounter_id CHAR(36) NULL AFTER patient_id,
    ADD COLUMN arrived_at DATETIME(6) NULL AFTER status,
    ADD COLUMN checked_in_by CHAR(36) NULL AFTER arrived_at,
    ADD CONSTRAINT fk_appointment_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE SET NULL,
    ADD CONSTRAINT fk_appointment_checked_in_by FOREIGN KEY (checked_in_by) REFERENCES admins(id) ON DELETE SET NULL,
    ADD UNIQUE KEY uq_appointment_encounter (encounter_id);

ALTER TABLE encounters
    ADD COLUMN workflow_stage VARCHAR(32) NOT NULL DEFAULT 'intake' AFTER status,
    ADD COLUMN workflow_updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) AFTER workflow_stage,
    ADD COLUMN closed_at DATETIME(6) NULL AFTER workflow_updated_at,
    ADD COLUMN closed_by CHAR(36) NULL AFTER closed_at,
    ADD CONSTRAINT fk_encounter_closed_by FOREIGN KEY (closed_by) REFERENCES admins(id) ON DELETE SET NULL,
    ADD INDEX idx_encounter_workflow (workflow_stage, visit_at);

UPDATE encounters
SET workflow_stage='closed', workflow_updated_at=updated_at, closed_at=updated_at
WHERE status='completed';

ALTER TABLE prescriptions
    MODIFY COLUMN status ENUM('draft', 'issued', 'not_required', 'cancelled') NOT NULL DEFAULT 'draft',
    ADD COLUMN issued_by CHAR(36) NULL AFTER status,
    ADD COLUMN review_required BOOLEAN NOT NULL DEFAULT FALSE AFTER issued_by,
    ADD COLUMN not_required_reason VARCHAR(500) NULL AFTER review_required,
    ADD COLUMN cancelled_at DATETIME(6) NULL AFTER not_required_reason,
    ADD CONSTRAINT fk_prescription_issued_by FOREIGN KEY (issued_by) REFERENCES admins(id) ON DELETE SET NULL;

CREATE TABLE diagnostic_requirements (
    id CHAR(36) PRIMARY KEY,
    encounter_id CHAR(36) NOT NULL,
    exam_type ENUM('echo', 'ecg', 'xray', 'bp', 'lab') NOT NULL,
    status ENUM('planned', 'completed', 'reviewed', 'not_required') NOT NULL DEFAULT 'planned',
    reason VARCHAR(500) NULL,
    reviewed_by CHAR(36) NULL,
    reviewed_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_diagnostic_requirement_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE,
    CONSTRAINT fk_diagnostic_requirement_reviewer FOREIGN KEY (reviewed_by) REFERENCES admins(id) ON DELETE SET NULL,
    UNIQUE KEY uq_diagnostic_requirement (encounter_id, exam_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE client_education_documents (
    id CHAR(36) PRIMARY KEY,
    encounter_id CHAR(36) NOT NULL,
    version_no INT NOT NULL,
    source_a_revision_id CHAR(36) NOT NULL,
    source_p_revision_id CHAR(36) NOT NULL,
    content_snapshot MEDIUMTEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    status ENUM('draft', 'delivered', 'superseded') NOT NULL DEFAULT 'draft',
    delivery_method ENUM('print', 'pdf') NULL,
    delivered_by CHAR(36) NULL,
    delivered_at DATETIME(6) NULL,
    created_by CHAR(36) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_education_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE,
    CONSTRAINT fk_education_a_revision FOREIGN KEY (source_a_revision_id) REFERENCES soap_section_revisions(id),
    CONSTRAINT fk_education_p_revision FOREIGN KEY (source_p_revision_id) REFERENCES soap_section_revisions(id),
    CONSTRAINT fk_education_delivered_by FOREIGN KEY (delivered_by) REFERENCES admins(id) ON DELETE SET NULL,
    CONSTRAINT fk_education_created_by FOREIGN KEY (created_by) REFERENCES admins(id) ON DELETE SET NULL,
    UNIQUE KEY uq_education_version (encounter_id, version_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE prescription_items (
    id CHAR(36) PRIMARY KEY,
    prescription_id CHAR(36) NOT NULL,
    position_no SMALLINT UNSIGNED NOT NULL,
    medication_name VARCHAR(255) NOT NULL,
    dose_value DECIMAL(12,4) NOT NULL,
    dose_unit VARCHAR(40) NOT NULL,
    route VARCHAR(80) NOT NULL,
    frequency VARCHAR(120) NOT NULL,
    duration_days SMALLINT UNSIGNED NOT NULL,
    instructions VARCHAR(1000) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_prescription_item_prescription FOREIGN KEY (prescription_id) REFERENCES prescriptions(id) ON DELETE CASCADE,
    UNIQUE KEY uq_prescription_item_position (prescription_id, position_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE prescription_revisions (
    id CHAR(36) PRIMARY KEY,
    prescription_id CHAR(36) NOT NULL,
    revision_no INT NOT NULL,
    status VARCHAR(32) NOT NULL,
    snapshot_json MEDIUMTEXT NOT NULL,
    snapshot_sha256 CHAR(64) NOT NULL,
    created_by CHAR(36) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_prescription_revision_prescription FOREIGN KEY (prescription_id) REFERENCES prescriptions(id) ON DELETE CASCADE,
    CONSTRAINT fk_prescription_revision_admin FOREIGN KEY (created_by) REFERENCES admins(id) ON DELETE SET NULL,
    UNIQUE KEY uq_prescription_revision (prescription_id, revision_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE invoices (
    id CHAR(36) PRIMARY KEY,
    encounter_id CHAR(36) NOT NULL,
    version_no INT NOT NULL DEFAULT 1,
    replaces_invoice_id CHAR(36) NULL,
    invoice_number VARCHAR(40) NOT NULL UNIQUE,
    status ENUM('draft', 'issued', 'partially_paid', 'paid', 'void') NOT NULL DEFAULT 'draft',
    subtotal_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    discount_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    total_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    paid_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    deferred_reason VARCHAR(500) NULL,
    issued_at DATETIME(6) NULL,
    issued_by CHAR(36) NULL,
    voided_at DATETIME(6) NULL,
    void_reason VARCHAR(500) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_invoice_encounter FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE,
    CONSTRAINT fk_invoice_issued_by FOREIGN KEY (issued_by) REFERENCES admins(id) ON DELETE SET NULL,
    CONSTRAINT fk_invoice_replaces FOREIGN KEY (replaces_invoice_id) REFERENCES invoices(id) ON DELETE SET NULL,
    UNIQUE KEY uq_invoice_encounter_version (encounter_id, version_no),
    INDEX idx_invoice_status (status, issued_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE invoice_items (
    id CHAR(36) PRIMARY KEY,
    invoice_id CHAR(36) NOT NULL,
    position_no SMALLINT UNSIGNED NOT NULL,
    item_code VARCHAR(80) NULL,
    description VARCHAR(255) NOT NULL,
    quantity DECIMAL(10,2) NOT NULL DEFAULT 1,
    unit_amount DECIMAL(12,2) NOT NULL,
    line_amount DECIMAL(12,2) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_invoice_item_invoice FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
    UNIQUE KEY uq_invoice_item_position (invoice_id, position_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE payments (
    id CHAR(36) PRIMARY KEY,
    invoice_id CHAR(36) NOT NULL,
    payment_type ENUM('payment', 'refund') NOT NULL DEFAULT 'payment',
    payment_method ENUM('cash', 'card', 'transfer', 'other') NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    receipt_number VARCHAR(50) NOT NULL UNIQUE,
    receipt_snapshot MEDIUMTEXT NOT NULL,
    reason VARCHAR(500) NULL,
    paid_at DATETIME(6) NOT NULL,
    recorded_by CHAR(36) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_payment_invoice FOREIGN KEY (invoice_id) REFERENCES invoices(id),
    CONSTRAINT fk_payment_recorded_by FOREIGN KEY (recorded_by) REFERENCES admins(id) ON DELETE SET NULL,
    INDEX idx_payment_paid_at (paid_at),
    INDEX idx_payment_invoice (invoice_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE daily_closings (
    id CHAR(36) PRIMARY KEY,
    business_date DATE NOT NULL UNIQUE,
    totals_snapshot MEDIUMTEXT NOT NULL,
    closed_by CHAR(36) NULL,
    closed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_daily_closing_admin FOREIGN KEY (closed_by) REFERENCES admins(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO invoices
    (id, encounter_id, invoice_number, status, subtotal_amount, discount_amount,
     total_amount, paid_amount, deferred_reason, issued_at, created_at, updated_at)
SELECT UUID(), br.encounter_id, CONCAT('LEGACY-', LEFT(br.id, 8)),
       CASE WHEN br.payment_status = 'paid' THEN 'paid' ELSE 'issued' END,
       br.consultation_amount + br.diagnostic_amount + br.medication_amount + br.other_amount,
       br.discount_amount, br.total_amount,
       CASE WHEN br.payment_status = 'paid' THEN br.total_amount ELSE 0 END,
       CASE WHEN br.payment_status = 'unpaid' THEN '기존 미수납 기록' ELSE NULL END,
       COALESCE(br.paid_at, br.created_at), br.created_at, br.updated_at
FROM billing_records br
LEFT JOIN invoices i ON i.encounter_id = br.encounter_id
WHERE i.id IS NULL;

INSERT INTO payments
    (id, invoice_id, payment_type, payment_method, amount, receipt_number,
     receipt_snapshot, paid_at, recorded_by, created_at)
SELECT UUID(), i.id, 'payment', COALESCE(br.payment_method, 'other'), br.total_amount,
       CONCAT('LEGACY-RCP-', LEFT(br.id, 8)),
       JSON_OBJECT('legacy_billing_id', br.id, 'invoice_number', i.invoice_number,
                   'amount', br.total_amount, 'method', COALESCE(br.payment_method, 'other')),
       COALESCE(br.paid_at, br.updated_at), NULL, br.created_at
FROM billing_records br
JOIN invoices i ON i.encounter_id=br.encounter_id
LEFT JOIN payments p ON p.invoice_id=i.id
WHERE br.payment_status='paid' AND br.total_amount > 0 AND p.id IS NULL;
