-- Run in MySQL Workbench as an administrator.
-- Replace the example password below before execution.
CREATE DATABASE IF NOT EXISTS hairsense_demo
    CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
USE hairsense_demo;

CREATE TABLE IF NOT EXISTS sensor_readings (
    device_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    seq BIGINT UNSIGNED NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    is_synthetic BOOLEAN NOT NULL,
    schema_version VARCHAR(16) NOT NULL,
    timestamp_ms BIGINT UNSIGNED NOT NULL,
    optical DOUBLE NULL,
    gyro_x DOUBLE NULL,
    gyro_y DOUBLE NULL,
    gyro_z DOUBLE NULL,
    ingested_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (device_id, session_id, seq)
) ENGINE=InnoDB;

CREATE USER IF NOT EXISTS 'hairsense_app'@'localhost'
    IDENTIFIED BY '0907';
GRANT SELECT, INSERT ON hairsense_demo.sensor_readings
    TO 'hairsense_app'@'localhost';

-- IF NOT EXISTS does not reset the password of an existing user.
