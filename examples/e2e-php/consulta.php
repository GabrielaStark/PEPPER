<?php
// Fixture del E2E de PEPPER: la consulta por folio de la ventanilla sintética (ver index.php).
$env = parse_ini_file(__DIR__ . '/.env', false, INI_SCANNER_RAW) ?: [];
$dsn = sprintf('mysql:host=%s;port=%s;dbname=%s;charset=utf8mb4',
    $env['DB_HOST'] ?? '127.0.0.1', $env['DB_PORT'] ?? '3306', $env['DB_DATABASE'] ?? '');
$pdo = new PDO($dsn, $env['DB_USERNAME'] ?? '', $env['DB_PASSWORD'] ?? '', [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
$folio = trim($_POST['folio'] ?? '');
$fila = null;
if ($folio !== '') {
    $st = $pdo->prepare('select t.folio, t.solicitante, e.nombre as estado from tramites t join estados e on e.id = t.estado_id where t.folio = ?');
    $st->execute([$folio]);
    $fila = $st->fetch(PDO::FETCH_ASSOC);
}
?>
<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><title>Consulta de trámite</title></head>
<body>
<h1>Consulta de trámite</h1>
<?php if ($folio === ''): ?>
  <p class="error">Escribe un folio.</p>
<?php elseif ($fila === false || $fila === null): ?>
  <p class="error">No existe el trámite <?= htmlspecialchars($folio) ?>.</p>
<?php else: ?>
  <p>Folio <?= htmlspecialchars($fila['folio']) ?> · <?= htmlspecialchars($fila['solicitante']) ?> · estado: <?= htmlspecialchars($fila['estado']) ?></p>
<?php endif; ?>
<a href="/">Volver</a>
</body>
</html>
