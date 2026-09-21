"""Bytecode de la JVM para `pepper map`: qué clases mirar, con qué `javap`, y qué dijo.

Dos lecciones del tercer stack (2026-09-21) viven aquí:

  · Las clases se extraen a un JAR temporal, no al sistema de archivos. En macOS
    `putAway/` y `putaway/` son la misma carpeta: seis clases de un WAR real se
    pisaban entre sí y desaparecían del mapa sin aviso. Un zip distingue mayúsculas.
  · Un solo `javap` no lee todo. El de JDK 25 rechaza clases de Groovy 1.7 (flag
    ACC_SYNTHETIC en campos de major 47) que el de JDK 8 lee sin quejarse. Se
    localizan TODOS los javap de la máquina y, lo que el primero no lee, se intenta
    con los demás. Lo que ninguno lee queda como hueco, con nombre.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_CLASS_HEADER = re.compile(r"^(?:public |final |abstract |private |protected )*(?:class|interface|enum) ([\w.$]+)")


def find_javaps() -> List[str]:
    """Todos los `javap` de la máquina, el del PATH primero, sin duplicados."""
    found: List[str] = []
    seen = set()

    def add(path: Optional[str]) -> None:
        if not path:
            return
        real = os.path.realpath(path)
        if real in seen or not os.access(real, os.X_OK):
            return
        seen.add(real)
        found.append(path)

    add(shutil.which("javap"))
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        add(os.path.join(java_home, "bin", "javap"))
    patterns = [
        "/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/javap",
        os.path.expanduser("~/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/javap"),
        "/usr/lib/jvm/*/bin/javap",
        "/usr/local/opt/openjdk*/bin/javap",
        "/opt/homebrew/opt/openjdk*/bin/javap",
        os.path.expanduser("~/.sdkman/candidates/java/*/bin/javap"),
        os.path.expanduser("~/.jdks/*/bin/javap"),
    ]
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            add(path)
    return found


def default_tools() -> Dict[str, object]:
    javaps = find_javaps()
    tools: Dict[str, object] = {}
    if javaps:
        tools["javap"] = javaps[0]
        tools["javap_alternates"] = javaps[1:]
    return tools


def run_tool(binary: Optional[str], args: List[str], timeout: int = 300) -> Optional[str]:
    if not binary:
        return None
    try:
        out = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout,
                             errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def own_roots(names: List[str], class_root: str, depth: int = 3) -> List[str]:
    """Paquetes raíz del sistema (p. ej. `mx/gob/organismo/`): los jars que los comparten son propios."""
    root = class_root.rstrip("/") + "/"
    roots = set()
    for n in names:
        if n.startswith(root) and n.endswith(".class"):
            parts = n[len(root):].split("/")
            if len(parts) > depth:
                roots.add("/".join(parts[:depth]) + "/")
    return sorted(roots)


def match_any(patterns: List[str], value: str) -> bool:
    return any(re.search(p, value) for p in patterns)


def collect_classes(artifact: Path, class_root: str, package_prefixes: List[str],
                    include_libs: bool, tmpdir: Path) -> List[Tuple[str, Path]]:
    """Copia las clases del artefacto (y de sus jars propios) a JARs temporales.

    Los prefijos de paquete se anclan a un segmento de ruta (`beans/` no casa con
    `xmlbeans/`). "Jar propio" = comparte paquete raíz con las clases del WAR, así
    las librerías de terceros quedan fuera sin listas negras.
    → [(nombre.calificado, jar_que_lo_contiene)] en orden determinístico."""
    anchored = [r"(?:^|/)" + p.lstrip("^/") for p in package_prefixes]
    wanted: List[Tuple[str, Path]] = []
    main_jar = tmpdir / "classes.jar"
    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()
        root = class_root.rstrip("/") + "/"
        with zipfile.ZipFile(main_jar, "w", zipfile.ZIP_STORED) as out:
            for n in sorted(names):
                if n.startswith(root) and n.endswith(".class"):
                    relative = n[len(root):]
                    if not anchored or match_any(anchored, relative):
                        out.writestr(relative, archive.read(n))
                        wanted.append((relative[:-len(".class")].replace("/", "."), main_jar))
        if include_libs:
            own = own_roots(names, class_root)
            for n in sorted(names):
                if not n.endswith(".jar"):
                    continue
                with zipfile.ZipFile(archive.open(n)) as jar:
                    members = [m for m in sorted(jar.namelist())
                               if m.endswith(".class") and any(m.startswith(o) for o in own)
                               and (not anchored or match_any(anchored, m))]
                    if not members:
                        continue
                    lib_jar = tmpdir / "lib" / (Path(n).stem + ".jar")
                    lib_jar.parent.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(lib_jar, "w", zipfile.ZIP_STORED) as out:
                        for m in members:
                            out.writestr(m, jar.read(m))
                            wanted.append((m[:-len(".class")].replace("/", "."), lib_jar))
    return wanted


def split_output(out: str) -> Dict[str, str]:
    """La salida de javap con varias clases → {fqn: su bloque}."""
    outputs: Dict[str, str] = {}
    current: Optional[str] = None
    buffer: List[str] = []
    for line in out.splitlines():
        m = _CLASS_HEADER.match(line)
        if m and not line.startswith(" "):
            if current is not None:
                outputs[current] = "\n".join(buffer)
            current = m.group(1)
            buffer = [line]
        else:
            buffer.append(line)
    if current is not None:
        outputs[current] = "\n".join(buffer)
    return outputs


def _batches(javap: str, classes: List[Tuple[str, Path]], flags: List[str], batch: int) -> Dict[str, str]:
    outputs: Dict[str, str] = {}
    by_root: Dict[Path, List[str]] = {}
    for fqn, root in classes:
        by_root.setdefault(root, []).append(fqn)
    for root, fqns in by_root.items():
        for start in range(0, len(fqns), batch):
            chunk = fqns[start:start + batch]
            out = run_tool(javap, [*flags, "-classpath", str(root), *chunk])
            if out is None:
                for fqn in chunk:  # un lote roto no oculta a los demás
                    single = run_tool(javap, [*flags, "-classpath", str(root), fqn])
                    if single is not None:
                        outputs.update(split_output(single))
                continue
            outputs.update(split_output(out))
    return outputs


def javap_outputs(tools: Dict[str, object], classes: List[Tuple[str, Path]], flags: List[str],
                  batch: int = 40) -> Tuple[Dict[str, str], List[str]]:
    """Corre javap por lotes; lo que el principal no lee se intenta con los alternos.
    → ({fqn: salida}, notas sobre qué javap leyó qué)."""
    javap = str(tools.get("javap") or "")
    notes: List[str] = []
    if not javap:
        return {}, notes
    outputs = _batches(javap, classes, flags, batch)
    missing = [(fqn, root) for fqn, root in classes if fqn not in outputs]
    for alternate in list(tools.get("javap_alternates") or []):
        if not missing:
            break
        extra = _batches(str(alternate), missing, flags, batch)
        if extra:
            outputs.update(extra)
            notes.append(f"{len(extra)} clase(s) que `{javap}` no pudo leer se leyeron con `{alternate}`")
            missing = [(fqn, root) for fqn, root in missing if fqn not in outputs]
    return outputs, notes
