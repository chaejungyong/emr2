ALTER TABLE patients
    ADD COLUMN owner_name VARCHAR(120) NULL AFTER name,
    ADD COLUMN owner_phone CHAR(13) NULL AFTER owner_name;
