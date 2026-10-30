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
# MIERCOLES: hoy, jueves y viernes.
# VIERNES: hoy, sabado, domingo, lunes, martes y miercoles.
# NUNCA descarga AYER.
# ==========================================================

def obtener_consultas():
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
        "Este extractor se ejecuta los miercoles y viernes."
    )


# ==========================================================
# DESCARGAR PROGRAMACION
# ==========================================================

def descargar_programacion(segmento):
    if segmento:
        endpoint = (
            f"{BASE_URL}/{COUNTRY}/async/guide/all/"
            f"{segmento}/-360"
        )
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
        raise RuntimeError(
            f"No se encontraron canales para {segmento or 'HOY'}. "
            f"Respuesta HTTP: {response.status_code}. "
            "Se conserva el XML anterior."
        )

    # Guardar una copia de la respuesta para poder inspeccionarla
    # si mi.tv cambia su estructura o devuelve datos incompletos.
    nombre_debug = f"debug_mitv_{segmento or 'hoy'}.html"
    Path(nombre_debug).write_text(response.text, encoding="utf-8")

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

    # Imagen en background-image
    style = elemento.get("style", "")
    match = re.search(
        r"""background-image\s*:\s*url\(['"]?(.*?)['"]?\)""",
        style,
        re.IGNORECASE,
    )
    if match:
        return urljoin(BASE_URL, match.group(1).strip())

    # Imagen mediante <img>
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

    valor = texto_de(elemento)
    valor = valor.replace(".", ":").strip()

    # Acepta HH:MM y H:MM.
    match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", valor)
    if not match:
        return None

    hora, minuto = int(match.group(1)), int(match.group(2))
    if hora > 23 or minuto > 59:
        return None

    return datetime.strptime(
        f"{hora:02d}:{minuto:02d}", "%H:%M"
    ).time()


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

    # Respaldo: texto del enlace, quitando hora/subtitulo/sinopsis.
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


def fecha_desde_xmltv(valor):
    if not valor:
        return None
    try:
        return datetime.strptime(valor[:14], "%Y%m%d%H%M%S").date()
    except ValueError:
        return None


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

        # Evita convertir segmentos como ayer/sabado/domingo
        # en canales XMLTV. Solo acepta enlaces reales de canales.
        if not href_canal.startswith(f"/{COUNTRY}/canales/"):
            continue

        if not slug:
            continue

        channel_id = f"mitv.{slug}"

        titulo_canal = enlace_canal.select_one("h3")
        nombre_canal = texto_de(titulo_canal)
        if not nombre_canal:
            nombre_canal = texto_de(enlace_canal)

        if not nombre_canal:
            continue

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
            # IMPORTANTE:
            # No descartamos un programa por no tener a.program-link.
            # Algunas fichas pueden tener hora y titulo sin ese enlace.
            enlace = item.select_one("a.program-link")
            if enlace is None:
                enlace = item.select_one("a[href*='/programas/']")

            hora = extraer_hora(item)
            titulo = extraer_titulo(item, enlace)

            if hora is None or not titulo:
                continue

            subtitulo = texto_de(
                item.select_one(".sub-title")
            )
            sinopsis = texto_de(
                item.select_one(".synopsis")
            )
            imagen = extraer_imagen(
                item.select_one(".image")
            )

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

        # NO ordenar por hora. Se conserva el orden original del HTML,
        # que es el orden cronologico publicado por mi.tv. Ordenar solo
        # por hora puede mover emisiones de despues de medianoche.
        emisiones_con_fecha = []
        fecha_actual = fecha_programacion
        hora_anterior = None

        for emision in emisiones:
            hora = emision["hora"]

            # Cuando la hora retrocede en el listado, se cruza medianoche.
            if hora_anterior is not None and hora < hora_anterior:
                fecha_actual += timedelta(days=1)

            inicio = datetime.combine(fecha_actual, hora)

            emisiones_con_fecha.append({
                **emision,
                "inicio": inicio,
            })

            hora_anterior = hora

        for indice, emision in enumerate(emisiones_con_fecha):
            inicio = emision["inicio"]
            stop = ""

            if indice + 1 < len(emisiones_con_fecha):
                stop = fecha_xmltv(
                    emisiones_con_fecha[indice + 1]["inicio"]
                )

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

        print(
            f"  {nombre_canal}: {len(emisiones)} programas | "
            f"primero: {emisiones[0]['hora'].strftime('%H:%M')} "
            f"{emisiones[0]['titulo']} | "
            f"ultimo: {emisiones[-1]['hora'].strftime('%H:%M')} "
            f"{emisiones[-1]['titulo']}"
        )

    return list(canales.values()), programas


# ==========================================================
# CREAR ELEMENTOS XMLTV
# ==========================================================

def crear_canal(canal):
    elemento = ET.Element("channel", {"id": canal["id"]})

    nombre = ET.SubElement(elemento, "display-name")
    nombre.text = canal["nombre"]

    if canal["logo"]:
        ET.SubElement(
            elemento,
            "icon",
            {"src": canal["logo"]},
        )

    return elemento


