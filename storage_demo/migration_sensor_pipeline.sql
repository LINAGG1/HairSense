-- Stop uvicorn before running this file in MySQL Workbench.
-- Additive migration: existing sensor_readings and image data are preserved.
USE hairsense_demo;

CREATE TABLE IF NOT EXISTS sensor_sessions (
    device_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    boot_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'receiving',
    metadata_json JSON NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (device_id, session_id, boot_id)
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE IF NOT EXISTS sensor_analysis_runs (
    device_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    boot_id VARCHAR(64) NOT NULL,
    pipeline_version VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    result_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (device_id, session_id, boot_id),
    CONSTRAINT fk_sensor_run_session FOREIGN KEY (device_id, session_id, boot_id)
        REFERENCES sensor_sessions(device_id, session_id, boot_id)
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- Match this account to the configured app account if you use a different user.
GRANT SELECT, INSERT, UPDATE ON hairsense_demo.sensor_sessions TO 'hairsense_app'@'localhost';
GRANT SELECT, INSERT, UPDATE ON hairsense_demo.sensor_analysis_runs TO 'hairsense_app'@'localhost';

SHOW CREATE TABLE sensor_sessions;
SHOW CREATE TABLE sensor_analysis_runs;
