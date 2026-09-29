-- Run in MySQL Workbench as an administrator.
-- Replace the example password below before execution.
CREATE DATABASE IF NOT EXISTS hairsense_demo
    CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
USE hairsense_demo;

CREATE TABLE IF NOT EXISTS sensor_readings (
    device_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    boot_id VARCHAR(64) NOT NULL,
    seq BIGINT UNSIGNED NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    is_synthetic BOOLEAN NOT NULL,
    schema_version INT UNSIGNED NOT NULL,
    timestamp_ms BIGINT UNSIGNED NOT NULL,
    optical DOUBLE NULL,
    gyro_x DOUBLE NULL,
    gyro_y DOUBLE NULL,
    gyro_z DOUBLE NULL,
    ingested_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (device_id, session_id, boot_id, seq)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS image_analysis_results (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    user_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NULL,

    measured_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    micro_scale_grade TINYINT UNSIGNED NOT NULL,
    micro_scale_confidence DOUBLE NOT NULL,

    excess_sebum_grade TINYINT UNSIGNED NOT NULL,
    excess_sebum_confidence DOUBLE NOT NULL,

    perifollicular_erythema_grade TINYINT UNSIGNED NOT NULL,
    perifollicular_erythema_confidence DOUBLE NOT NULL,

    follicular_erythema_pustule_grade TINYINT UNSIGNED NOT NULL,
    follicular_erythema_pustule_confidence DOUBLE NOT NULL,

    dandruff_grade TINYINT UNSIGNED NOT NULL,
    dandruff_confidence DOUBLE NOT NULL,

    hair_loss_grade TINYINT UNSIGNED NOT NULL,
    hair_loss_confidence DOUBLE NOT NULL,

    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    INDEX idx_image_user_date (user_id, measured_at)

) ENGINE=InnoDB;

CREATE USER IF NOT EXISTS 'hairsense_app'@'localhost'
    IDENTIFIED BY '0907';
GRANT SELECT, INSERT ON hairsense_demo.sensor_readings
    TO 'hairsense_app'@'localhost';

GRANT SELECT, INSERT ON  hairsense_demo.image_analysis_results
    TO 'hairsense_app'@'localhost';
-- IF NOT EXISTS does not reset the password of an existing user.
