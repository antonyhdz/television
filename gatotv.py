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

# Lista oficial y completa de países soportados y sus códigos ISO
COUNTRY_MAP = {
    "el_salvador": "SV",
    "argentina": "AR",
    "mexico": "MX",
    "espana": "ES",
    "colombia": "CO",
    "chile": "CL",
    "peru": "PE",
    "costa_rica": "CR",
    "guatemala": "GT",
    "honduras": "HN",
    "panama": "PA",
    "republica_dominicana": "DO",
    "uruguay": "UY",
    "venezuela": "VE",
    "estados_unidos": "US",
    "bolivia": "BO",
    "ecuador": "EC",
    "paraguay": "PY",
    "puerto_rico": "PR",
}

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


def get_countries_list() -> list[dict]:
    """Genera la lista de países de forma robusta utilizando el diccionario oficial."""
    print("Cargando lista de países configurados...")
    countries = []
    for slug, code in COUNTRY_MAP.items():
        countries.append({
            "slug": slug,
            "code": code,
            "url": f"{BASE_URL}/guia_tv/{slug}"
        })
    print(f"Países cargados para procesamiento: {len(countries)}")
    return countries


def extract_logo(soup: BeautifulSoup) -> str | None:
    """Busca el logo del canal en el HTML."""
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

    for source in soup.find_all("source"):
        srcset = source.get("srcset", "")
        if "logos/canales" in srcset:
            first_url = srcset.split(",")[0].strip().split(" ")[0]
            if first_url:
                return urljoin(BASE_URL + "/", first_url)

    return None


def discover_channels(session: requests.Session, channels_url: str) -> list[dict]:
    """Obtiene el catálogo de canales para un país específico."""
    soup = get_soup(session, channels_url)
    channels = {}

    for link in soup.select('a[href*="/canal/"]'):
        href = link.get("href", "").strip()
        name = clean_text(link.get_text(" ", strip=True))

        if not href or not name:
            continue

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

    return list(channels.values())


