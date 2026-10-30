import re
import uuid
import argparse
import requests
import xml.etree.ElementTree as ET

from pathlib import Path
from datetime import date, datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urljoin


# ==========================================================
# CONFIGURACION
# ==========================================================

BASE_URL = "https://mi.tv"
COUNTRY = "sv"
OUTPUT_FILE = Path("mi_tv.xml")
TIMEZONE = "-0600"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://mi.tv/sv/programacion",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ==========================================================
# CALENDARIO DE ACTUALIZACION
# ==========================================================

def obtener_consultas(forzar=False):
    if forzar:
        return [("", date.today())]

    hoy = date.today()
    dia_semana = hoy.weekday()

    if dia_semana == 4:  # Viernes
        return [
            ("", hoy),
            ("sabado", hoy + timedelta(days=1)),
            ("domingo", hoy + timedelta(days=2)),
            ("lunes", hoy + timedelta(days=3)),
            ("martes", hoy + timedelta(days=4)),
            ("miercoles", hoy + timedelta(days=5)),
        ]

    if dia_semana == 2:  # Miercoles
        return [
            ("", hoy),
            ("manana", hoy + timedelta(days=1)),
            ("viernes", hoy + timedelta(days=2)),
        ]

    raise RuntimeError(
        "Este extractor se ejecuta los miercoles y viernes. Usa --forzar para ejecutar hoy."
    )


# ==========================================================
# DESCARGAR PROGRAMACION
# ==========================================================

