CREATE TABLE medication_billing_catalog (
    id CHAR(36) PRIMARY KEY,
    medication_name VARCHAR(255) NOT NULL UNIQUE,
    billing_description VARCHAR(255) NOT NULL,
    unit_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by CHAR(36) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_medication_billing_admin FOREIGN KEY (updated_by) REFERENCES admins(id) ON DELETE SET NULL,
    INDEX idx_medication_billing_active (is_active, medication_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELETE FROM billing_catalog_items WHERE code='MED';
