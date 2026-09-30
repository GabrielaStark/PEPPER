<?php
// Fixture del E2E de PEPPER (scripts/e2e_docker.py --profile php-apache-mysql): una "ventanilla" mínima
// en PHP clásico que lee su .env y consulta MySQL por PDO. No es un sistema real ni lo pretende: existe
// para que un cambio en el núcleo que rompa el levantamiento de la familia PHP se caiga en CI.
$env = parse_ini_file(__DIR__ . '/.env', false, INI_SCANNER_RAW) ?: [];
$dsn = sprintf('mysql:host=%s;port=%s;dbname=%s;charset=utf8mb4',
    $env['DB_HOST'] ?? '127.0.0.1', $env['DB_PORT'] ?? '3306', $env['DB_DATABASE'] ?? '');
try {
    $pdo = new PDO($dsn, $env['DB_USERNAME'] ?? '', $env['DB_PASSWORD'] ?? '', [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    $total = (int) $pdo->query('select count(*) from tramites')->fetchColumn();
    $pendientes = (int) $pdo->query('select count(*) from v_tramites_pendientes')->fetchColumn();
} catch (PDOException $e) {
    http_response_code(500);
    echo 'Ventanilla del E2E: sin base — ' . htmlspecialchars($e->getMessage());
    exit;
}
?>
<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><title>Ventanilla del E2E</title></head>
<body>
<h1>Ventanilla del E2E</h1>
<p>Trámites registrados: <?= $total ?> · pendientes: <?= $pendientes ?></p>
<form action="consulta.php" method="post" id="consulta">
  <label for="folio">Folio</label> <input type="text" name="folio" id="folio">
  <input type="submit" name="buscar" value="Consultar">
</form>
</body>
</html>
