const state = {
  observationsCsv: "",
  stationsCsv: "",
  analysis: null,
  report: null,
  publicSource: null,
};

const $ = (id) => document.getElementById(id);
const numberFormat = new Intl.NumberFormat();

function showToast(message, isError = false) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.toggle("toast-error", isError);
  toast.classList.add("toast-visible");
  window.clearTimeout(showToast.timeoutId);
  showToast.timeoutId = window.setTimeout(() => toast.classList.remove("toast-visible"), 3600);
}

async function readFile(input) {
  const file = input.files?.[0];
  if (!file) return "";
  if (file.size > 8 * 1024 * 1024) {
    input.value = "";
    throw new Error("Each CSV must be no larger than 8 MB.");
  }
  return file.text();
}

async function loadNoaaStation() {
  const button = $("load-noaa");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Fetching NOAA data…";
  try {
    const payload = await requestJson("/api/public/station", {
      station_id: $("noaa-station").value.trim(),
      start_year: Number($("noaa-start-year").value),
      end_year: Number($("noaa-end-year").value),
    });
    state.observationsCsv = payload.observations_csv;
    state.stationsCsv = payload.stations_csv;
    state.publicSource = payload.source;
    $("observations-file-label").textContent = "Public NOAA station data loaded";
    $("stations-file-label").textContent = `${payload.source.station_name} metadata loaded`;
    $("source-label").textContent =
      `NOAA GHCN-Daily · ${payload.source.station_id} · ${payload.source.start_year}–${payload.source.end_year} · cached under data/public/`;
    renderAnalysis(payload.analysis);
    showToast("Public NOAA data fetched, cached locally, and analyzed.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

async function requestJson(url, body) {
  const options = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

function makeElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function displayValue(value, unit) {
  const decimals = unit === "mm" || unit === "°C" ? 1 : 0;
  return `${Number(value).toFixed(decimals)} ${unit}`;
}

function renderAnalysis(analysis) {
  state.analysis = analysis;
  state.report = null;
  $("generate-report").disabled = false;
  $("report-output").classList.add("hidden");
  $("metric-observations").textContent = numberFormat.format(analysis.summary.observation_count);
  $("metric-stations").textContent = numberFormat.format(analysis.summary.station_count);
  $("metric-anomalies").textContent = numberFormat.format(analysis.summary.anomaly_count);
  $("metric-quality").textContent = `${numberFormat.format(analysis.quality.valid_rows)} / ${numberFormat.format(analysis.quality.input_rows)}`;
  $("metric-period").textContent = `${analysis.summary.date_start} — ${analysis.summary.date_end}`;
  $("dataset-range").textContent = `${analysis.summary.date_start} — ${analysis.summary.date_end}`;

  const legend = $("element-legend");
  legend.replaceChildren();
  Object.entries(analysis.summary.elements).forEach(([element, count]) => {
    const item = makeElement("span", "element-pill", `${element} · ${numberFormat.format(count)}`);
    legend.append(item);
  });

  const stationBody = $("station-table");
  stationBody.replaceChildren();
  analysis.station_summaries.forEach((station) => {
    const row = document.createElement("tr");
    [
      station.station_name,
      station.element,
      displayValue(station.mean, station.unit),
      `${displayValue(station.minimum, station.unit)} – ${displayValue(station.maximum, station.unit)}`,
      `${displayValue(station.latest_value, station.unit)} · ${station.latest_date}`,
    ].forEach((text, index) => row.append(makeElement(index === 0 ? "th" : "td", "", text)));
    stationBody.append(row);
  });

  $("anomaly-count").textContent = String(analysis.anomalies.length);
  const anomalyList = $("anomaly-list");
  anomalyList.replaceChildren();
  if (!analysis.anomalies.length) {
    anomalyList.append(makeElement("p", "empty-copy", "No anomaly candidates were identified in this dataset."));
  } else {
    analysis.anomalies.slice(0, 8).forEach((anomaly) => {
      const item = makeElement("div", "anomaly-item");
      const title = makeElement("strong", "", `${anomaly.station_name} · ${anomaly.element}`);
      const detail = makeElement("span", "", `${anomaly.date} · ${displayValue(anomaly.value, anomaly.unit)} · score ${anomaly.score}`);
      item.append(title, detail);
      anomalyList.append(item);
    });
  }

  const trendList = $("trend-list");
  trendList.replaceChildren();
  if (!analysis.trends.length) {
    trendList.append(makeElement("p", "empty-copy", "At least two observed dates are needed to compare trends."));
  } else {
    analysis.trends.forEach((trend) => {
      const item = makeElement("div", "trend-item");
      item.append(
        makeElement("strong", "", trend.element_label),
        makeElement("span", trend.change > 0 ? "trend-up" : trend.change < 0 ? "trend-down" : "", `${trend.direction} ${Math.abs(trend.change)} ${trend.unit}`),
        makeElement("small", "", `${trend.start_date} to ${trend.end_date}`),
      );
      trendList.append(item);
    });
  }

  const qualityDetails = $("quality-details");
  qualityDetails.replaceChildren();
  [
    ["Input rows", analysis.quality.input_rows],
    ["Valid supported rows", analysis.quality.valid_rows],
    ["Rejected quality flag", analysis.quality.rejected_quality_flag],
    ["Missing value / invalid date", analysis.quality.rejected_missing_or_invalid_date],
    ["Out of range / missing station", analysis.quality.rejected_out_of_range_or_missing_station],
    ["Unsupported elements ignored", analysis.quality.ignored_unsupported_element],
  ].forEach(([label, value]) => {
    qualityDetails.append(makeElement("span", "", label), makeElement("strong", "", numberFormat.format(value)));
  });
}

function renderReport(report) {
  state.report = report;
  $("report-output").classList.remove("hidden");
  $("report-title").textContent = report.title;
  $("report-intro").textContent = report.intro;
  const claims = $("report-claims");
  claims.replaceChildren();
  report.claims.forEach((claim) => {
    const item = makeElement("li", "claim-item");
    item.append(makeElement("span", "claim-copy", claim.text));
    const details = document.createElement("details");
    details.className = "claim-provenance";
    details.append(makeElement("summary", "claim-source", `${claim.id} · ${claim.source_observation_ids.length} source observation${claim.source_observation_ids.length === 1 ? "" : "s"}`));
    details.append(makeElement("p", "claim-source-list", claim.source_observation_ids.join(", ")));
    item.append(details);
    claims.append(item);
  });
  const verification = report.verification;
  const badge = $("verification-badge");
  badge.textContent = verification.verified ? "✓ All claims verified" : "Review needed";
  badge.classList.toggle("verification-failed", !verification.verified);
  const sourceCount = new Set(report.claims.flatMap((claim) => claim.source_observation_ids)).size;
  $("provenance-summary").textContent = `${verification.checks.filter((check) => check.status === "verified").length} of ${verification.checks.length} statements verified against ${numberFormat.format(sourceCount)} source observation${sourceCount === 1 ? "" : "s"}.`;
  $("report-scope").textContent = "Scope: descriptive analysis of supplied observations only; this is not a forecast. Verification confirms traceability to the structured claim store, not scientific correctness of the source data.";
  $("report-output").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadSample() {
  $("load-sample").disabled = true;
  $("load-sample").textContent = "Loading sample…";
  try {
    const [observationsResponse, stationsResponse, payload] = await Promise.all([
      fetch("/data/sample-observations.csv"),
      fetch("/data/sample-stations.csv"),
      requestJson("/api/demo"),
    ]);
    if (!observationsResponse.ok || !stationsResponse.ok) throw new Error("Could not load the bundled sample CSV files.");
    state.observationsCsv = await observationsResponse.text();
    state.stationsCsv = await stationsResponse.text();
    $("observations-file-label").textContent = "Bundled NOAA observations loaded";
    $("stations-file-label").textContent = "Bundled NOAA station metadata loaded";
    $("source-label").textContent = "NOAA GHCN-Daily · USW00094728 · cached locally for offline startup.";
    renderAnalysis(payload.analysis);
    showToast("Bundled public NOAA dataset analyzed.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    $("load-sample").disabled = false;
    $("load-sample").innerHTML = '<span aria-hidden="true">✦</span> Load bundled NOAA dataset';
  }
}

async function analyzeUpload() {
  const button = $("analyze-data");
  button.disabled = true;
  button.textContent = "Analyzing…";
  try {
    state.observationsCsv = await readFile($("observations-file"));
    state.stationsCsv = await readFile($("stations-file"));
    if (!state.observationsCsv) throw new Error("Choose an observations CSV first.");
    const payload = await requestJson("/api/analyze", {
      observations_csv: state.observationsCsv,
      stations_csv: state.stationsCsv,
    });
    renderAnalysis(payload.analysis);
    showToast("Weather data analyzed successfully.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.textContent = "Analyze uploaded data";
    button.disabled = !$("observations-file").files?.length;
  }
}

async function generateReport() {
  if (!state.observationsCsv) {
    showToast("Load or analyze a dataset before generating a report.", true);
    return;
  }
  const topics = [...document.querySelectorAll('input[name="topic"]:checked')].map((input) => input.value);
  const button = $("generate-report");
  button.disabled = true;
  button.textContent = "Verifying report…";
  try {
    const payload = await requestJson("/api/report", {
      observations_csv: state.observationsCsv,
      stations_csv: state.stationsCsv,
      controls: {
        audience: $("audience").value,
        depth: $("depth").value,
        tone: $("tone").value,
        length: $("length").value,
        topics,
      },
    });
    renderAnalysis(payload.analysis);
    renderReport(payload.report);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = !state.analysis;
    button.innerHTML = 'Generate verified report <span aria-hidden="true">→</span>';
  }
}

function downloadReport() {
  if (!state.report) return;
  const blob = new Blob([state.report.markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "weather-station-summary.md";
  link.click();
  URL.revokeObjectURL(url);
}

async function downloadDocx() {
  if (!state.report) return;
  const button = $("download-docx");
  button.disabled = true;
  try {
    const response = await fetch("/api/export/docx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markdown: state.report.markdown }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || `DOCX export failed (${response.status}).`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "weather-station-summary.docx";
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

$("load-sample").addEventListener("click", loadSample);
$("load-noaa").addEventListener("click", loadNoaaStation);
$("analyze-data").addEventListener("click", analyzeUpload);
$("generate-report").addEventListener("click", generateReport);
$("download-report").addEventListener("click", downloadReport);
$("download-docx").addEventListener("click", downloadDocx);
$("print-report").addEventListener("click", () => {
  if (!state.report) return;
  window.print();
});
$("observations-file").addEventListener("change", () => {
  const file = $("observations-file").files?.[0];
  $("observations-file-label").textContent = file ? file.name : "Choose observations CSV";
  $("analyze-data").disabled = !file;
});
$("stations-file").addEventListener("change", () => {
  const file = $("stations-file").files?.[0];
  $("stations-file-label").textContent = file ? file.name : "Choose station metadata CSV";
});

const thisYear = new Date().getFullYear();
$("noaa-end-year").value = String(thisYear);
$("noaa-end-year").max = String(thisYear);
$("noaa-start-year").value = String(thisYear - 1);
$("noaa-start-year").max = String(thisYear);
loadSample();
