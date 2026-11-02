"""Dependency-free local web server for the F-32 weather reporting MVP."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from weather_pipeline import DatasetError, analyze_dataset, generate_report, load_noaa_station_data


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
MAX_REQUEST_BYTES = 18 * 1024 * 1024
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def public_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """Omit raw rows from the browser payload while retaining derived evidence."""
    return {key: value for key, value in analysis.items() if key != "observations"}


class WeatherHandler(BaseHTTPRequestHandler):
    server_version = "F32WeatherMVP/1.0"

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str, filename: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise DatasetError("A valid Content-Length header is required.") from error
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise DatasetError("Request is too large. Upload each CSV within the 8 MB limit.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DatasetError("Request body must be valid UTF-8 JSON.") from error
        if not isinstance(payload, dict):
            raise DatasetError("Request body must be a JSON object.")
        return payload

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/demo":
            self._serve_demo()
            return
        if path == "/api/health":
            self._send_json({"status": "ok"})
            return
        allowed_files = {
            "/": STATIC / "index.html",
            "/static/index.html": STATIC / "index.html",
            "/static/styles.css": STATIC / "styles.css",
            "/static/app.js": STATIC / "app.js",
            "/data/sample-observations.csv": DATA / "sample-observations.csv",
            "/data/sample-stations.csv": DATA / "sample-stations.csv",
        }
        file_path = allowed_files.get(path)
        if file_path is None or not file_path.is_file():
            self._send_json({"error": "Not found."}, 404)
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_demo(self) -> None:
        try:
            analysis = analyze_dataset(
                (DATA / "sample-observations.csv").read_text(encoding="utf-8"),
                (DATA / "sample-stations.csv").read_text(encoding="utf-8"),
            )
            self._send_json({"analysis": public_analysis(analysis)})
        except (OSError, DatasetError) as error:
            logging.exception("Unable to load the bundled sample dataset")
            self._send_json({"error": f"Unable to load sample dataset: {error}"}, 500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/api/analyze", "/api/report", "/api/public/station", "/api/export/docx"}:
            self._send_json({"error": "Not found."}, 404)
            return
        try:
            payload = self._read_json()
            if path == "/api/public/station":
                station_id = payload.get("station_id")
                start_year = payload.get("start_year")
                end_year = payload.get("end_year")
                if not isinstance(station_id, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", station_id):
                    raise DatasetError("Enter a valid 11-character NOAA GHCN-Daily station ID.")
                observations_csv, station_csv, source = load_noaa_station_data(
                    station_id,
                    start_year,
                    end_year,
                )
                analysis = analyze_dataset(observations_csv, station_csv)
                self._send_json(
                    {
                        "analysis": public_analysis(analysis),
                        "observations_csv": observations_csv,
                        "stations_csv": station_csv,
                        "source": source,
                    }
                )
                return
            if path == "/api/export/docx":
                markdown = payload.get("markdown")
                if not isinstance(markdown, str) or not markdown.strip() or len(markdown) > 500_000:
                    raise DatasetError("A non-empty report under 500 KB is required for DOCX export.")
                self._send_bytes(
                    create_docx(markdown),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "weather-station-summary.docx",
                )
                return
            observations_csv = payload.get("observations_csv")
            station_csv = payload.get("stations_csv", "")
            if not isinstance(observations_csv, str) or not isinstance(station_csv, str):
                raise DatasetError("Observations and station metadata must be CSV text.")
            analysis = analyze_dataset(observations_csv, station_csv)
            if path == "/api/analyze":
                self._send_json({"analysis": public_analysis(analysis)})
                return
            controls = payload.get("controls", {})
            if not isinstance(controls, dict):
                raise DatasetError("Report controls must be a JSON object.")
            report = generate_report(analysis, controls)
            self._send_json({"analysis": public_analysis(analysis), "report": report})
        except DatasetError as error:
            self._send_json({"error": str(error)}, 400)
        except Exception:
            logging.exception("Unexpected request processing error")
            self._send_json({"error": "The request could not be processed. Check the server log."}, 500)

    def log_message(self, format_string: str, *args: Any) -> None:
        logging.info("%s - %s", self.address_string(), format_string % args)


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), WeatherHandler)
    logging.info("F-32 Weather Reporting MVP available at http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Stopping local server")
    finally:
        server.server_close()


def create_docx(markdown: str) -> bytes:
    """Create a minimal standards-compliant DOCX using only local standard-library code."""
    from html import escape

    paragraphs = []
    for line in markdown.splitlines():
        text = line.lstrip("#- ").strip()
        if not text:
            continue
        paragraphs.append(
            "<w:p><w:r><w:t xml:space=\"preserve\">"
            + escape(text)
            + "</w:t></w:r></w:p>"
        )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(paragraphs)
        + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    package = BytesIO()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr("word/document.xml", document)
    return package.getvalue()


if __name__ == "__main__":
    main()
