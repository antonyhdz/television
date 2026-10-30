import re
import uuid
import argparse
import requests
import xml.etree.ElementTree as ET

from pathlib import Path
from datetime import date, datetime, time, timedelta
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


# ==========================================================
# CALENDARIO DE ACTUALIZACION
# ==========================================================

def obtener_consultas():

    hoy = date.today()
    dia_semana = hoy.weekday()

    # VIERNES: hoy y los siguientes seis días
    if dia_semana == 4:
        consultas = [
            ("", hoy), 
            ("sabado", hoy + timedelta(days=1)),
            ("domingo", hoy + timedelta(days=2)),
            ("lunes", hoy + timedelta(days=3)),
            ("martes", hoy + timedelta(days=4)),
            ("miercoles", hoy + timedelta(days=5)),
        ]

    # MIERCOLES: hoy, jueves y viernes
    elif dia_semana == 2:
        consultas = [
            ("", hoy),
            ("manana", hoy + timedelta(days=1)),
            ("viernes", hoy + timedelta(days=2)),
        ]

    else:
        raise RuntimeError(
            "Este extractor se ejecuta los miércoles y viernes."
        )

    return consultas

    hoy = date.today()
    dia_semana = hoy.weekday()

    # VIERNES: siete dias, incluyendo hoy
    if dia_semana == 4:

        consultas = [
            ("sabado", hoy + timedelta(days=1)),
            ("domingo", hoy + timedelta(days=2)),
            ("lunes", hoy + timedelta(days=3)),
            ("martes", hoy + timedelta(days=4)),
            ("miercoles", hoy + timedelta(days=5)),
            ("ayer", hoy - timedelta(days=1)),
            ("", hoy),  # HOY: viernes
        ]

    # MIERCOLES: jueves, viernes y hoy
    elif dia_semana == 2:

        consultas = [
            ("manana", hoy + timedelta(days=1)),  # Jueves
            ("viernes", hoy + timedelta(days=2)), # Viernes
            ("", hoy),                           # HOY: miercoles
        ]

    else:
        raise RuntimeError(
            "Este extractor se ejecuta los viernes y miercoles."
        )

    return consultas

    hoy = date.today()
    dia_semana = hoy.weekday()

    consultas = []

    # VIERNES: siete dias
    if dia_semana == 4:

        consultas = [
            ("sabado", hoy + timedelta(days=1)),
            ("domingo", hoy + timedelta(days=2)),
            ("lunes", hoy + timedelta(days=3)),
            ("martes", hoy + timedelta(days=4)),
            ("miercoles", hoy + timedelta(days=5)),
            ("ayer", hoy - timedelta(days=1)),
            ("", hoy),
        ]

    # MIERCOLES: solamente jueves y viernes
    elif dia_semana == 2:

        consultas = [
            ("manana", hoy + timedelta(days=1)),
            ("viernes", hoy + timedelta(days=2)),
        ]

    else:
        raise RuntimeError(
            "Este extractor se ejecuta los viernes y miercoles."
        )

    return consultas


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
        endpoint = (
            f"{BASE_URL}/{COUNTRY}/async/guide/all/-360"
        )
        referer = f"{BASE_URL}/{COUNTRY}/programacion"

    params = {
        "cb": uuid.uuid4().hex
    }

    headers = HEADERS.copy()
    headers["Referer"] = referer

    print(f"\nDescargando: {segmento or 'HOY'}")

    response = requests.get(
        endpoint,
        params=params,
        headers=headers,
        timeout=45
    )

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    if not soup.select("#channels .channel"):
        raise RuntimeError(
            f"No se encontraron canales para: {segmento or 'HOY'}"
        )

    return soup


# ==========================================================
# EXTRAER IMAGEN DE FONDO
# ==========================================================

