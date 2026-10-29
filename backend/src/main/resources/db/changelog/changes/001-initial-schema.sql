--liquibase formatted sql
--changeset opti-transit:001-init-schema

CREATE TABLE IF NOT EXISTS app_status (
    id BIGSERIAL PRIMARY KEY,
    service_name VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
