"""Evidence primitives for an agent-led validation case, not an autonomous oracle."""
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import shutil
import sys

from connections import dump, digest, query, ensure_evidence_writable
from powerbi_inventory import visual_bindings

HERE = Path(__file__).resolve().parent


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Evidence path escapes case: {relative}")
    return path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def init_case(directory, target):
    directory = Path(directory)
    ensure_evidence_writable(directory)
    if directory.exists():
        raise ValueError("Case already exists; resume it or choose a new run directory")
    directory.mkdir(parents=True)
    dump(directory / "case.json", {"schema_version": 2, "state_contract_version": 1, "id": directory.name,
         "created_at": now(), "target": target, "intent": "initial validation",
         "summary": "Investigation in progress", "scope": {}, "trace": [],
         "claims": [], "facts": [], "experiments": [], "findings": [], "limitations": [], "review": []})


def inspect_project(project, directory):
    project = Path(project).resolve()
    directory = Path(directory).resolve()
    ensure_evidence_writable(directory)
    config = read(project / "pilot.json")
    sources = []
    candidates = [project / config["definition_file"]] if config.get("definition_file") else []
    if config.get("dbt_project"):
        dbt = project / config["dbt_project"]
        candidates += list((dbt / "models").rglob("*.sql")) + list((dbt / "models").rglob("*.yml"))
        candidates += [dbt / "dbt_project.yml", dbt / "target/manifest.json", dbt / "target/run_results.json"]
    if config.get("engine_context"):
        candidates.append(project / config["engine_context"])
    report_dirs = []
    pbip = project / config.get("powerbi_project", "__no_powerbi_project__")
    if pbip.is_file():
        if pbip.suffix != ".pbix":
            candidates += [pbip]
        if pbip.suffix == ".pbir":
            report_dirs = [pbip.parent]
        elif pbip.suffix == ".pbip":
            report_dirs = [pbip.parent / a["report"]["path"] for a in read(pbip).get("artifacts", [])]
        elif pbip.suffix == ".pbix" and config.get("powerbi_report_snapshot"):
            report_dirs = [project / config["powerbi_report_snapshot"]]
        else:
            raise ValueError("Static inspection requires PBIP or definition.pbir; open PBIX in Desktop for runtime inspection")
        for report_dir in report_dirs:
            candidates += list(report_dir.rglob("*.json")) + list(report_dir.glob("*.pbir"))
            entrypoint = report_dir / "definition.pbir"
            binding = read(entrypoint).get("datasetReference", {}) if entrypoint.is_file() else {}
            if "byPath" in binding:
                semantic = (report_dir / binding["byPath"]["path"]).resolve()
                candidates += list(semantic.rglob("*.bim")) + list(semantic.rglob("*.tmdl")) + list(semantic.glob("*.pbism"))
        candidates += list((pbip.parent / "data").glob("provenance.json"))
    seen = set()
    for path in candidates:
        if not path.exists() or path in seen or ".pbi" in path.parts:
            continue
        seen.add(path)
        relative = path.relative_to(project).as_posix()
        target = safe_path(directory, "evidence/source/" + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        entry = {"path": relative, "evidence": target.relative_to(directory).as_posix(), "sha256": digest(target)}
        if path.name == "manifest.json":
            manifest = read(path)
            entry["nodes"] = [{"id": k, "relation": v.get("relation_name"), "depends_on": v.get("depends_on"), "compiled_code": v.get("compiled_code"), "path": v.get("original_file_path")} for k, v in manifest.get("nodes", {}).items()]
        elif path.suffix == ".bim":
            entry["model"] = read(path)
        elif path.name == "report.json":
            report = read(path)
            entry["pages"] = [{"name": p.get("displayName"), "filters": p.get("filters"), "visuals": [json.loads(v["config"]) for v in p.get("visualContainers", [])]} for p in report.get("sections", [])]
        elif path.suffix in [".sql", ".yml", ".md", ".tmdl"]:
            entry["content"] = path.read_text(encoding="utf-8-sig")
        sources.append(entry)
    tooling = []
    for source in sorted(HERE.glob("*.py")) + sorted((HERE.parent / "templates").glob("*.html")):
        target = directory / "evidence/tooling" / (("templates/" + source.name) if source.suffix == ".html" else source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        tooling.append({"path": target.relative_to(directory).as_posix(), "sha256": digest(target)})
    bindings = []
    for report_dir in report_dirs:
        for binding in visual_bindings(report_dir):
            path = Path(binding.pop("source_path")).relative_to(project)
            binding["source_evidence"] = "evidence/source/" + path.as_posix()
            bindings.append(binding)
    result = {"captured_at": now(), "project": str(project), "config": config, "sources": sources,
              "visual_bindings": bindings,
              "python_version": sys.version, "tooling": tooling,
              "note": "Static evidence only. Verify deployed models, source populations, import freshness and actual rendering."}
    if pbip.is_file() and pbip.suffix == ".pbix":
        result["pbix_identity"] = {"path": str(pbip), "sha256": digest(pbip),
            "note": "PBIX binary hashed, not copied into the case. Report snapshot is independently supplied extraction; verify its provenance."}
    dump(directory / "evidence/inventory.json", result)
    return result


def inventory_summary(inventory, directory):
    return {"inventory": str(Path(directory) / "evidence/inventory.json"),
            "source_files": len(inventory["sources"]),
            "visual_bindings": inventory["visual_bindings"][:100],
            "bindings_truncated": len(inventory["visual_bindings"]) > 100,
            "note": "Static report bindings require review even when rendered UI is unavailable."}


def validate_case(directory):
    directory = Path(directory)
    case = read(directory / "case.json")
    required = ["id", "target", "intent", "summary", "scope", "trace", "claims", "experiments", "findings", "limitations", "review"]
    errors = [f"Missing {key}" for key in required if key not in case]
    strict = case.get("schema_version") == 2
    if case.get("schema_version") not in [1, 2]:
        errors.append("Unsupported case schema version")
    if strict:
        try:
            created = dt.datetime.fromisoformat(case["created_at"].replace("Z", "+00:00"))
            if created.tzinfo is None or created > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
                raise ValueError("creation time needs a timezone and cannot be in the future")
            if case.get("reviewed_at") and dt.datetime.fromisoformat(case["reviewed_at"].replace("Z", "+00:00")) < created:
                raise ValueError("review time cannot precede creation time")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"Invalid case timestamp: {exc}")
        inventory_path = directory / "evidence/inventory.json"
        if inventory_path.is_file():
            bindings = read(inventory_path).get("visual_bindings", [])
            if bindings:
                review = case.get("scope", {}).get("binding_review", {})
                known = {b["visual_id"] for b in bindings}
                if review.get("status") != "reviewed" or not review.get("visual_ids") or not review.get("evidence"):
                    errors.append("Current static report bindings are available: scope.binding_review needs status=reviewed, target visual_ids and evidence")
                elif not set(review["visual_ids"]).issubset(known):
                    errors.append("Binding review references unknown visual IDs")
                for reference in review.get("evidence", []):
                    if not safe_path(directory, reference).is_file():
                        errors.append(f"Missing binding-review evidence: {reference}")
    if not case.get("claims"):
        errors.append("No claims assessed")
    ids = [c.get("id") for c in case.get("claims", [])]
    if len(ids) != len(set(ids)) or None in ids:
        errors.append("Claim IDs must be unique and present")
    for claim in case.get("claims", []):
        if strict:
            coverage = claim.get("coverage", {})
            if not claim.get("layer") or not coverage.get("required") or not isinstance(coverage.get("observed"), list):
                errors.append(f"{claim.get('id')}: layer and required/observed coverage are mandatory")
        for key in ["expected", "source", "status", "evidence"]:
            if key not in claim:
                errors.append(f"{claim.get('id')}: missing {key}")
        if claim.get("status") not in ["passed", "failed", "inconclusive"]:
            errors.append(f"{claim.get('id')}: invalid status")
        if claim.get("status") in ["passed", "failed"] and not claim.get("evidence"):
            errors.append(f"{claim.get('id')}: measured claim needs evidence")
        for reference in claim.get("evidence", []):
            path = safe_path(directory, reference)
            if not path.is_file():
                errors.append(f"Missing evidence: {reference}")
        needs_state = claim.get('layer') == 'interaction' or 'interaction' in claim.get('coverage', {}).get('required', [])
        if case.get('state_contract_version') == 1 and needs_state and claim.get('status') == 'passed' and not claim.get('state_checks'):
            errors.append(f"{claim.get('id')}: interaction pass requires state-check receipts")
        for reference in claim.get('state_checks', []):
            try:
                from state_checks import verify_receipt
                receipt = verify_receipt(safe_path(directory, reference), directory)
                if claim.get('status') == 'passed' and receipt['status'] != 'passed':
                    raise ValueError('Passed claim cites a failed state check')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(f"{claim.get('id')}: invalid state check {reference}: {exc}")
    for experiment in case.get("experiments", []):
        if not all(k in experiment for k in ["id", "question", "procedure", "result", "evidence"]):
            errors.append("Experiment lacks question, replay procedure, result or evidence")
        if strict:
            procedure = experiment.get("procedure", {})
            if not isinstance(procedure, dict) or not (procedure.get("commands") or procedure.get("command")):
                errors.append(f"{experiment.get('id')}: executable replay command is mandatory")
    for category in ["trace", "experiments", "findings"]:
        for record in case.get(category, []):
            for reference in record.get("evidence", []):
                if not isinstance(reference, str) or not safe_path(directory, reference).is_file():
                    errors.append(f"{category}: missing evidence {reference}")
    for finding in case.get("findings", []):
        if strict and not all(k in finding for k in ["id", "title", "explanation", "severity", "claim_ids", "impact", "evidence"]):
            errors.append("Finding lacks required business impact or supporting fields")
        if any(cid not in ids for cid in finding.get("claim_ids", [])):
            errors.append(f"{finding.get('id')}: references unknown claim")
    for claim in case.get("claims", []):
        coverage = claim.get("coverage", {})
        if isinstance(coverage, dict) and claim.get("status") == "passed":
            missing = set(coverage.get("required", [])) - set(coverage.get("observed", []))
            if missing:
                errors.append(f"{claim.get('id')}: passed claim has unobserved required layers {sorted(missing)}")
    if strict and not case.get("facts"):
        errors.append("At least one evidence-linked fact is required")
    for fact in case.get("facts", []):
        try:
            if not all(k in fact for k in ["id", "label", "evidence", "pointer", "value"]):
                raise ValueError("missing fact fields")
            actual = read(safe_path(directory, fact["evidence"]))
            if not fact["pointer"].startswith("/"):
                raise ValueError("expected a JSON pointer beginning with /")
            for token in fact["pointer"][1:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                actual = actual[int(token)] if isinstance(actual, list) else actual[token]
            if type(actual) is not type(fact["value"]) or actual != fact["value"]:
                raise ValueError("value differs from cited evidence; retain the original JSON type and value")
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            errors.append(f"Fact {fact.get('id')}: {exc}")
    for component in case.get("components", []):
        if not all(k in component for k in ["id", "name", "page", "definition", "claim_ids", "scenarios"]):
            errors.append(f"Component {component.get('id')} lacks id/name/page/definition/claim_ids/scenarios")
            continue
        if any(cid not in ids for cid in component["claim_ids"]):
            errors.append(f"Component {component['id']} references unknown claims")
        if not component["scenarios"]:
            errors.append(f"Component {component['id']} has no captured scenarios")
        for scenario in component["scenarios"]:
            images = [e for e in scenario.get("evidence", []) if str(e).lower().endswith(".png")]
            if not scenario.get("description") or not images:
                errors.append(f"Component {component['id']} scenario {scenario.get('id')} needs a description and a screenshot")
            for reference in scenario.get("evidence", []):
                if not isinstance(reference, str) or not safe_path(directory, reference).is_file():
                    errors.append(f"Component {component['id']}: missing scenario evidence {reference}")
    if any(r.get("decision") == "accepted" and not r.get("confirmation") for r in case.get("review", [])):
        errors.append("Accepted decisions require explicit confirmation provenance")
    return case, errors


def seal(directory):
    directory = Path(directory)
    if (directory / "manifest.json").exists():
        verify(directory)
        return read(directory / "manifest.json")
    case, errors = validate_case(directory)
    if not (directory / "manifest.json").exists() and case.get("schema_version") != 2:
        errors.append("New cases require schema_version 2; legacy cases can only be read/verified")
    if errors:
        raise ValueError("; ".join(errors))
    paths = [directory / "case.json"]
    for folder in ["evidence", "procedures"]:
        paths += [p for p in (directory / folder).rglob("*") if p.is_file()]
    manifest = {"sealed_at": now(), "files": {p.relative_to(directory).as_posix(): digest(p) for p in sorted(paths)}}
    dump(directory / "manifest.json", manifest)
    return manifest


def verify(directory):
    directory = Path(directory)
    manifest = read(directory / "manifest.json")
    if "case.json" not in manifest.get("files", {}):
        raise ValueError("Manifest does not include the case record")
    actual = {"case.json"}
    for folder in ["evidence", "procedures"]:
        actual.update(p.relative_to(directory).as_posix() for p in (directory / folder).rglob("*") if p.is_file())
    mismatches = [name for name, sha in manifest["files"].items() if not safe_path(directory, name).is_file() or digest(safe_path(directory, name)) != sha]
    if actual != set(manifest["files"]):
        detail = {"added": sorted(actual - set(manifest["files"])),
                  "missing": sorted(set(manifest["files"]) - actual), "changed_or_missing_recorded_files": mismatches}
        raise ValueError("Evidence file inventory changed since sealing: " + json.dumps(detail) + ". This concerns files INSIDE the historical case, not current project source changes.")
    if mismatches:
        raise ValueError("Evidence changed: " + ", ".join(mismatches))
    return {"verified_files": len(manifest["files"])}


def render(directory):
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    directory = Path(directory)
    if (directory / "manifest.json").is_file():
        verify(directory)
    case, errors = validate_case(directory)
    template_dirs = [d for d in (HERE.parent / "templates", HERE / "templates") if (d / "report.html").is_file()]
    if not template_dirs:
        raise FileNotFoundError("report.html template not found beside the scripts; run qa.py from the plugin, not from a copied tooling folder")
    env = Environment(loader=FileSystemLoader(template_dirs), autoescape=select_autoescape(["html"]))
    screenshots = []
    for path in (directory / "evidence").rglob("*.png"):
        if not path.is_file():
            continue
        state_file = path.parent / "state.json"
        state = read(state_file) if state_file.is_file() else {}
        screenshots.append({"path": path.relative_to(directory).as_posix(),
                            "status": state.get("observation_status", "Capture requires interpretation"),
                            "note": state.get("note", "")})
    html = env.get_template("report.html").render(case=case, errors=errors, screenshots=screenshots)
    (directory / "report.html").write_text(html, encoding="utf-8")
    return {"report": str(directory / "report.html"), "validation_errors": errors}


def profile_evidence(path, column):
    import math
    import pandas as pd
    evidence = read(path)
    frame = pd.DataFrame(evidence["rows"])
    if column not in frame.columns:
        raise ValueError("Profile column is absent or the result has no rows")
    values = pd.to_numeric(frame[column], errors="coerce")
    invalid = (frame[column].notna() & values.isna()).sum()
    statistics = values.describe(percentiles=[.01, .25, .5, .75, .99]).to_dict()
    statistics = {k: float(v) if math.isfinite(float(v)) else None for k, v in statistics.items()}
    return {"input_sha256": digest(path), "complete": evidence.get("complete", False), "column": column,
            "invalid_numeric": int(invalid), "null_count": int(frame[column].isna().sum()),
            "statistics": statistics, "interpretation": "Descriptive evidence, not a correctness verdict."}


def retain_evidence(baseline, directory, files):
    baseline, directory = Path(baseline).resolve(), Path(directory).resolve()
    verify(baseline)
    if directory.is_relative_to(baseline) or (directory / "manifest.json").exists():
        raise ValueError("Retain evidence only into a separate unsealed case")
    manifest = read(baseline / "manifest.json")
    destination = safe_path(directory, "evidence/retained/" + baseline.name)
    if destination.exists():
        raise ValueError("This baseline subset was already retained; use a new case")
    if not files or any(name not in manifest["files"] for name in files):
        raise ValueError("Select files recorded in the verified baseline manifest")
    destination.mkdir(parents=True)
    records = []
    for name in files:
        target = safe_path(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(safe_path(baseline, name), target)
        records.append({"source_path": name, "sha256": digest(target),
                        "evidence": target.relative_to(directory).as_posix()})
    shutil.copy2(baseline / "manifest.json", destination / "baseline-manifest.json")
    provenance = {"baseline_id": read(baseline / "case.json")["id"],
                  "baseline_manifest_sha256": digest(baseline / "manifest.json"),
                  "retained_at": now(), "files": records,
                  "note": "Exact copies of selected prior evidence, not fresh measurements or a complete baseline bundle."}
    dump(destination / "provenance.json", provenance)
    return provenance


def review_revision(directory, decisions_file, output):
    directory, output = Path(directory), Path(output)
    verify(directory)
    if output.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Review revision must be outside the original case")
    if output.exists():
        raise ValueError("Review revision must have a new directory")
    decisions = read(decisions_file)
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("Expected a nonempty decision list")
    case = read(directory / "case.json")
    claims = {c["id"]: c for c in case["claims"]}
    seen = set()
    reviewed_sha = digest(directory / 'manifest.json')
    for decision in decisions:
        if not all(decision.get(k) for k in ["claim_id", "decision", "reviewer", "confirmation"]):
            raise ValueError("Decision requires claim_id, decision, reviewer and explicit confirmation provenance")
        if decision["claim_id"] not in claims:
            raise ValueError("Unknown claim in review")
        if decision['claim_id'] in seen:
            raise ValueError('Duplicate decisions for one claim')
        seen.add(decision['claim_id'])
        if decision.get('reviewed_manifest_sha256') not in (None, reviewed_sha):
            raise ValueError('Decision refers to a different evidence manifest')
        if decision["decision"] not in ["accepted", "confirmed_defect", "unresolved", "exception"]:
            raise ValueError("Invalid review decision")
        if decision["decision"] == "accepted" and claims[decision["claim_id"]]["status"] != "passed":
            raise ValueError("Cannot accept an unpassed assertion; revise expectation and rerun, or record an exception")
        if decision["decision"] == "exception" and not all(decision.get(k) for k in ["reason", "scope", "expires_at"]):
            raise ValueError("Exceptions require reason, scope and expiry")
        decision["reviewed_manifest_sha256"] = digest(directory / "manifest.json")
        decision["recorded_at"] = now()
    shutil.copytree(directory, output, ignore=lambda src, names: ["manifest.json", "report.html"] if Path(src).resolve() == directory.resolve() else [])
    reviewed = output / "evidence/reviewed-baselines" / digest(directory / "manifest.json")
    reviewed.mkdir(parents=True, exist_ok=True)
    shutil.copy2(directory / "manifest.json", reviewed / "manifest.json")
    shutil.copy2(directory / "case.json", reviewed / "case.json")
    case["id"] = output.name
    case["review"] = decisions
    case["review_of"] = str(directory.resolve())
    case["reviewed_record"] = (reviewed / "case.json").relative_to(output).as_posix()
    dump(output / "case.json", case)
    seal(output)
    return render(output)


def describe_state(text, observed):
    """Caption for the sign-off page: the agent's sentence plus the observed numbers that prove the state."""
    parts = []
    for card, value in (observed.get("cards") or {}).items():
        shown = value if value is not None else (observed.get("cards_text") or {}).get(card)
        if shown is not None:
            parts.append(f"{card} {shown:,}" if isinstance(shown, (int, float)) else f"{card} {shown}")
    for key, value in (observed.get("tables") or {}).items():
        if value is not None:
            parts.append(f"{key} total {value:,}")
    if observed.get("date_start") and observed.get("date_end"):
        parts.append(f"{observed['date_start']} to {observed['date_end']}")
    for title, caption in (observed.get("slicers") or {}).items():
        if caption and caption != "All":
            parts.append(f"{title}: {caption}")
    charts = observed.get("charts") or {}
    selected = sum(c.get("selected_marks", 0) for c in charts.values())
    if selected:
        parts.append(f"{selected} selected mark" + ("s" if selected != 1 else ""))
    suffix = " Observed: " + "; ".join(parts) + "." if parts else ""
    text = text.strip()
    return text + ("" if text.endswith((".", "!", "?")) else ".") + suffix


def attach_component(directory, run, spec_file):
    """Attach a completed pbi_cycle run to an unsealed case as a reviewable component.

    Copies the run's evidence (captures, receipts, oracles, journal, plan) into
    evidence/runs/<run-name>, turns every captured state into a scenario with its
    screenshot, and records the component's claims with their receipts. Facts are
    added for every derived card/table value so the report's numbers stay bound to
    the observation files.
    """
    directory, run = Path(directory).resolve(), Path(run).resolve()
    ensure_evidence_writable(directory)
    if (directory / "manifest.json").exists():
        raise ValueError("Case is sealed; attach components before sealing")
    spec = read(spec_file)
    journal = read(run / "evidence/journal.json")
    if journal.get("status") != "completed":
        raise ValueError(f"Run did not complete cleanly: {journal.get('status')}; repair and rerun into a fresh directory")
    destination = safe_path(directory, "evidence/runs/" + run.name)
    if destination.exists():
        raise ValueError("This run is already attached")
    shutil.copytree(run / "evidence", destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if (run / "plan.json").is_file():
        shutil.copy2(run / "plan.json", destination / "plan.json")
    rel = lambda name: (destination / name).relative_to(directory).as_posix()
    case = read(directory / "case.json")
    ids = {c["id"] for c in case["claims"]}
    scenarios, facts = [], []
    for state in journal["states"]:
        label = state["label"]
        for suffix in ("screen.png", "state.json", "check.json"):
            if not (destination / label / suffix).is_file():
                raise ValueError(f"State {label} lacks {suffix}")
        from state_checks import verify_receipt
        receipt = verify_receipt(destination / label / "check.json", directory)
        if receipt["status"] != "passed":
            raise ValueError(f"State {label} has a failed receipt; it cannot become a reviewable scenario")
        observed = read(destination / label / "state.json")
        scenarios.append({"id": f"{spec['id']}-{label}", "description": describe_state(state.get("description") or label, observed),
                          "evidence": [rel(f"{label}/screen.png"), rel(f"{label}/state.json"), rel(f"{label}/check.json")]})
        for card, value in (observed.get("cards") or {}).items():
            facts.append({"id": f"{spec['id']}-{label}-{re.sub(r'[^A-Za-z0-9]+', '_', card)}", "label": f"{label}: {card}",
                          "evidence": rel(f"{label}/state.json"), "pointer": "/cards/" + card.replace("~", "~0").replace("/", "~1"), "value": value, "unit": ""})
        for key, value in (observed.get("tables") or {}).items():
            facts.append({"id": f"{spec['id']}-{label}-table_{key}", "label": f"{label}: {key} total",
                          "evidence": rel(f"{label}/state.json"), "pointer": "/tables/" + key, "value": value, "unit": ""})
    oracle_files = [rel("dax/" + p.name) for p in sorted((destination / "dax").glob("*.json"))] if (destination / "dax").is_dir() else []
    states = {s["label"] for s in journal["states"]}
    for claim in spec["claims"]:
        if claim["id"] in ids:
            raise ValueError(f"Claim {claim['id']} already exists in the case")
        chosen = claim.get("states") or sorted(states)
        unknown = [s for s in chosen if s not in states]
        if unknown:
            raise ValueError(f"Claim {claim['id']} cites states that were not captured: {unknown}")
        layer = claim.get("layer", "interaction")
        layers = {"interaction": ["engine", "render", "interaction"], "render": ["engine", "render"], "engine": ["engine"], "cross-layer": ["engine", "render"]}[layer]
        record = {"id": claim["id"], "expected": claim["expected"], "observed": claim["observed"],
                  "source": claim.get("source", "Observed implementation, verified controls and current DAX; business approval pending."),
                  "status": claim.get("status", "passed"), "layer": layer,
                  "coverage": {"required": layers, "observed": layers if claim.get("status", "passed") != "inconclusive" else claim.get("observed_layers", layers)},
                  "evidence": [rel(f"{s}/state.json") for s in chosen] + oracle_files + list(claim.get("extra_evidence", [])),
                  "state_checks": [rel(f"{s}/check.json") for s in chosen] if layer == "interaction" else []}
        case["claims"].append(record)
        ids.add(claim["id"])
    component = {"id": spec["id"], "name": spec["name"], "page": spec.get("page", journal.get("page")),
                 "definition": spec["definition"], "claim_ids": [c["id"] for c in spec["claims"]] + list(spec.get("related_claim_ids", [])),
                 "scenarios": scenarios, "run": rel("journal.json")}
    if any(c["id"] == component["id"] for c in case.get("components", [])):
        raise ValueError("Component id already present")
    case.setdefault("components", []).append(component)
    case.setdefault("facts", []).extend(facts)
    if spec.get("visual_ids"):
        review = case.setdefault("scope", {}).setdefault("binding_review", {"status": "reviewed", "visual_ids": [], "evidence": ["evidence/inventory.json"]})
        review["status"] = "reviewed"
        review["visual_ids"] = sorted(set(review.get("visual_ids", [])) | set(spec["visual_ids"]))
        review.setdefault("evidence", ["evidence/inventory.json"])
    for experiment in spec.get("experiments", []):
        experiment.setdefault("evidence", [rel("journal.json")])
        experiment.setdefault("procedure", {})
        experiment["procedure"].setdefault("commands", [f"python <plugin>/scripts/pbi_cycle.py --connection connection.json --plan {rel('plan.json')} --out <fresh directory>"])
        case["experiments"].append(experiment)
    dump(directory / "case.json", case)
    return {"component": component["id"], "scenarios": len(scenarios), "claims": [c["id"] for c in spec["claims"]], "facts": len(facts)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("--case", required=True); init.add_argument("--target", required=True); init.add_argument("--project")
    ins = sub.add_parser("inspect"); ins.add_argument("--project", required=True); ins.add_argument("--case", required=True)
    sql = sub.add_parser("sql"); sql.add_argument("--config", required=True); sql.add_argument("--file", required=True); sql.add_argument("--out", required=True); sql.add_argument("--limit", type=int, default=5000)
    for name in ["seal", "verify", "render", "validate"]:
        parser = sub.add_parser(name); parser.add_argument("--case", required=True)
    stats = sub.add_parser("profile"); stats.add_argument("--file", required=True); stats.add_argument("--column", required=True); stats.add_argument("--out", required=True)
    rev = sub.add_parser("review"); rev.add_argument("--case", required=True); rev.add_argument("--decisions", required=True); rev.add_argument("--out", required=True)
    comp = sub.add_parser("component"); comp.add_argument("--case", required=True); comp.add_argument("--run", required=True); comp.add_argument("--spec", required=True)
    retain = sub.add_parser("retain"); retain.add_argument("--baseline", required=True); retain.add_argument("--case", required=True); retain.add_argument("--files", nargs="+", required=True)
    a = p.parse_args()
    if a.command == "init":
        init_case(a.case, a.target); result = {"case": a.case}
        if a.project:
            result.update(inventory_summary(inspect_project(a.project, a.case), a.case))
    elif a.command == "inspect":
        result = inspect_project(a.project, Path(a.case))
        result = inventory_summary(result, a.case)
    elif a.command == "sql":
        ensure_evidence_writable(a.out)
        if Path(a.out).exists():
            raise ValueError("Evidence already exists; use a new output name")
        result = query(read(a.config), Path(a.file).read_text(encoding="utf-8-sig"), a.limit)
        dump(a.out, result)
        result = {"output": a.out, "complete": result["complete"], "returned_rows": result["returned_rows"], "rows": result["rows"][:30]}
    elif a.command == "profile":
        ensure_evidence_writable(a.out)
        if Path(a.out).exists():
            raise ValueError("Profile evidence already exists; use a new output name")
        result = profile_evidence(a.file, a.column)
        dump(a.out, result)
    elif a.command == "review":
        result = review_revision(a.case, a.decisions, a.out)
    elif a.command == "component":
        result = attach_component(a.case, a.run, a.spec)
    elif a.command == "retain":
        result = retain_evidence(a.baseline, a.case, a.files)
    elif a.command == "validate":
        _, errors = validate_case(a.case); result = {"errors": errors}
        if errors:
            print(json.dumps(result)); return 1
    else:
        result = {"seal": seal, "verify": verify, "render": render}[a.command](a.case)
    print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
