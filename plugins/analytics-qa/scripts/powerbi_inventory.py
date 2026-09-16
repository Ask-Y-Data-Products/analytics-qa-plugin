"""Normalize report evidence without interpreting DAX or inventing lineage."""
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def visual_bindings(report_dir):
    root = Path(report_dir)
    enhanced = root / "definition"
    result = []
    if enhanced.is_dir():
        report = read(enhanced / "report.json")
        for page_file in sorted((enhanced / "pages").glob("*/page.json")):
            page = read(page_file)
            for path in sorted((page_file.parent / "visuals").glob("*/visual.json")):
                container = read(path)
                visual = container.get("visual", {})
                query = visual.get("query", {})
                result.append({"visual_id": container["name"], "page_id": page["name"],
                    "page": page["displayName"], "source_path": str(path), "format": "PBIR",
                    "type": visual.get("visualType", "group"),
                    "projections": {role: value.get("projections", []) for role, value in query.get("queryState", {}).items()},
                    "query": query, "title_properties": visual.get("visualContainerObjects", {}).get("title", []),
                    "filters": {"report": report.get("filterConfig"), "page": page.get("filterConfig"), "visual": container.get("filterConfig")},
                    "interactions": page.get("visualInteractions", []), "sync_group": visual.get("syncGroup"),
                    "hidden": container.get("isHidden", False), "position": container.get("position"),
                    "formatting": visual.get("objects", {}), "expansion_states": visual.get("expansionStates", [])})
    elif (root / "report.json").is_file():
        report = read(root / "report.json")
        for page in report.get("sections", []):
            page_config = page.get("config", {})
            if isinstance(page_config, str):
                page_config = json.loads(page_config)
            for item in page.get("visualContainers", []):
                config = json.loads(item["config"]) if isinstance(item["config"], str) else item["config"]
                visual = config.get("singleVisual", {})
                prototype = visual.get("prototypeQuery", {})
                selected = {s.get("Name"): s for s in prototype.get("Select", [])}
                resolved = {role: [{"query_ref": p.get("queryRef"),
                    "select_expression": selected.get(p.get("queryRef")),
                    "source_aliases": prototype.get("From", [])} for p in projections]
                    for role, projections in visual.get("projections", {}).items()}
                result.append({"visual_id": config.get("name"), "page_id": page.get("name"),
                    "page": page.get("displayName"), "source_path": str(root / "report.json"), "format": "legacy",
                    "type": visual.get("visualType"), "projections": visual.get("projections", {}),
                    "query": prototype, "resolved_projections": resolved,
                    "title_properties": visual.get("vcObjects", {}).get("title", []),
                    "filters": {"report": report.get("filters"), "page": page.get("filters"), "visual": item.get("filters")},
                    "interactions": page.get("visualInteractions", page_config.get("relationships", [])),
                    "page_config": page_config, "sync_group": visual.get("syncGroup"),
                    "hidden": config.get("isHidden", False), "position": config.get("layouts"),
                    "formatting": visual.get("objects", {})})
    return result


def semantic_summary(rowsets):
    """Resolve engine IDs; raw DMV evidence remains authoritative."""
    def rows(name):
        return rowsets.get(name, {}).get("rows", [])
    tables = {str(r["ID"]): r for r in rows("TMSCHEMA_TABLES")}
    columns = {str(r["ID"]): r for r in rows("TMSCHEMA_COLUMNS")}
    def table(value):
        return tables.get(str(value), {}).get("Name", f"unresolved:{value}")
    def column(value):
        item = columns.get(str(value), {})
        return {"table": table(item.get("TableID")), "column": item.get("ExplicitName") or item.get("InferredName") or item.get("Name")}
    relationships = []
    for row in rows("TMSCHEMA_RELATIONSHIPS"):
        relationships.append({"id": row["ID"], "from": column(row.get("FromColumnID")),
            "to": column(row.get("ToColumnID")), "active": row.get("IsActive"),
            "from_cardinality": row.get("FromCardinality"), "to_cardinality": row.get("ToCardinality"),
            "cross_filter": row.get("CrossFilteringBehavior"), "security_filter": row.get("SecurityFilteringBehavior"),
            "referential_integrity_assumed": row.get("RelyOnReferentialIntegrity")})
    measures = [{"table": table(r.get("TableID")), "name": r.get("Name"),
                 "expression": r.get("Expression"), "format": r.get("FormatString"),
                 "state": r.get("State"), "error": r.get("ErrorMessage")} for r in rows("TMSCHEMA_MEASURES")]
    partitions = [{"table": table(r.get("TableID")), **r} for r in rows("TMSCHEMA_PARTITIONS")]
    return {"tables": [{"id": r["ID"], "name": r["Name"], "hidden": r.get("IsHidden")} for r in tables.values()],
            "measures": measures, "relationships": relationships, "partitions": partitions,
            "unavailable_rowsets": [k for k, v in rowsets.items() if "error" in v],
            "note": "Enum values and full expressions retained in raw rowsets. Dependencies do not prove runtime filter propagation or source lineage."}