def extraer_imagen(elemento):

    if not elemento:
        return ""

    style = elemento.get("style", "")

    match = re.search(
        r"background-image\s*:\s*url\(['\"]?(.*?)['\"]?\)",
        style,
        re.IGNORECASE
    )

    if match:
        return match.group(1)

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

    canales = []
    programas = []

    bloques = soup.select("#channels .channel")

    for bloque in bloques:

        enlace_canal = bloque.select_one("a.c")

        if not enlace_canal:
            continue

        href_canal = enlace_canal.get("href", "")

        slug = href_canal.rstrip("/").split("/")[-1]

        if not slug:
            continue

        channel_id = f"mitv.{slug}"

        titulo_canal = enlace_canal.select_one("h3")

        if not titulo_canal:
            continue

        nombre_canal = titulo_canal.get_text(
            " ", strip=True
        )

        imagen_canal = enlace_canal.select_one("img")

        logo = ""

        if imagen_canal:
            logo = imagen_canal.get("src", "")

        canales.append({
            "id": channel_id,
            "nombre": nombre_canal,
            "logo": logo
        })

        lista = bloque.select_one("ul.broadcasts")

        if not lista:
            continue

        emisiones = []

        for item in lista.select("li"):

            enlace = item.select_one("a.program-link")

            if not enlace:
                continue

            hora_elemento = item.select_one(".time")
            titulo_elemento = item.select_one(".title")

            if not hora_elemento or not titulo_elemento:
                continue

            hora_texto = hora_elemento.get_text(
                strip=True
            )

            titulo = titulo_elemento.get_text(
                " ", strip=True
            )

            try:
                hora = datetime.strptime(
                    hora_texto, "%H:%M"
                ).time()
            except ValueError:
                continue

            subtitulo_elemento = item.select_one(".sub-title")
            sinopsis_elemento = item.select_one(".synopsis")
            imagen_elemento = item.select_one(".image")

            subtitulo = (
                subtitulo_elemento.get_text(" ", strip=True)
                if subtitulo_elemento else ""
            )

            sinopsis = (
                sinopsis_elemento.get_text(" ", strip=True)
                if sinopsis_elemento else ""
            )

            imagen = extraer_imagen(imagen_elemento)

            enlace_programa = enlace.get("href", "")

            url_programa = urljoin(
                BASE_URL, enlace_programa
            )

            emisiones.append({
                "hora": hora,
                "titulo": titulo,
                "subtitulo": subtitulo,
                "sinopsis": sinopsis,
                "imagen": imagen,
                "url": url_programa
            })

        # Ordenar emisiones y calcular cambios de dia
        emisiones.sort(key=lambda x: x["hora"])

        emisiones_con_fecha = []
        fecha_actual = fecha_programacion
        hora_anterior = None

        for emision in emisiones:

            hora = emision["hora"]

            if hora_anterior and hora <= hora_anterior:
                fecha_actual += timedelta(days=1)

            inicio = datetime.combine(
                fecha_actual, hora
            )

            emisiones_con_fecha.append({
                **emision,
                "inicio": inicio
            })

            hora_anterior = hora

        # Crear programas XMLTV
        for indice, emision in enumerate(emisiones_con_fecha):

            inicio = emision["inicio"]

            programa = {
                "channel": channel_id,
                "start": fecha_xmltv(inicio),
                "stop": "",
                "titulo": emision["titulo"],
                "subtitulo": emision["subtitulo"],
                "sinopsis": emision["sinopsis"],
                "imagen": emision["imagen"],
                "url": emision["url"]
            }

            # El siguiente programa determina el final
            if indice + 1 < len(emisiones_con_fecha):

                siguiente = emisiones_con_fecha[indice + 1]

                programa["stop"] = fecha_xmltv(
                    siguiente["inicio"]
                )

            programas.append(programa)

    return canales, programas


# ==========================================================
# CREAR ELEMENTOS XMLTV
# ==========================================================

def crear_canal(root, canal):

    elemento = ET.Element(
        "channel",
        {"id": canal["id"]}
    )

    nombre = ET.SubElement(elemento, "display-name")
    nombre.text = canal["nombre"]

    if canal["logo"]:
        ET.SubElement(
            elemento,
            "icon",
            {"src": canal["logo"]}
        )

    return elemento


