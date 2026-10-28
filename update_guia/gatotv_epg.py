from __future__ import annotations

import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.gatotv.com"
CHANNELS_URL = f"{BASE_URL}/canales_de_tv"

# GatoTV interpreta los horarios de su parrilla en EST (UTC-5).
DEFAULT_TZ = timezone(timedelta(hours=-5))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
}

CATEGORY_MAP = {
    "pelicula": "Película / Cine",
    "programa": "Programa",
    "serie": "Serie",
    "caricatura": "Infantil / Animación",
    "deporte": "Deportes",
    "telenovela": "Telenovela",
    "noticiero": "Noticiero / Información",
    "documental": "Documental",
    "musical": "Música",
}


def clean_text(value: str) -> str:
    """Normaliza espacios y elimina espacios al principio y al final."""
    return re.sub(r"\s+", " ", value or "").strip()


def log_error(log_path: Path, message: str) -> None:
    """Añade un error al archivo de registro con marca temporal."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as file:
        file.write(f"[{timestamp}] {message}\n")


def get_soup(session: requests.Session, url: str) -> BeautifulSoup:
    """Descarga una página con hasta tres intentos y reintentos exponenciales."""
    last_error = None

    for attempt in range(1, 4):
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()

            if (
                not response.encoding
                or response.encoding.lower() == "iso-8859-1"
            ):
                response.encoding = response.apparent_encoding or "utf-8"

            return BeautifulSoup(response.text, "html.parser")

        except requests.RequestException as exc:
            last_error = exc
            time.sleep(attempt * 2)

    raise RuntimeError(f"No se pudo descargar {url}: {last_error}")


def extract_logo(soup: BeautifulSoup) -> str | None:
    """
    Busca el logo del canal en el HTML.
    Reconoce imágenes cuyo atributo alt contenga 'logo canal' o cuya URL apunte a logos de canales.
    """
    # 1. Búsqueda en etiquetas <img>
    for img in soup.find_all("img"):
        alt = img.get("alt", "").strip().lower()
        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-original")
        )
        if not src:
            continue

        if alt.startswith("logo canal") or "logos/canales" in src:
            return urljoin(BASE_URL + "/", src)

    # 2. Búsqueda en elementos <picture><source>
    for source in soup.find_all("source"):
        srcset = source.get("srcset", "")
        if "logos/canales" in srcset:
            first_url = srcset.split(",")[0].strip().split(" ")[0]
            if first_url:
                return urljoin(BASE_URL + "/", first_url)

    return None


def discover_channels(session: requests.Session) -> list[dict]:
    """Obtiene el catálogo general de canales publicado por GatoTV."""
    print(f"Descargando catálogo: {CHANNELS_URL}")
    soup = get_soup(session, CHANNELS_URL)

    channels = {}

    for link in soup.select('a[href*="/canal/"]'):
        href = link.get("href", "").strip()
        name = clean_text(link.get_text(" ", strip=True))

        if not href or not name:
            continue

        # El catálogo lleva al perfil del canal (/canal/<id>).
        path = urlparse(urljoin(BASE_URL, href)).path.strip("/")
        parts = path.split("/")

        if len(parts) != 2 or parts[0] != "canal":
            continue

        site_id = parts[1]
        if not site_id:
            continue

        channels[site_id] = {
            "id": site_id,
            "name": name,
            "url": f"{BASE_URL}/canal/{site_id}",
            "logo": None,
        }

    result = list(channels.values())

    if not result:
        raise RuntimeError(
            "No se encontraron canales en el catálogo. "
            "GatoTV podría haber cambiado su HTML."
        )

    print(f"Canales descubiertos en catálogo: {len(result)}")
    return result


def extract_programs(
    soup: BeautifulSoup,
    program_date: date,
    tz_info: timezone = DEFAULT_TZ,
) -> list[dict]:
    """
    Extrae los horarios y programas de la tabla EPG de GatoTV.
    Corrige automáticamente las fechas para emisiones de trasnoche y
    cruces de medianoche.
    """
    row_selector = (
        "table.tbl_EPG tr.tbl_EPG_row, "
        "table.tbl_EPG tr.tbl_EPG_rowAlternate, "
        "table.tbl_EPG tr.tbl_EPG_row_selected"
    )

    rows = soup.select(row_selector)
    if not rows:
        return []

    # Filtrar solo las filas que contengan horarios válidos
    valid_rows: list[tuple[BeautifulSoup, list[BeautifulSoup]]] = []
    for row in rows:
        times = row.select("td time[datetime]")
        if len(times) >= 2:
            valid_rows.append((row, times))

    if not valid_rows:
        return []

    programs = []
    current_date = program_date
    prev_hour = -1

    for idx, (row, times) in enumerate(valid_rows):
        try:
            start_parts = times[0]["datetime"].split(":")[:2]
            stop_parts = times[1]["datetime"].split(":")[:2]
            start_h, start_m = int(start_parts[0]), int(start_parts[1])
            stop_h, stop_m = int(stop_parts[0]), int(stop_parts[1])
        except (ValueError, KeyError, TypeError, IndexError):
            continue

        is_out_of_schedule = bool(row.select(".tbl_EPG_TimesColumnOutOfSchedule"))

        # Si el primer programa viene de la noche anterior (ej. 23:59 a 00:20)
        if idx == 0 and (is_out_of_schedule or (start_h >= 18 and stop_h <= 12)):
            start_dt_date = program_date - timedelta(days=1)
            stop_dt_date = program_date
            prev_hour = -1
        else:
            # Si el horario da la vuelta a medianoche al avanzar el día
            if prev_hour >= 18 and start_h < 6:
                current_date = current_date + timedelta(days=1)

            start_dt_date = current_date
            if (stop_h < start_h) or (stop_h == start_h and stop_m <= start_m):
                stop_dt_date = current_date + timedelta(days=1)
            else:
                stop_dt_date = current_date

            prev_hour = start_h

        start = datetime(
            start_dt_date.year,
            start_dt_date.month,
            start_dt_date.day,
            start_h,
            start_m,
            tzinfo=tz_info,
        )
        stop = datetime(
            stop_dt_date.year,
            stop_dt_date.month,
            stop_dt_date.day,
            stop_h,
            stop_m,
            tzinfo=tz_info,
        )

        title_node = (
            row.select_one(".div_program_title_on_channel span")
            or row.select_one(".div_program_title_on_channel a")
            or row.select_one(".div_program_title_on_channel")
            or row.select_one(".tbl_EPG_ProgramsColumn")
        )

        title = clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
        if not title:
            continue

        # Subtítulo o nombre de episodio
        sub_title = ""
        sub_node = row.select_one(".div_episode_programa_on_channel")
        if sub_node:
            sub_title = clean_text(sub_node.get_text(" ", strip=True))

        # Categoría temática
        category = ""
        row_classes = set(row.get("class", []))
        for td in row.find_all("td", recursive=False):
            row_classes.update(td.get("class", []))
        for cls_name, cat_label in CATEGORY_MAP.items():
            if cls_name in row_classes:
                category = cat_label
                break

        # Miniatura / póster del programa
        icon = None
        img_node = row.select_one("img")
        if img_node:
            img_src = (
                img_node.get("src")
                or img_node.get("data-src")
                or img_node.get("data-original")
            )
            if (
                img_src
                and not img_src.endswith("spacer.gif")
                and "quantserve" not in img_src
            ):
                icon = urljoin(BASE_URL + "/", img_src)

        # Descripción / Sinopsis si existiera
        description = ""
        desc_node = row.select_one(
            ".descripcion, .description, .sinopsis, .summary"
        )
        if desc_node:
            description = clean_text(desc_node.get_text(" ", strip=True))
        elif sub_title:
            # Fallback para reproductores IPTV que solo renderizan la etiqueta <desc>
            description = sub_title

        programs.append(
            {
                "start": start,
                "stop": stop,
                "title": title,
                "sub_title": sub_title,
                "category": category,
                "icon": icon,
                "description": description,
            }
        )

    return programs


def xmltv_time(value: datetime) -> str:
    """Convierte una fecha a formato XMLTV con su desfase horario."""
    return value.strftime("%Y%m%d%H%M%S %z")


def add_text(
    parent: ET.Element, tag: str, value: str, lang: str = "es"
) -> None:
    """Añade un elemento XML con texto y atributo de idioma."""
    node = ET.SubElement(parent, tag, {"lang": lang})
    node.text = value


def write_xmltv(
    channels: list[dict],
    all_programs: list[dict],
    output_path: Path,
    include_empty_channels: bool = False,
) -> None:
    """Genera el archivo XMLTV con canales, logos, categorías y programas."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tv = ET.Element(
        "tv",
        {
            "source-info-name": "GatoTV",
            "source-info-url": BASE_URL,
            "generator-info-name": "GatoTV EPG Python",
        },
    )

    # Identificar qué canales tienen programación
    channels_with_progs = {p["channel_id"] for p in all_programs}

    # Declaración de canales y sus logos
    for channel in channels:
        if not include_empty_channels and channel["id"] not in channels_with_progs:
            continue

        node = ET.SubElement(
            tv, "channel", {"id": f"{channel['id']}.gatotv.com"}
        )
        add_text(node, "display-name", channel["name"])

        if channel.get("logo"):
            ET.SubElement(node, "icon", {"src": channel["logo"]})

    # Ordenar la programación cronológicamente
    all_programs.sort(
        key=lambda program: (
            program["start"],
            program["channel_id"],
            program["title"],
        )
    )

    for program in all_programs:
        node = ET.SubElement(
            tv,
            "programme",
            {
                "start": xmltv_time(program["start"]),
                "stop": xmltv_time(program["stop"]),
                "channel": f"{program['channel_id']}.gatotv.com",
            },
        )

        add_text(node, "title", program["title"])

        if program.get("sub_title"):
            add_text(node, "sub-title", program["sub_title"])

        if program.get("description"):
            add_text(node, "desc", program["description"])

        if program.get("category"):
            add_text(node, "category", program["category"])

        if program.get("icon"):
            ET.SubElement(node, "icon", {"src": program["icon"]})

    tree = ET.ElementTree(tv)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"\nArchivo XMLTV generado: {output_path.resolve()}")
    print(f"Tamaño: {size_mb:.2f} MB")


