"""Descarga una foto con licencia libre de cada jugador desde Wikimedia Commons.

Se ejecuta una sola vez; la app usa luego los archivos locales de datos/fotos/.
    python herramientas/descargar_fotos.py
Guarda datos/fotos/<id>.<ext> y datos/fotos/creditos.json (autor y licencia, requeridos por CC BY-SA).
Solo descarga las fotos que faltan, así que puede volver a ejecutarse si alguna falló.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR_FOTOS = RAIZ / "datos" / "fotos"
RUTA_CREDITOS = DIR_FOTOS / "creditos.json"
CABECERAS = {"User-Agent": "TribunaGlobal-taller-educativo/1.0 (proyecto academico local)"}

# Título del artículo en la Wikipedia en inglés (desambiguado cuando hace falta).
ARTICULOS = {
    "j001": "Lionel Messi",
    "j002": "Cristiano Ronaldo",
    "j003": "Neymar",
    "j004": "Kylian Mbappé",
    "j005": "Erling Haaland",
    "j006": "Vinícius Júnior",
    "j007": "Mohamed Salah",
    "j008": "Robert Lewandowski",
    "j009": "Luka Modrić",
    "j010": "Kevin De Bruyne",
    "j011": "Luis Suárez (Uruguayan footballer)",
    "j012": "James Rodríguez",
    "j013": "Luis Díaz (footballer, born 1997)",
    "j014": "Son Heung-min",
    "j015": "Sadio Mané",
    "j016": "Pelé",
    "j017": "Diego Maradona",
    "j018": "Zinedine Zidane",
    "j019": "Ronaldinho",
    "j020": "Ronaldo (Brazilian footballer)",
}


def pedir(url: str, intentos: int = 5) -> bytes:
    """GET respetando el encabezado Retry-After cuando el servidor limita (HTTP 429)."""
    for intento in range(intentos):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=CABECERAS), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or intento == intentos - 1:
                raise
            espera = int(exc.headers.get("Retry-After", "10")) + 2
            print(f"  servidor ocupado, esperando {espera} s...")
            time.sleep(espera)
    raise RuntimeError("sin respuesta")


def api(base: str, **params) -> dict:
    params |= {"format": "json", "formatversion": "2"}
    return json.loads(pedir(base + "?" + urllib.parse.urlencode(params)))


def limpiar_html(texto: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", texto or "")).strip()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    DIR_FOTOS.mkdir(parents=True, exist_ok=True)
    creditos = json.loads(RUTA_CREDITOS.read_text("utf-8")) if RUTA_CREDITOS.exists() else {}
    pendientes = {
        jid: t for jid, t in ARTICULOS.items()
        if jid not in creditos or not (DIR_FOTOS / creditos[jid]["archivo"]).exists()
    }
    if not pendientes:
        print("Todas las fotos ya están descargadas.")
        return

    # 1) Una consulta: imagen principal (libre) de cada artículo.
    datos = api(
        "https://en.wikipedia.org/w/api.php",
        action="query", prop="pageimages|description", piprop="name",
        redirects="1", titles="|".join(pendientes.values()),
    )
    normalizados = {n["from"]: n["to"] for n in datos["query"].get("normalized", [])}
    redirigidos = {r["from"]: r["to"] for r in datos["query"].get("redirects", [])}
    paginas = {p["title"]: p for p in datos["query"]["pages"]}

    archivo_por_jid, descripcion = {}, {}
    for jid, titulo in pendientes.items():
        t = normalizados.get(titulo, titulo)
        t = redirigidos.get(t, t)
        pagina = paginas.get(t, {})
        if pagina.get("pageimage"):
            archivo_por_jid[jid] = "File:" + pagina["pageimage"]
            descripcion[jid] = pagina.get("description", "")
        else:
            print(f"FALLO {jid} {titulo}: el artículo no tiene imagen libre")

    # 2) Una consulta a Commons: URL de 400 px, autor y licencia de todas las imágenes.
    info = api(
        "https://commons.wikimedia.org/w/api.php",
        action="query", prop="imageinfo", iiprop="url|extmetadata", iiurlwidth="400",
        titles="|".join(archivo_por_jid.values()),
    )
    norm_c = {n["from"]: n["to"] for n in info["query"].get("normalized", [])}
    paginas_c = {p["title"]: p for p in info["query"]["pages"]}

    # 3) Descarga de cada imagen, con pausa entre peticiones.
    for jid, archivo in archivo_por_jid.items():
        pagina = paginas_c.get(norm_c.get(archivo, archivo), {})
        if not pagina.get("imageinfo"):
            print(f"FALLO {jid}: {archivo} no está en Commons")
            continue
        ii = pagina["imageinfo"][0]
        meta = ii.get("extmetadata", {})
        url_img = ii.get("thumburl") or ii["url"]
        extension = Path(urllib.parse.urlparse(url_img).path).suffix.lower()
        if extension not in (".jpg", ".jpeg", ".png", ".webp"):
            print(f"FALLO {jid}: formato no admitido {extension}")
            continue
        try:
            contenido = pedir(url_img)
        except Exception as exc:
            print(f"FALLO {jid}: {exc}")
            continue
        destino = DIR_FOTOS / f"{jid}{extension}"
        destino.write_bytes(contenido)
        creditos[jid] = {
            "archivo": destino.name,
            "autor": limpiar_html(meta.get("Artist", {}).get("value", "Desconocido")),
            "licencia": limpiar_html(meta.get("LicenseShortName", {}).get("value", "")),
            "fuente": ii.get("descriptionurl", ""),
        }
        print(f"OK    {jid} {ARTICULOS[jid]}: {descripcion[jid]} | {creditos[jid]['licencia']}")
        time.sleep(1.5)

    RUTA_CREDITOS.write_text(json.dumps(dict(sorted(creditos.items())), ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"\n{len(creditos)}/{len(ARTICULOS)} fotos disponibles en {DIR_FOTOS}")


if __name__ == "__main__":
    main()
