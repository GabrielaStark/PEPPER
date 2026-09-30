<?php
// Fixture: una página de PHP clásico que convive con el Laravel (pasa en sistemas viejos)
require_once __DIR__ . '/../bootstrap/legacy_db.php';
?>
<!doctype html>
<html><head><title>Consulta pública de trámites</title></head>
<body>
<h1>Consulta pública</h1>
<form action="consulta_publica.php" method="post">
  <label>Folio</label> <input type="text" name="folio">
  <input type="submit" name="buscar" value="Consultar">
</form>
<?php if (!empty($_POST['folio'])) { $st = $pdo->prepare('select folio, estado from tramites where folio = ?'); $st->execute([$_POST['folio']]); } ?>
</body></html>
