-- Sistema de Solicitudes - esquema base
-- Migrado desde el servidor SRV-TRAMITES-01 el 12/03/2019

CREATE SEQUENCE folio_seq START WITH 1 INCREMENT BY 1;

CREATE TABLE citizen (
    id           BIGSERIAL PRIMARY KEY,
    nombre       VARCHAR(200) NOT NULL,
    curp         VARCHAR(18),
    status       VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE',
    nationality  VARCHAR(2)   NOT NULL DEFAULT 'MX',
    fecha_alta   TIMESTAMP    NOT NULL DEFAULT now()
);

CREATE TABLE application (
    id              BIGSERIAL PRIMARY KEY,
    folio           VARCHAR(20)  NOT NULL UNIQUE,
    citizen_id      BIGINT       NOT NULL REFERENCES citizen (id),
    tipo_tramite    VARCHAR(50)  NOT NULL,
    estado          VARCHAR(30)  NOT NULL,
    fecha_registro  TIMESTAMP    NOT NULL DEFAULT now()
);

CREATE TABLE application_history (
    id              BIGSERIAL PRIMARY KEY,
    application_id  BIGINT      NOT NULL REFERENCES application (id),
    estado          VARCHAR(30) NOT NULL,
    fecha           TIMESTAMP   NOT NULL DEFAULT now()
);

CREATE INDEX idx_application_citizen ON application (citizen_id);
CREATE INDEX idx_history_application ON application_history (application_id);