def extract_programs(
    soup: BeautifulSoup,
    program_date: date,
    tz_info: timezone = DEFAULT_TZ,
) -> list[dict]:
    """Extrae los horarios y programas de la tabla EPG de GatoTV omitiendo días vacíos."""
    row_selector = (
        "table.tbl_EPG tr.tbl_EPG_row, "
        "table.tbl_EPG tr.tbl_EPG_rowAlternate, "
        "table.tbl_EPG tr.tbl_EPG_row_selected"
    )

    rows = soup.select(row_selector)
    if not rows:
        return []

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

        if idx == 0 and (is_out_of_schedule or (start_h >= 18 and stop_h <= 12)):
            start_dt_date = program_date - timedelta(days=1)
            stop_dt_date = program_date
            prev_hour = -1
        else:
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

        sub_title = ""
        sub_node = row.select_one(".div_episode_programa_on_channel")
        if sub_node:
            sub_title = clean_text(sub_node.get_text(" ", strip=True))

        category = ""
        row_classes = set(row.get("class", []))
        for td in row.find_all("td", recursive=False):
            row_classes.update(td.get("class", []))
        for cls_name, cat_label in CATEGORY_MAP.items():
            if cls_name in row_classes:
                category = cat_label
                break

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

        description = ""
        desc_node = row.select_one(
            ".descripcion, .description, .sinopsis, .summary"
        )
        if desc_node:
            description = clean_text(desc_node.get_text(" ", strip=True))
        elif sub_title:
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
    include_empty_channels: bool,
    country_code: str,
) -> None:
    """Genera el archivo XMLTV individual para el país con el formato exacto requerido."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tv = ET.Element(
        "tv",
        {
            "source-info-name": "GatoTV",
            "source-info-url": BASE_URL,
            "generator-info-name": "GatoTV EPG Python Multi-Country",
        },
    )

    channels_with_progs = {p["channel_id"] for p in all_programs}

    for channel in channels:
        if not include_empty_channels and channel["id"] not in channels_with_progs:
            continue

        node = ET.SubElement(
            tv, "channel", {"id": f"{channel['id']}.gatotv.com"}
        )
        
        # Formato exacto solicitado: [PAIS] - GatoTV - [Nombre del Canal]
        formatted_name = f"{country_code} - GatoTV - {channel['name']}"
        add_text(node, "display-name", formatted_name)

        if channel.get("logo"):
            ET.SubElement(node, "icon", {"src": channel["logo"]})

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
    print(f"-> Archivo generado: {output_path.resolve()} ({size_mb:.2f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extractor masivo de EPG XMLTV para todos los países en GatoTV"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=4,
        help="Días de programación por país (1-14, por defecto: 4)",
    )
    parser.add_argument(
        "--max-channels",
        type=int,
        default=0,
        help="Límite máximo de canales por país (0 = sin límite)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="Pausa en segundos entre peticiones HTTP (por defecto: 0.3)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="gatotv",
        help="Carpeta donde se guardarán los archivos XML por país (por defecto: gatotv)",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Incluir canales sin programas registrados",
    )
    args = parser.parse_args()

    if args.days < 1 or args.days > 14:
        parser.error("--days debe estar entre 1 y 14")

    session = requests.Session()
    session.headers.update(HEADERS)

    # Cargar la lista completa de países mediante el diccionario oficial seguro
    countries = get_countries_list()

    out_dir = Path(args.output_dir)
    error_log_path = out_dir / "errores_gatotv_global.txt"
    out_dir.mkdir(parents=True, exist_ok=True)

    if error_log_path.exists():
        error_log_path.unlink()

    tz_info = timezone(timedelta(hours=-5.0))

    for c_idx, country in enumerate(countries, start=1):
        country_slug = country["slug"]
        country_code = country["code"]
        guide_url = country["url"]

        output_filename = f"{country_code.lower()}-GatoTV.xml"
        output_path = out_dir / output_filename

        print(f"\n[{c_idx}/{len(countries)}] Procesando país: {country_slug.upper()} (Prefijo: {country_code}) -> Archivo: {output_filename}")

        try:
            channels = discover_channels(session, guide_url)
            if not channels:
                print(f"  AVISO: No se encontraron canales para {country_slug}.")
                continue

            if args.max_channels > 0:
                channels = channels[: args.max_channels]

            all_programs = []
            seen_program_keys = set()
            total_channels = len(channels)

            for index, channel in enumerate(channels, start=1):
                channel_count = 0
                for offset in range(args.days):
                    day = date.today() + timedelta(days=offset)
                    url = f"{BASE_URL}/canal/{channel['id']}/{day.isoformat()}"

                    try:
                        soup = get_soup(session, url)
                        if not channel.get("logo"):
                            channel["logo"] = extract_logo(soup)

                        programs = extract_programs(soup, day, tz_info=tz_info)

                        if not programs:
                            continue

                        for item in programs:
                            item["channel_id"] = channel["id"]
                            key = (item["channel_id"], item["start"].isoformat())
                            if key in seen_program_keys:
                                continue
                            seen_program_keys.add(key)
                            all_programs.append(item)
                            channel_count += 1

                        time.sleep(args.delay)
                    except Exception as exc:
                        log_error(error_log_path, f"{country_slug} | {channel['name']} | {url} | {exc}")

                print(f"  [{index}/{total_channels}] {channel['name']}: {channel_count} programas (4 días máx)")

            if not all_programs:
                print(f"  AVISO: No se acumuló programación válida para {country_slug}, omitiendo archivo.")
                continue

            write_xmltv(
                channels,
                all_programs,
                output_path,
                include_empty_channels=args.include_empty,
                country_code=country_code,
            )

        except Exception as exc:
            print(f"  ERROR procesando el país {country_slug}: {exc}")
            log_error(error_log_path, f"PAÍS FALLIDO: {country_slug} | {exc}")

    print("\n¡Proceso masivo completado con éxito!")
    print(f"Los archivos XML (ej. sv-gatotv.xml, ar-gatotv.xml) se guardaron en: {out_dir.resolve()}")


if __name__ == "__main__":
    main()