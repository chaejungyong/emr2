CREATE TABLE IF NOT EXISTS patient_chart_number_sequence (
    id TINYINT UNSIGNED PRIMARY KEY,
    next_value BIGINT UNSIGNED NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO patient_chart_number_sequence (id, next_value)
SELECT
    1,
    COALESCE(
        MAX(
            CASE
                WHEN chart_number REGEXP '^C-[0-9]+$'
                THEN CAST(SUBSTRING(chart_number, 3) AS UNSIGNED)
                ELSE 0
            END
        ),
        0
    ) + 1
FROM patients
ON DUPLICATE KEY UPDATE
    next_value = GREATEST(next_value, VALUES(next_value));
