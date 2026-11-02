import unittest

from weather_pipeline import DatasetError, analyze_dataset, generate_report, parse_ghcn_dly


SAMPLE = """STATION,DATE,ELEMENT,DATA_VALUE,M_FLAG,Q_FLAG,S_FLAG,OBS_TIME
STATION-A,20260601,TMAX,200,,,,1200
STATION-A,20260602,TMAX,205,,,,1200
STATION-A,20260603,TMAX,202,,,,1200
STATION-A,20260604,TMAX,201,,,,1200
STATION-A,20260605,TMAX,350,,,,1200
STATION-A,20260601,PRCP,0,,,,1200
STATION-A,20260602,PRCP,4,,,,1200
STATION-A,20260603,PRCP,0,,Q,,
STATION-A,20260604,TMIN,-9999,,,,1200
STATION-A,20260605,FOO,10,,,,1200
"""


class WeatherPipelineTests(unittest.TestCase):
    def test_quality_checks_and_station_metrics(self):
        result = analyze_dataset(SAMPLE)
        self.assertEqual(result["quality"]["input_rows"], 10)
        self.assertEqual(result["quality"]["valid_rows"], 7)
        self.assertEqual(result["quality"]["rejected_quality_flag"], 1)
        self.assertEqual(result["quality"]["rejected_missing_or_invalid_date"], 1)
        self.assertEqual(result["quality"]["ignored_unsupported_element"], 1)
        summary = next(item for item in result["station_summaries"] if item["element"] == "TMAX")
        self.assertEqual(summary["observation_count"], 5)
        self.assertEqual(summary["maximum"], 35.0)

    def test_anomaly_and_provenance_are_produced(self):
        result = analyze_dataset(SAMPLE)
        self.assertTrue(result["anomalies"])
        anomaly = result["anomalies"][0]
        self.assertEqual(anomaly["date"], "2026-06-05")
        fact = next(
            item for item in result["facts"]
            if item["category"] == "anomaly" and item["date"] == anomaly["date"]
        )
        self.assertEqual(fact["source_observation_ids"], [anomaly["observation_id"]])

    def test_report_controls_change_presentation_not_source_facts(self):
        result = analyze_dataset(SAMPLE)
        report = generate_report(
            result,
            {
                "audience": "public",
                "depth": "concise",
                "tone": "plain",
                "length": "detailed",
                "topics": ["overview", "anomalies"],
            },
        )
        self.assertTrue(report["verification"]["verified"])
        self.assertTrue(all(claim["source_observation_ids"] for claim in report["claims"]))
        self.assertIn("plain-language", report["intro"])
        self.assertTrue(all(claim["category"] in {"overview", "anomaly"} for claim in report["claims"]))

    def test_invalid_controls_and_empty_supported_data_are_reported(self):
        with self.assertRaises(DatasetError):
            generate_report(analyze_dataset(SAMPLE), {"audience": "unknown"})
        with self.assertRaises(DatasetError):
            generate_report(analyze_dataset(SAMPLE), {"audience": []})
        with self.assertRaises(DatasetError):
            generate_report(analyze_dataset(SAMPLE), {"topics": [[]]})
        with self.assertRaises(DatasetError):
            analyze_dataset("STATION,DATE,ELEMENT,DATA_VALUE,M_FLAG,Q_FLAG,S_FLAG,OBS_TIME\n")

    def test_public_noaa_fixed_width_records_convert_with_date_and_quality_flags(self):
        records = []
        for month in (2, 3):
            daily = ["-9999   "] * 31
            if month == 2:
                daily[28] = "  235   "
                daily[27] = "  245 Q "
            records.append(f"USW000947282024{month:02d}TMAX" + "".join(daily))
        csv_text = parse_ghcn_dly("\n".join(records), 2024, 2024)
        parsed = analyze_dataset(csv_text)
        self.assertEqual(parsed["quality"]["input_rows"], 2)
        self.assertEqual(parsed["quality"]["valid_rows"], 1)
        self.assertEqual(parsed["quality"]["rejected_quality_flag"], 1)
        self.assertEqual(parsed["observations"][0]["date"], "2024-02-29")
        self.assertEqual(parsed["observations"][0]["value"], 23.5)

    def test_public_noaa_parser_excludes_years_outside_selected_window(self):
        daily = ["-9999   "] * 31
        daily[0] = "  180   "
        record = "USW00094728202401TMAX" + "".join(daily)
        csv_text = parse_ghcn_dly(record, 2023, 2023)
        with self.assertRaises(DatasetError):
            analyze_dataset(csv_text)


if __name__ == "__main__":
    unittest.main()