def descargar_programacion(segmento):
    if segmento:
        endpoint = f"{BASE_URL}/{COUNTRY}/async/guide/all/{segmento}/-360"
        referer = f"{BASE_URL}/{COUNTRY}/programacion/{segmento}"
    else:
        endpoint = f"{BASE_URL}/{COUNTRY}/async/guide/all/-360"
        referer = f"{BASE_URL}/{COUNTRY}/programacion"

    params = {"cb": uuid.uuid4().hex}
    headers = {"Referer": referer}

    print(f"\nDescargando: {segmento or 'HOY'}")

    response = SESSION.get(
        endpoint,
        params=params,
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    bloques = soup.select("#channels .channel")

    if not bloques:
        # Guardar HTML para depuración como indicaba el mensaje original
        debug_file = Path(f"debug_mitv_{segmento or 'hoy'}.html")
        debug_file.write_text(response.text, encoding="utf-8")
        
        raise RuntimeError(
            f"No se encontraron canales para {segmento or 'HOY'}. "
            f"Respuesta HTTP: {response.status_code}. "
            f"Se guardó el HTML en {debug_file.name} para análisis."
        )

    print(
        f"Respuesta recibida: {len(response.text):,} caracteres | "
        f"{len(bloques)} canales | HTTP {response.status_code}"
    )

    return soup


# ==========================================================
# UTILIDADES HTML
# ==========================================================

def texto_de(elemento):
    if not elemento:
        return ""
    return elemento.get_text(" ", strip=True)


def extraer_imagen(elemento):
    if not elemento:
        return ""

    style = elemento.get("style", "")
    match = re.search(
        r"""background-image\s*:\s*url\(['"]?(.*?)['"]?\)""",
        style,
        re.IGNORECASE,
    )
    if match:
        return urljoin(BASE_URL, match.group(1).strip())

    imagen = elemento.select_one("img")
    if imagen:
        return (
            imagen.get("src")
            or imagen.get("data-src")
            or imagen.get("data-original")
            or ""
        )

    return ""


def extraer_hora(item):
    elemento = (
        item.select_one(".time")
        or item.select_one("[class*='time']")
        or item.select_one("time")
    )
    if not elemento:
        return None

    valor = texto_de(elemento).replace(".", ":").strip()
    match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", valor)
    if not match:
        return None

    hora, minuto = int(match.group(1)), int(match.group(2))
    if hora > 23 or minuto > 59:
        return None

    return datetime.strptime(f"{hora:02d}:{minuto:02d}", "%H:%M").time()


def extraer_titulo(item, enlace):
    elemento = (
        item.select_one(".title")
        or item.select_one("[class*='title']")
        or item.select_one("h3")
        or item.select_one("h4")
    )

    titulo = texto_de(elemento)
    if titulo:
        return titulo

    if enlace:
        copia = BeautifulSoup(str(enlace), "html.parser")
        for selector in (".time", ".sub-title", ".synopsis"):
            for nodo in copia.select(selector):
                nodo.decompose()
        return copia.get_text(" ", strip=True)

    return ""


# ==========================================================
# CONVERTIR FECHA XMLTV
# ==========================================================

def fecha_xmltv(fecha):
    return fecha.strftime("%Y%m%d%H%M%S") + " " + TIMEZONE


# ==========================================================
# EXTRAER CANALES Y PROGRAMAS
# ==========================================================

def extraer_datos(soup, fecha_programacion):
    canales = {}
    programas = []

    bloques = soup.select("#channels .channel")

    for bloque in bloques:
        enlace_canal = bloque.select_one("a.c")
        if not enlace_canal:
            continue

        href_canal = enlace_canal.get("href", "").strip()
        slug = href_canal.rstrip("/").split("/")[-1]

        if not href_canal.startswith(f"/{COUNTRY}/canales/") or not slug:
            continue

        channel_id = f"mitv.{slug}"

        # --- CORRECCIÓN AQUÍ: Obtener el nombre antes de usarlo ---
        titulo_canal = texto_de(enlace_canal.select_one("h3"))
        if not titulo_canal:
            titulo_canal = texto_de(enlace_canal)

        if not titulo_canal:
            continue

        nombre_canal = f"{COUNTRY.upper()} - MiTV - {titulo_canal}"
        # ---------------------------------------------------------

        imagen_canal = enlace_canal.select_one("img")
        logo = ""
        if imagen_canal:
            logo = (
                imagen_canal.get("src")
                or imagen_canal.get("data-src")
                or imagen_canal.get("data-original")
                or ""
            )
            if logo:
                logo = urljoin(BASE_URL, logo)

        canales[channel_id] = {
            "id": channel_id,
            "nombre": nombre_canal,
            "logo": logo,
        }

        lista = bloque.select_one("ul.broadcasts")
        if not lista:
            print(f"  AVISO: {nombre_canal}: no tiene ul.broadcasts")
            continue

        emisiones = []
        items = lista.select("li")

        for item in items:
            enlace = item.select_one("a.program-link")
            if enlace is None:
                enlace = item.select_one("a[href*='/programas/']")

            hora = extraer_hora(item)
            titulo = extraer_titulo(item, enlace)

            if hora is None or not titulo:
                continue

            subtitulo = texto_de(item.select_one(".sub-title"))
            sinopsis = texto_de(item.select_one(".synopsis"))
            imagen = extraer_imagen(item.select_one(".image"))

            url_programa = ""
            if enlace:
                href = enlace.get("href", "").strip()
                if href:
                    url_programa = urljoin(BASE_URL, href)

            emisiones.append({
                "hora": hora,
                "titulo": titulo,
                "subtitulo": subtitulo,
                "sinopsis": sinopsis,
                "imagen": imagen,
                "url": url_programa,
            })

        if not emisiones:
            print(f"  AVISO: {nombre_canal}: 0 programas reconocidos")
            continue

        emisiones_con_fecha = []
        fecha_actual = fecha_programacion
        hora_anterior = None

        for emision in emisiones:
            hora = emision["hora"]
            if hora_anterior is not None and hora < hora_anterior:
                fecha_actual += timedelta(days=1)

            inicio = datetime.combine(fecha_actual, hora)
            emisiones_con_fecha.append({**emision, "inicio": inicio})
            hora_anterior = hora

        for indice, emision in enumerate(emisiones_con_fecha):
            inicio = emision["inicio"]
            stop = ""

            if indice + 1 < len(emisiones_con_fecha):
                stop = fecha_xmltv(emisiones_con_fecha[indice + 1]["inicio"])

            programas.append({
                "channel": channel_id,
                "start": fecha_xmltv(inicio),
                "stop": stop,
                "titulo": emision["titulo"],
                "subtitulo": emision["subtitulo"],
                "sinopsis": emision["sinopsis"],
                "imagen": emision["imagen"],
                "url": emision["url"],
            })

    return list(canales.values()), programas


# ==========================================================
# CREAR ELEMENTOS XMLTV
# ==========================================================

def crear_canal(canal):
    elemento = ET.Element("channel", {"id": canal["id"]})
    nombre = ET.SubElement(elemento, "display-name")
    nombre.text = canal["nombre"]

    if canal["logo"]:
        ET.SubElement(elemento, "icon", {"src": canal["logo"]})

    return elemento


def crear_programa(datos):
    atributos = {"channel": datos["channel"], "start": datos["start"]}
    if datos["stop"]:
        atributos["stop"] = datos["stop"]

    elemento = ET.Element("programme", atributos)

    titulo = ET.SubElement(elemento, "title", {"lang": "es"})
    titulo.text = datos["titulo"]

    if datos["subtitulo"]:
        subtitulo = ET.SubElement(elemento, "sub-title", {"lang": "es"})
        subtitulo.text = datos["subtitulo"]

    if datos["sinopsis"]:
        descripcion = ET.SubElement(elemento, "desc", {"lang": "es"})
        descripcion.text = datos["sinopsis"]

    if datos["imagen"]:
        ET.SubElement(elemento, "icon", {"src": datos["imagen"]})

    if datos["url"]:
        url = ET.SubElement(elemento, "url")
        url.text = datos["url"]

    return elemento


# ==========================================================
# CARGAR O CREAR XMLTV
# ==========================================================

def cargar_xml():
    if OUTPUT_FILE.exists():
        try:
            return ET.parse(OUTPUT_FILE).getroot()
        except ET.ParseError:
            print("XML anterior invalido. Se creara uno nuevo.")

    root = ET.Element("tv")
    root.set("generator-info-name", "Extractor mi.tv El Salvador")
    root.set("generator-info-url", "https://mi.tv/sv/programacion")
    return root


# ==========================================================
# ACTUALIZAR XML SIN PERDER OTROS DIAS
# ==========================================================

def actualizar_xml(canales_nuevos, programas_nuevos, fechas):
    root = cargar_xml()
    fechas_actualizadas = {fecha.strftime("%Y%m%d") for fecha in fechas}

    canales_existentes = {
        canal.get("id"): canal for canal in root.findall("channel")
    }

    for canal in canales_nuevos:
        nuevo = crear_canal(canal)
        anterior = canales_existentes.get(canal["id"])
        if anterior is not None:
            root.remove(anterior)
        root.append(nuevo)

    for programa in list(root.findall("programme")):
        inicio = programa.get("start", "")
        if inicio[:8] in fechas_actualizadas:
            root.remove(programa)

    existentes = {
        (p.get("channel"), p.get("start"), p.findtext("title", ""))
        for p in root.findall("programme")
    }

    agregados = 0
    for datos in programas_nuevos:
        clave = (datos["channel"], datos["start"], datos["titulo"])
        if clave in existentes:
            continue

        root.append(crear_programa(datos))
        existentes.add(clave)
        agregados += 1

    programas = root.findall("programme")
    programas.sort(key=lambda p: (p.get("start", ""), p.get("channel", "")))

    for programa in list(root.findall("programme")):
        root.remove(programme if 'programme' in locals() else programa) # Limpieza segura

    for programa in programas:
        root.append(programa)

    ET.indent(root, space="    ")

    temporal = OUTPUT_FILE.with_suffix(".tmp")
    ET.ElementTree(root).write(temporal, encoding="utf-8", xml_declaration=True)
    temporal.replace(OUTPUT_FILE)

    return agregados


# ==========================================================
# VALIDAR RESULTADO
# ==========================================================

def validar_xml():
    tree = ET.parse(OUTPUT_FILE)
    root = tree.getroot()

    ids_canales = {canal.get("id") for canal in root.findall("channel")}
    programas = root.findall("programme")
    canales_sin_definir = sorted({
        p.get("channel") for p in programas if p.get("channel") not in ids_canales
    })

    if canales_sin_definir:
        print("AVISO: hay programas sin canal definido:", ", ".join(canales_sin_definir))

    print(f"Validacion XML: {len(ids_canales)} canales, {len(programas)} programas.")


# ==========================================================
# PROCESO PRINCIPAL
# ==========================================================

def ejecutar(forzar=False):
    consultas = obtener_consultas(forzar=forzar)

    canales_totales = {}
    programas_totales = []
    fechas_actualizadas = []

    print("=" * 60)
    print("       EXTRACTOR EPG - MI.TV EL SALVADOR")
    print("=" * 60)
    print(f"Fecha de ejecucion: {date.today()}")

    for segmento, fecha in consultas:
        soup = descargar_programacion(segmento)
        canales, programas = extraer_datos(soup, fecha)

        print(f"Fecha {fecha}: {len(canales)} canales | {len(programas)} programas extraidos")

        if not canales or not programas:
            raise RuntimeError(f"Descarga incompleta para {fecha}. No se modifico el XML existente.")

        for canal in canales:
            canales_totales[canal["id"]] = canal

        programas_totales.extend(programas)
        fechas_actualizadas.append(fecha)

    agregados = actualizar_xml(
        list(canales_totales.values()),
        programas_totales,
        fechas_actualizadas,
    )

    validar_xml()

    print("\n" + "=" * 60)
    print("ACTUALIZACION COMPLETADA")
    print("=" * 60)
    print(f"Canales descargados: {len(canales_totales)}")
    print(f"Programas recibidos: {len(programas_totales)}")
    print(f"Programas agregados al XML: {agregados}")
    print(f"Fechas actualizadas: {len(fechas_actualizadas)}")
    print(f"Archivo XML: {OUTPUT_FILE.resolve()}")


# ==========================================================
# INICIO
# ==========================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extractor XMLTV de mi.tv El Salvador")
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="Ejecuta el extractor descargando únicamente HOY, ignorando el día de la semana.",
    )
    args = parser.parse_args()

    try:
        ejecutar(forzar=args.forzar)

    except requests.RequestException as error:
        print("\nERROR DE CONEXION:")
        print(error)

    except Exception as error:
        print("\nERROR:")
        print(error)