def crear_programa(datos):
    atributos = {
        "channel": datos["channel"],
        "start": datos["start"],
    }

    if datos["stop"]:
        atributos["stop"] = datos["stop"]

    elemento = ET.Element("programme", atributos)

    titulo = ET.SubElement(elemento, "title", {"lang": "es"})
    titulo.text = datos["titulo"]

    if datos["subtitulo"]:
        subtitulo = ET.SubElement(
            elemento, "sub-title", {"lang": "es"}
        )
        subtitulo.text = datos["subtitulo"]

    if datos["sinopsis"]:
        descripcion = ET.SubElement(
            elemento, "desc", {"lang": "es"}
        )
        descripcion.text = datos["sinopsis"]

    if datos["imagen"]:
        ET.SubElement(
            elemento, "icon", {"src": datos["imagen"]}
        )

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
    root.set(
        "generator-info-url",
        "https://mi.tv/sv/programacion",
    )
    return root


# ==========================================================
# ACTUALIZAR XML SIN PERDER OTROS DIAS
# ==========================================================

def actualizar_xml(canales_nuevos, programas_nuevos, fechas):
    root = cargar_xml()

    fechas_actualizadas = {
        fecha.strftime("%Y%m%d") for fecha in fechas
    }

    # Actualizar datos de canales sin duplicarlos.
    canales_existentes = {
        canal.get("id"): canal
        for canal in root.findall("channel")
    }

    for canal in canales_nuevos:
        nuevo = crear_canal(canal)
        anterior = canales_existentes.get(canal["id"])

        if anterior is not None:
            root.remove(anterior)

        root.append(nuevo)

    # Reemplazar los programas de las fechas descargadas.
    # Se usa la fecha del atributo start, no el dia de ejecucion.
    for programa in list(root.findall("programme")):
        inicio = programa.get("start", "")
        if inicio[:8] in fechas_actualizadas:
            root.remove(programa)

    existentes = {
        (
            p.get("channel"),
            p.get("start"),
            p.findtext("title", ""),
        )
        for p in root.findall("programme")
    }

    agregados = 0
    for datos in programas_nuevos:
        clave = (
            datos["channel"],
            datos["start"],
            datos["titulo"],
        )

        if clave in existentes:
            continue

        root.append(crear_programa(datos))
        existentes.add(clave)
        agregados += 1

    # Orden cronologico global.
    programas = root.findall("programme")
    programas.sort(
        key=lambda p: (
            p.get("start", ""),
            p.get("channel", ""),
        )
    )

    for programa in list(root.findall("programme")):
        root.remove(programa)

    for programa in programas:
        root.append(programa)

    ET.indent(root, space="    ")

    temporal = OUTPUT_FILE.with_suffix(".tmp")
    ET.ElementTree(root).write(
        temporal,
        encoding="utf-8",
        xml_declaration=True,
    )
    temporal.replace(OUTPUT_FILE)

    return agregados


# ==========================================================
# VALIDAR RESULTADO
# ==========================================================

def validar_xml():
    tree = ET.parse(OUTPUT_FILE)
    root = tree.getroot()

    ids_canales = {
        canal.get("id") for canal in root.findall("channel")
    }

    programas = root.findall("programme")
    canales_sin_definir = sorted({
        p.get("channel")
        for p in programas
        if p.get("channel") not in ids_canales
    })

    if canales_sin_definir:
        print(
            "AVISO: hay programas sin canal definido:",
            ", ".join(canales_sin_definir),
        )

    print(
        f"Validacion XML: {len(ids_canales)} canales, "
        f"{len(programas)} programas."
    )


# ==========================================================
# PROCESO PRINCIPAL
# ==========================================================

def ejecutar():
    consultas = obtener_consultas()

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

        print(
            f"Fecha {fecha}: {len(canales)} canales | "
            f"{len(programas)} programas extraidos"
        )

        if not canales or not programas:
            raise RuntimeError(
                f"Descarga incompleta para {fecha}. "
                "No se modifico el XML existente. "
                "Revisa el archivo debug_mitv_*.html."
            )

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
    print("HTML de diagnostico: debug_mitv_*.html")


# ==========================================================
# INICIO
# ==========================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extractor XMLTV de mi.tv El Salvador"
    )
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="Ejecuta el extractor aunque hoy no sea miercoles o viernes.",
    )
    args = parser.parse_args()

    try:
        if args.forzar:
            # Para pruebas manuales fuera de miercoles/viernes:
            # descarga unicamente HOY.
            consultas_original = obtener_consultas
            obtener_consultas = lambda: [("", date.today())]

        ejecutar()

    except requests.RequestException as error:
        print("\nERROR DE CONEXION:")
        print(error)

    except Exception as error:
        print("\nERROR:")
        print(error)
