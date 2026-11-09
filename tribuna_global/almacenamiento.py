"""Lectura, escritura atómica y consulta de los archivos JSON."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Rutas construidas desde la ubicación del proyecto, no desde el directorio actual.
BASE_DIR = Path(__file__).resolve().parent
DIR_DATOS = BASE_DIR / "datos"
RUTA_JUGADORES = DIR_DATOS / "jugadores.json"
RUTA_OPINIONES = DIR_DATOS / "opiniones.json"

VERSION_ESQUEMA = 1
SENTIMIENTOS = ("positivo", "negativo", "neutro", "mixto")
CAMPOS_REGISTRO = (
    "id",
    "fecha_utc",
    "jugador_id",
    "jugador_nombre",
    "opinion_original",
    "sentimiento",
    "razon",
    "modelo",
    "version_prompt",
)


class ErrorCatalogo(RuntimeError):
    """El catálogo de jugadores no existe o es inválido."""


class ErrorAlmacenamiento(RuntimeError):
    """El archivo de opiniones no se pudo leer o escribir."""


def estructura_vacia() -> dict:
    return {"version": VERSION_ESQUEMA, "opiniones": []}


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
def cargar_jugadores(ruta: Path = RUTA_JUGADORES) -> list[dict]:
    """Lee y valida el catálogo. Lanza ErrorCatalogo ante cualquier problema."""
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except FileNotFoundError as exc:
        raise ErrorCatalogo(f"No existe el catálogo: {ruta}") from exc
    except json.JSONDecodeError as exc:
        raise ErrorCatalogo(f"El catálogo no es JSON válido (línea {exc.lineno}): {exc.msg}") from exc

    if not isinstance(datos, dict) or not isinstance(datos.get("jugadores"), list):
        raise ErrorCatalogo("El catálogo debe ser un objeto con la lista 'jugadores'.")

    jugadores = datos["jugadores"]
    if not jugadores:
        raise ErrorCatalogo("El catálogo está vacío.")

    vistos: set[str] = set()
    for i, jugador in enumerate(jugadores):
        if not isinstance(jugador, dict):
            raise ErrorCatalogo(f"El elemento {i} del catálogo no es un objeto.")
        for campo in ("id", "nombre", "pais"):
            valor = jugador.get(campo)
            if not isinstance(valor, str) or not valor.strip():
                raise ErrorCatalogo(f"El jugador en la posición {i} no tiene '{campo}' válido.")
        if jugador["id"] in vistos:
            raise ErrorCatalogo(f"Identificador repetido en el catálogo: {jugador['id']}")
        vistos.add(jugador["id"])

    return jugadores


# ---------------------------------------------------------------------------
# Opiniones
# ---------------------------------------------------------------------------
def _validar_estructura_opiniones(datos) -> None:
    if not isinstance(datos, dict):
        raise ErrorAlmacenamiento("opiniones.json debe contener un objeto JSON.")
    if datos.get("version") != VERSION_ESQUEMA:
        raise ErrorAlmacenamiento(
            f"Versión de esquema inesperada en opiniones.json: {datos.get('version')!r}"
        )
    if not isinstance(datos.get("opiniones"), list):
        raise ErrorAlmacenamiento("opiniones.json debe contener la lista 'opiniones'.")
    for i, registro in enumerate(datos["opiniones"]):
        if not isinstance(registro, dict):
            raise ErrorAlmacenamiento(f"El registro {i} no es un objeto.")
        faltantes = [c for c in CAMPOS_REGISTRO if c not in registro]
        if faltantes:
            raise ErrorAlmacenamiento(f"Al registro {i} le faltan campos: {', '.join(faltantes)}")
        if registro["sentimiento"] not in SENTIMIENTOS:
            raise ErrorAlmacenamiento(
                f"El registro {i} tiene un sentimiento inválido: {registro['sentimiento']!r}"
            )


def cargar_opiniones(ruta: Path = RUTA_OPINIONES) -> dict:
    """Lee el diccionario persistente.

    - Si el archivo no existe, devuelve la estructura vacía (sin escribir nada).
    - Si existe pero está dañado o tiene otro esquema, lanza ErrorAlmacenamiento
      y NO toca el archivo.
    """
    ruta = Path(ruta)
    if not ruta.exists():
        return estructura_vacia()
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except json.JSONDecodeError as exc:
        raise ErrorAlmacenamiento(
            f"opiniones.json contiene JSON inválido (línea {exc.lineno}, columna {exc.colno}). "
            "El archivo se conservó sin cambios; revíselo manualmente."
        ) from exc
    except OSError as exc:
        raise ErrorAlmacenamiento(f"No se pudo leer opiniones.json: {exc}") from exc

    _validar_estructura_opiniones(datos)
    return datos


def crear_registro(
    jugador: dict,
    texto: str,
    analisis,
    modelo: str,
    version_prompt: str = "1.0",
    registro_id: str | None = None,
) -> dict:
    """Combina los datos generados por el programa con el análisis del modelo."""
    if not analisis.es_pertinente or analisis.sentimiento is None:
        raise ValueError("Solo se crean registros para opiniones pertinentes con sentimiento.")
    return {
        "id": registro_id or str(uuid.uuid4()),
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jugador_id": jugador["id"],
        "jugador_nombre": jugador["nombre"],
        "opinion_original": texto,
        "sentimiento": analisis.sentimiento,
        "razon": analisis.razon,
        "modelo": modelo,
        "version_prompt": version_prompt,
    }


def _escribir_atomico(ruta: Path, datos: dict) -> None:
    """Escribe en un temporal del mismo directorio y lo reemplaza al final."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, ruta_tmp = tempfile.mkstemp(dir=ruta.parent, prefix=".opiniones_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(ruta_tmp, ruta)
    except BaseException:
        try:
            os.remove(ruta_tmp)
        except OSError:
            pass
        raise


def guardar_opinion(ruta: Path, registro: dict) -> bool:
    """Añade el registro sin perder los anteriores.

    Devuelve True si se escribió y False si ya existía un registro con el mismo id
    (reintento del mismo envío). Lanza ErrorAlmacenamiento si falla la lectura o escritura.
    """
    ruta = Path(ruta)
    datos = cargar_opiniones(ruta)  # si está dañado, lanza error y no se sobrescribe

    if any(r.get("id") == registro["id"] for r in datos["opiniones"]):
        return False

    datos["opiniones"].append(registro)
    try:
        _escribir_atomico(ruta, datos)
    except OSError as exc:
        raise ErrorAlmacenamiento(f"No se pudo escribir opiniones.json: {exc}") from exc
    return True


# ---------------------------------------------------------------------------
# Consultas (no llaman al modelo)
# ---------------------------------------------------------------------------
def consultar_opiniones(
    registros: list[dict],
    jugador_id: str | None = None,
    sentimiento: str | None = None,
) -> list[dict]:
    """Filtra por jugador y/o sentimiento. None significa «todos»."""
    return [
        r
        for r in registros
        if (jugador_id is None or r.get("jugador_id") == jugador_id)
        and (sentimiento is None or r.get("sentimiento") == sentimiento)
    ]


def contar_por_sentimiento(registros: list[dict]) -> dict[str, int]:
    conteo = Counter(r.get("sentimiento") for r in registros)
    return {s: conteo.get(s, 0) for s in SENTIMIENTOS}
