CREATE TABLE billing_catalog_items (
    code VARCHAR(80) PRIMARY KEY,
    description VARCHAR(255) NOT NULL,
    unit_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    match_type ENUM('consultation', 'diagnostic', 'prescription') NOT NULL,
    match_value VARCHAR(40) NULL,
    position_no SMALLINT UNSIGNED NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by CHAR(36) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_billing_catalog_admin FOREIGN KEY (updated_by) REFERENCES admins(id) ON DELETE SET NULL,
    INDEX idx_billing_catalog_order (is_active, position_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO billing_catalog_items
    (code, description, unit_amount, match_type, match_value, position_no)
VALUES
    ('CONSULT', '심장 진찰료', 30000, 'consultation', NULL, 10),
    ('ECHO', '심장초음파 검사', 150000, 'diagnostic', 'echo', 20),
    ('ECG', '심전도 검사', 50000, 'diagnostic', 'ecg', 30),
    ('XRAY', '흉부 X-ray 검사', 50000, 'diagnostic', 'xray', 40),
    ('BP', '혈압 검사', 10000, 'diagnostic', 'bp', 50),
    ('LAB', '혈액 검사', 50000, 'diagnostic', 'lab', 60),
    ('MED', '조제·약제비', 50000, 'prescription', NULL, 70);
