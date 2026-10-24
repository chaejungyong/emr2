ALTER TABLE soap_sections
    ADD COLUMN draft_text MEDIUMTEXT NULL AFTER current_text,
    ADD COLUMN draft_diagnosis_name VARCHAR(255) NULL AFTER diagnosis_name,
    ADD COLUMN draft_updated_at DATETIME(6) NULL AFTER confirmed_at;
