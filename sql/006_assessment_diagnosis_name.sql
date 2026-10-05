ALTER TABLE soap_sections
    ADD COLUMN diagnosis_name VARCHAR(255) NULL AFTER current_text;

ALTER TABLE soap_section_revisions
    ADD COLUMN diagnosis_name VARCHAR(255) NULL AFTER content;
