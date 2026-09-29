-- Additive installation boundary: old captures are never backfilled.
USE hairsense_demo;
CREATE TABLE IF NOT EXISTS real_sensor_profiles (
    user_id VARCHAR(64) NOT NULL,
    device_id VARCHAR(64) NOT NULL,
    sensor VARCHAR(16) NOT NULL,
    state_json JSON NOT NULL,
    next_role VARCHAR(16) NOT NULL DEFAULT 'measurement',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (user_id, device_id, sensor)
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE IF NOT EXISTS real_sensor_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    device_id VARCHAR(64) NOT NULL,
    sensor VARCHAR(16) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    boot_id VARCHAR(64) NOT NULL,
    result_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY capture (device_id, session_id, boot_id),
    KEY latest (user_id, device_id, sensor, id)
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

GRANT SELECT, INSERT, UPDATE ON hairsense_demo.real_sensor_profiles TO 'hairsense_app'@'localhost';
GRANT SELECT, INSERT, UPDATE ON hairsense_demo.real_sensor_events TO 'hairsense_app'@'localhost';
