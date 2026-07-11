"""Grafana provisioning sanity checks (T-034).

Static validation of observability/: every dashboard JSON parses and carries
what Grafana needs for stable provisioning (uid, title, panels), the
datasource and dashboard-provider YAML files parse, panels reference the
provisioned datasource, and no provisioning file contains a hardcoded
password — secrets may only appear as ${ENV_VAR} placeholders that Grafana
expands from the container environment.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY_DIR = PROJECT_ROOT / "observability"
DASHBOARD_FILES = sorted((OBSERVABILITY_DIR / "dashboards").glob("*.json"))
DATASOURCE_FILES = sorted((OBSERVABILITY_DIR / "datasources").glob("*.yaml"))
PROVIDER_FILE = OBSERVABILITY_DIR / "dashboards.yaml"

DATASOURCE_UID = "ledgerlens-postgres"
ENV_PLACEHOLDER = re.compile(r"^\$\{[A-Z][A-Z0-9_]*\}$")
PASSWORD_KEY = re.compile(r"password", re.IGNORECASE)


def _load_dashboard(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: top-level JSON must be an object"
    return data


def _walk(node: Any) -> list[Any]:
    """Every dict/list node of a JSON tree, depth-first."""
    nodes: list[Any] = [node]
    if isinstance(node, dict):
        for value in node.values():
            nodes.extend(_walk(value))
    elif isinstance(node, list):
        for item in node:
            nodes.extend(_walk(item))
    return nodes


# --- dashboards ---------------------------------------------------------------


def test_expected_dashboards_present() -> None:
    names = {path.name for path in DASHBOARD_FILES}
    assert {"operations.json", "session.json", "quality.json"} <= names, (
        f"missing dashboards in {OBSERVABILITY_DIR / 'dashboards'}: found {sorted(names)}"
    )


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda path: path.name)
def test_dashboard_has_uid_title_and_panels(path: Path) -> None:
    dashboard = _load_dashboard(path)
    assert dashboard.get("uid"), "uid is required for stable provisioned dashboard URLs"
    assert dashboard.get("title"), "title is required"
    panels = dashboard.get("panels")
    assert isinstance(panels, list) and panels, "dashboard has no panels"
    for panel in panels:
        assert panel.get("type"), f"panel without a type: {panel.get('title')!r}"
        assert panel.get("title"), f"untitled panel id={panel.get('id')}"


def test_dashboard_uids_and_titles_are_unique() -> None:
    dashboards = [_load_dashboard(path) for path in DASHBOARD_FILES]
    uids = [d["uid"] for d in dashboards]
    titles = [d["title"] for d in dashboards]
    assert len(set(uids)) == len(uids), f"duplicate dashboard uids: {uids}"
    assert len(set(titles)) == len(titles), f"duplicate dashboard titles: {titles}"


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda path: path.name)
def test_dashboard_references_only_the_provisioned_datasource(path: Path) -> None:
    dashboard = _load_dashboard(path)
    uids = {
        node["datasource"]["uid"]
        for node in _walk(dashboard)
        if isinstance(node, dict) and isinstance(node.get("datasource"), dict)
    }
    unknown = {uid for uid in uids if uid != DATASOURCE_UID and not uid.startswith("$")}
    assert not unknown, f"panels reference unknown datasource uids: {sorted(unknown)}"


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda path: path.name)
def test_dashboard_sql_targets_are_nonempty(path: Path) -> None:
    dashboard = _load_dashboard(path)
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            raw_sql = target.get("rawSql", "")
            assert raw_sql.strip().upper().startswith("SELECT"), (
                f"panel {panel['title']!r}: rawSql must be a SELECT, got {raw_sql[:60]!r}"
            )


# --- datasource / provider YAML -------------------------------------------------


def test_datasource_yaml_is_valid_and_uses_grafana_ro() -> None:
    assert DATASOURCE_FILES, f"no datasource files in {OBSERVABILITY_DIR / 'datasources'}"
    for path in DATASOURCE_FILES:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        datasources = config.get("datasources")
        assert isinstance(datasources, list) and datasources, f"{path.name}: no datasources"
        for ds in datasources:
            assert ds["type"] == "postgres", f"{path.name}: unexpected type {ds['type']!r}"
            assert ds["uid"] == DATASOURCE_UID
            assert ds["user"] == "grafana_ro", "datasource must use the read-only role"
            password = str(ds.get("secureJsonData", {}).get("password", ""))
            assert ENV_PLACEHOLDER.match(password), (
                f"{path.name}: datasource password must be a ${{ENV_VAR}} placeholder, "
                f"got {password!r}"
            )


def test_dashboard_provider_yaml_is_valid() -> None:
    config = yaml.safe_load(PROVIDER_FILE.read_text(encoding="utf-8"))
    providers = config.get("providers")
    assert isinstance(providers, list) and providers, "dashboards.yaml: no providers"
    options = providers[0].get("options", {})
    assert options.get("path") == "/var/lib/grafana/dashboards", (
        "provider path must match the dashboards mount in docker-compose.yml"
    )


def test_no_hardcoded_passwords_in_provisioning_files() -> None:
    """grep-style guard: any line mentioning a password in a provisioning file
    must carry a ${...} placeholder, never a literal value."""
    for path in [*DATASOURCE_FILES, PROVIDER_FILE, *DASHBOARD_FILES]:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or ":" not in stripped:
                continue
            key, _, value = stripped.partition(":")
            if PASSWORD_KEY.search(key) and value.strip():
                assert "${" in value, (
                    f"{path.name}:{line_no} looks like a hardcoded password: {stripped!r}"
                )


# --- docker-compose wiring -------------------------------------------------------


def test_compose_grafana_service_mounts_provisioning() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    grafana = compose["services"].get("grafana")
    assert grafana, "docker-compose.yml has no grafana service"
    assert "3001:3000" in grafana["ports"], "grafana must publish 3001 (3000 is the web UI)"
    volumes = "\n".join(grafana["volumes"])
    assert "./observability/datasources:" in volumes
    assert "./observability/dashboards.yaml:" in volumes
    assert "./observability/dashboards:" in volumes
    env = grafana["environment"]
    assert env["GF_AUTH_ANONYMOUS_ORG_ROLE"] == "Viewer", "anonymous access must stay view-only"
    admin_password = str(env["GF_SECURITY_ADMIN_PASSWORD"])
    assert admin_password.startswith("${"), "admin password must come from the environment"