def load_channel_filter(
    channels_arg: str | None, file_arg: str | None
) -> set[str]:
    """Carga y normaliza los identificadores de canal a filtrar."""
    targets = set()

    if channels_arg:
        for cid in channels_arg.split(","):
            cid = cid.strip().lower()
            if cid.endswith(".gatotv.com"):
                cid = cid[:-11]
            if cid:
                targets.add(cid)

    if file_arg:
        path = Path(file_arg)
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="ignore")
            # Soporte para listas M3U/M3U8 (extracción de tvg-id)
            if (
                "#EXTM3U" in content
                or "#EXTINF" in content
                or path.suffix.lower() in [".m3u", ".m3u8"]
            ):
                for match in re.finditer(
                    r'tvg-id="([^"]+)"', content, re.IGNORECASE
                ):
                    cid = match.group(1).strip().lower()
                    if cid.endswith(".gatotv.com"):
                        cid = cid[:-11]
                    if cid:
                        targets.add(cid)
            else:
                for line in content.splitlines():
                    line = line.strip().lower()
                    if line and not line.startswith("#"):
                        if line.endswith(".gatotv.com"):
                            line = line[:-11]
                        targets.add(line)

    return targets


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extractor y generador de EPG XMLTV para GatoTV"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=2,
        help="Días de programación que se descargarán (1-14, por defecto: 2)",
    )
    parser.add_argument(
        "--max-channels",
        type=int,
        default=0,
        help="Límite máximo de canales a procesar (0 = sin límite)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Pausa en segundos entre peticiones HTTP (por defecto: 0.5)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="guia.xml",
        help="Ruta del archivo XMLTV de salida (por defecto: guia.xml)",
    )
    parser.add_argument(
        "--channels",
        type=str,
        default=None,
        help="Lista de IDs de canales separados por coma (ej: de_las_estrellas_mexico,warner_channel)",
    )
    parser.add_argument(
        "--channels-file",
        type=str,
        default=None,
        help="Archivo .txt o lista de reproducción .m3u/.m3u8 para filtrar canales por su tvg-id",
    )
    parser.add_argument(
        "--timezone-offset",
        type=float,
        default=-5.0,
        help="Desfase horario en horas respecto a UTC (por defecto: -5 para EST / GatoTV)",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Incluir en el XMLTV canales que no tengan programas registrados",
    )
    args = parser.parse_args()

    if args.days < 1 or args.days > 14:
        parser.error("--days debe estar entre 1 y 14")

    if args.delay < 0:
        parser.error("--delay no puede ser negativo")

    output_path = Path(args.output)
    error_log_path = output_path.parent / "errores_gatotv.txt"

    if error_log_path.exists():
        error_log_path.unlink()

    tz_info = timezone(timedelta(hours=args.timezone_offset))

    session = requests.Session()
    session.headers.update(HEADERS)

    # Descubrir canales
    channels = discover_channels(session)

    # Filtrar canales si se especificaron
    target_ids = load_channel_filter(args.channels, args.channels_file)
    if target_ids:
        print(f"Filtrando por {len(target_ids)} canales especificados...")
        known_ids = {ch["id"].lower(): ch for ch in channels}
        filtered_channels = []

        for tid in target_ids:
            if tid in known_ids:
                filtered_channels.append(known_ids[tid])
            else:
                # Si el ID no está en el índice general, agregarlo directamente
                filtered_channels.append(
                    {
                        "id": tid,
                        "name": tid.replace("_", " ").title(),
                        "url": f"{BASE_URL}/canal/{tid}",
                        "logo": None,
                    }
                )
        channels = filtered_channels
        print(f"Canales seleccionados: {len(channels)}")

    if args.max_channels > 0:
        channels = channels[: args.max_channels]
        print(f"Modo limitado: se procesarán {len(channels)} canales.")

    all_programs = []
    seen_program_keys = set()
    total = len(channels)

    for index, channel in enumerate(channels, start=1):
        print(f"\n[{index}/{total}] {channel['name']} ({channel['id']})")
        channel_count = 0

        for offset in range(args.days):
            day = date.today() + timedelta(days=offset)
            url = f"{BASE_URL}/canal/{channel['id']}/{day.isoformat()}"

            try:
                soup = get_soup(session, url)

                # Extraer el logo de la primera página válida del canal
                if not channel.get("logo"):
                    channel["logo"] = extract_logo(soup)
                    if channel["logo"]:
                        print(f"  Logo encontrado: {channel['logo']}")

                programs = extract_programs(soup, day, tz_info=tz_info)

                # Deduplicar programas entre días consecutivos
                added_count = 0
                for item in programs:
                    item["channel_id"] = channel["id"]
                    key = (item["channel_id"], item["start"].isoformat())
                    if key in seen_program_keys:
                        continue
                    seen_program_keys.add(key)
                    all_programs.append(item)
                    added_count += 1

                channel_count += added_count
                print(f"  {day}: {len(programs)} programas extraídos ({added_count} nuevos)")

                time.sleep(args.delay)

            except Exception as exc:
                message = f"{channel['name']} | {url} | {exc}"
                log_error(error_log_path, message)
                print(f"  ERROR: {exc}")

        print(f"  Total programas válidos de este canal: {channel_count}")

    write_xmltv(
        channels,
        all_programs,
        output_path,
        include_empty_channels=args.include_empty,
    )

    print(f"Canales procesados: {len(channels)}")
    print(f"Programas únicos totales: {len(all_programs)}")

    channels_with_logo = sum(1 for c in channels if c.get("logo"))
    print(f"Canales con logo: {channels_with_logo}/{len(channels)}")

    if error_log_path.exists():
        print(f"Se registraron errores en: {error_log_path.resolve()}")

    if not all_programs:
        print(
            "\nATENCIÓN: no se extrajeron programas. "
            "Revisa errores_gatotv.txt."
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