def crear_programa(root, datos):

    atributos = {
        "channel": datos["channel"],
        "start": datos["start"]
    }

    if datos["stop"]:
        atributos["stop"] = datos["stop"]

    elemento = ET.Element(
        "programme",
        atributos
    )

    titulo = ET.SubElement(elemento, "title")
    titulo.set("lang", "es")
    titulo.text = datos["titulo"]

    if datos["subtitulo"]:

        subtitulo = ET.SubElement(
            elemento, "sub-title"
        )

        subtitulo.set("lang", "es")
        subtitulo.text = datos["subtitulo"]

    if datos["sinopsis"]:

        descripcion = ET.SubElement(
            elemento, "desc"
        )

        descripcion.set("lang", "es")
        descripcion.text = datos["sinopsis"]

    if datos["imagen"]:

        ET.SubElement(
            elemento,
            "icon",
            {"src": datos["imagen"]}
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

    root.set(
        "generator-info-name",
        "Extractor mi.tv El Salvador"
    )

    root.set(
        "generator-info-url",
        "https://mi.tv/sv/programacion"
    )

    return root


# ==========================================================
# ACTUALIZAR XML SIN PERDER OTROS DIAS
# ==========================================================

def actualizar_xml(canales_nuevos, programas_nuevos, fechas):

    root = cargar_xml()

    fechas_actualizadas = {
        fecha.strftime("%Y%m%d")
        for fecha in fechas
    }

    # Actualizar informacion de canales
    canales_existentes = {
        c.get("id"): c
        for c in root.findall("channel")
    }

    for canal in canales_nuevos:

        nuevo = crear_canal(root, canal)

        anterior = canales_existentes.get(canal["id"])

        if anterior is not None:
            root.remove(anterior)

        root.append(nuevo)

    # Eliminar solamente los programas de las fechas descargadas
    for programa in list(root.findall("programme")):

        inicio = programa.get("start", "")

        if inicio[:8] in fechas_actualizadas:
            root.remove(programa)

    # Evitar programas duplicados
    existentes = set()

    for programa in root.findall("programme"):

        clave = (
            programa.get("channel"),
            programa.get("start"),
            programa.findtext("title", "")
        )

        existentes.add(clave)

    for datos in programas_nuevos:

        clave = (
            datos["channel"],
            datos["start"],
            datos["titulo"]
        )

        if clave in existentes:
            continue

        root.append(
            crear_programa(root, datos)
        )

        existentes.add(clave)

    # Ordenar programas cronologicamente
    programas = root.findall("programme")

    programas.sort(
        key=lambda x: (
            x.get("start", ""),
            x.get("channel", "")
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
        xml_declaration=True
    )

    temporal.replace(OUTPUT_FILE)


# ==========================================================
# PROCESO PRINCIPAL
# ==========================================================

def ejecutar():

    consultas = obtener_consultas()

    canales_totales = {}
    programas_totales = []
    fechas_actualizadas = []

    print("=" * 55)
    print("       EXTRACTOR EPG - MI.TV EL SALVADOR")
    print("=" * 55)

    print(f"Fecha de ejecucion: {date.today()}")

    for segmento, fecha in consultas:

        soup = descargar_programacion(segmento)

        canales, programas = extraer_datos(
            soup,
            fecha
        )

        print(
            f"Fecha: {fecha} | "
            f"Canales: {len(canales)} | "
            f"Programas: {len(programas)}"
        )

        for canal in canales:
            canales_totales[canal["id"]] = canal

        programas_totales.extend(programas)

        fechas_actualizadas.append(fecha)

    actualizar_xml(
        list(canales_totales.values()),
        programas_totales,
        fechas_actualizadas
    )

    print("\n" + "=" * 55)
    print("ACTUALIZACION COMPLETADA")
    print("=" * 55)

    print(f"Canales: {len(canales_totales)}")
    print(f"Programas descargados: {len(programas_totales)}")
    print(f"Fechas actualizadas: {len(fechas_actualizadas)}")
    print(f"Archivo: {OUTPUT_FILE.resolve()}")


# ==========================================================
# INICIO
# ==========================================================

if __name__ == "__main__":

    try:
        ejecutar()

    except requests.RequestException as error:

        print("\nERROR DE CONEXION:")
        print(error)

    except Exception as error:

        print("\nERROR:")
        print(error)