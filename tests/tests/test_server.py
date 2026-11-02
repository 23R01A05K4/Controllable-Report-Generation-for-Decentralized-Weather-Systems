import json
import threading
import unittest
import zipfile
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from server import WeatherHandler, create_docx


class WeatherServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), WeatherHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)

    def test_project_source_and_hidden_files_are_not_served(self):
        for path in ("/server.py", "/weather_pipeline.py", "/README.md", "/.git/config", "/.env", "/static/../server.py", "/data/public/ghcnd-stations.txt"):
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as error:
                    urlopen(self.base_url + path, timeout=3)
                self.assertEqual(error.exception.code, 404)

    def test_public_assets_and_health_are_available(self):
        for path in ("/", "/static/styles.css", "/static/app.js", "/data/sample-observations.csv", "/data/sample-stations.csv", "/api/health"):
            with self.subTest(path=path):
                with urlopen(self.base_url + path, timeout=3) as response:
                    self.assertEqual(response.status, 200)

    def test_docx_export_is_a_valid_office_open_xml_package(self):
        content = create_docx("# Weather report\n\nVerified observation.")
        with zipfile.ZipFile(BytesIO(content)) as package:
            self.assertIn("[Content_Types].xml", package.namelist())
            document = package.read("word/document.xml").decode("utf-8")
            self.assertIn("Weather report", document)
            self.assertIn("Verified observation.", document)

    def test_noaa_endpoint_validates_station_before_download(self):
        request = Request(
            f"{self.base_url}/api/public/station",
            data=json.dumps(
                {"station_id": "../../etc/pass", "start_year": 2025, "end_year": 2026}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with patch("server.load_noaa_station_data") as fetch:
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=3)
            self.assertEqual(error.exception.code, 400)
            fetch.assert_not_called()

    def test_docx_endpoint_returns_downloadable_word_document(self):
        request = Request(
            f"{self.base_url}/api/export/docx",
            data=json.dumps({"markdown": "# Local report"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            body = response.read()
            self.assertEqual(response.headers.get_content_type(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.assertIn("weather-station-summary.docx", response.headers["Content-Disposition"])
        with zipfile.ZipFile(BytesIO(body)) as package:
            self.assertIn("Local report", package.read("word/document.xml").decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
