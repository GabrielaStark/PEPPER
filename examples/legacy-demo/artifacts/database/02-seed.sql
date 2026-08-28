-- Datos de prueba del padron

INSERT INTO citizen (id, nombre, curp, status, nationality) VALUES
    (1001, 'Maria Elena Vargas Ruiz',  'VARM850312MDFRZL03', 'ACTIVE',    'MX'),
    (1002, 'Jose Antonio Peralta Lim', 'PELJ790921HDFRML07', 'ACTIVE',    'MX'),
    (1003, 'Carmen Sofia Dominguez',   'DOSC920104MDFMFR01', 'SUSPENDED', 'MX'),
    (1004, 'Roberto Nunez Salas',      'NUSR681130HDFXLB09', 'INACTIVE',  'MX'),
    (1005, 'Ana Lucia Ferreira Costa', NULL,                 'ACTIVE',    'BR');

SELECT setval('citizen_id_seq', 1005);
