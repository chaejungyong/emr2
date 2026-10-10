ALTER TABLE kb_documents
    ADD COLUMN source_type VARCHAR(80) NOT NULL DEFAULT 'medical_reference' AFTER display_name,
    ADD COLUMN canonical_title VARCHAR(255) NULL AFTER source_type,
    ADD COLUMN edition VARCHAR(80) NULL AFTER canonical_title,
    ADD COLUMN volume_label VARCHAR(80) NULL AFTER edition;

ALTER TABLE candidate_evidence
    ADD COLUMN source_type VARCHAR(80) NULL AFTER document_name,
    ADD COLUMN source_heading VARCHAR(500) NULL AFTER source_type,
    ADD COLUMN source_edition VARCHAR(80) NULL AFTER source_heading,
    ADD COLUMN source_volume VARCHAR(80) NULL AFTER source_edition;
