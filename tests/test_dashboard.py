"""The Grafana dashboard is portable (REQ_DASHBOARD_PORTABLE)."""

import json
import pathlib
import re

PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "deploy"
    / "grafana-dashboards"
    / "trackiwi.json"
)


def _load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def _panels(dashboard):
    return [p for p in dashboard["panels"] if p["type"] != "row"]


def test_three_rows_in_order():
    rows = [p["title"] for p in _load()["panels"] if p["type"] == "row"]
    assert rows == ["Status", "Travel", "Health"]


def test_required_panels_present():
    titles = {p["title"] for p in _panels(_load())}
    for required in (
        "Last seen",
        "Voltage now",
        "Battery now",
        "Satellites now",
        "GNSS quality now",
        "Current position",
        "Route",
        "Speed",
        "Distance per day",
        "Voltage",
        "Battery",
        "Satellites & GNSS quality",
    ):
        assert required in titles, required


def test_datasource_is_a_variable_everywhere():
    for panel in _panels(_load()):
        assert panel["datasource"] == {"type": "influxdb", "uid": "${DS_TRACKIWI}"}, panel["title"]
        for target in panel["targets"]:
            assert target["datasource"] == {"type": "influxdb", "uid": "${DS_TRACKIWI}"}


def test_every_query_is_flux_on_the_bucket_variable():
    for panel in _panels(_load()):
        for target in panel["targets"]:
            query = target["query"]
            assert 'from(bucket: "${bucket}")' in query, panel["title"]
            assert 'r._measurement == "trackiwi_position"' in query, panel["title"]
            assert 'r.tracker_name == "${tracker}"' in query, panel["title"]


def test_template_variables():
    variables = {v["name"]: v for v in _load()["templating"]["list"]}
    assert variables["DS_TRACKIWI"]["type"] == "datasource"
    assert variables["DS_TRACKIWI"]["query"] == "influxdb"
    assert variables["bucket"]["type"] == "textbox"
    assert variables["bucket"]["query"] == "trackiwi"
    assert variables["tracker"]["type"] == "query"
    assert (
        'schema.tagValues(bucket: "${bucket}", tag: "tracker_name"' in variables["tracker"]["query"]
    )


def test_no_hardcoded_datasource_uids_or_real_values():
    text = PATH.read_text(encoding="utf-8")
    uids = set(re.findall(r'"uid":\s*"([^"]*)"', text))
    assert uids <= {"${DS_TRACKIWI}", "trackiwi-overview"}, uids
    assert not re.search(r"-?\d{1,3}\.\d{5,}", text), "coordinate-like number"
    assert "__inputs" not in text
