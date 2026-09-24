-- Run as administrator AFTER migration_sensor_pipeline.sql.
-- Transport-independent storage for one image paired with one real sensor session.
USE hairsense_demo;

CREATE TABLE IF NOT EXISTS sensor_capture_images (
    device_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    boot_id VARCHAR(64) NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    image_sha256 CHAR(64) NOT NULL,
    image_mime VARCHAR(32) NOT NULL,
    image_bytes LONGBLOB NOT NULL,
    image_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    image_result_json JSON NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (device_id, session_id, boot_id),
    CONSTRAINT fk_capture_image_session FOREIGN KEY (device_id, session_id, boot_id)
        REFERENCES sensor_sessions(device_id, session_id, boot_id)
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

GRANT SELECT, INSERT, UPDATE ON hairsense_demo.sensor_capture_images TO 'hairsense_app'@'localhost';
-- No change to the existing sensor/image-model tables or files.
-- The configured server max_allowed_packet must accommodate image + JSON overhead.
SHOW CREATE TABLE sensor_capture_images;
