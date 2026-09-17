"""Evidence primitives for an agent-led validation case, not an autonomous oracle."""
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import shutil
import sys

from analyst_view import is_technical
from connections import dump, digest, query, ensure_evidence_writable
from powerbi_inventory import visual_bindings

HERE = Path(__file__).resolve().parent

# Layers a component claim may declare, mapped to the coverage they require.
COMPONENT_LAYERS = {"interaction": ["engine", "render", "interaction"], "render": ["engine", "render"],
                    "engine": ["engine"], "cross-layer": ["engine", "render"]}
# Layers any claim in a case may declare (see references/case-format.md).
CASE_LAYERS = ["sql", "engine", "binding", "render", "interaction", "cross-layer"]
# Decision vocabulary written by review_revision() and exported by review_form.py.
REVIEW_DECISIONS = ["accepted", "confirmed_defect", "unresolved", "exception"]
LAYER_HELP = ("interaction = a slicer/click changed the numbers and receipts prove it; "
              "render = the screen shows the engine's numbers; engine = DAX only; "
              "cross-layer = source vs engine comparison")
# The two fields the analyst actually reads on the sign-off page. `definition`,
# `expected`, `observed` and `source` stay technical on purpose; these do not.
PLAIN_LANGUAGE_HELP = ("write this for the analyst: no DAX, table or column names; "
                       "say what the figure is and what the reader should compare")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_path(root, relative, owner=None):
    """Resolve an evidence reference inside the case, naming the record that holds it."""
    root = Path(root).resolve()
    text = str(relative)
    complaint = (f"{owner + ': ' if owner else ''}path {text!r} escapes the case directory "
                 "(evidence paths must be relative, like evidence/runs/x/state.json)")
    if not text.strip() or text.startswith(("/", "\\")) or Path(text).is_absolute():
        raise ValueError(complaint)
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(complaint)
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
        target = safe_path(directory, "evidence/source/" + relative, f"inspected source {relative}")
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
                    if not safe_path(directory, reference, "scope.binding_review evidence").is_file():
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
            elif claim["layer"] not in CASE_LAYERS:
                errors.append(f"{claim.get('id')}: unknown layer {claim['layer']!r}; use one of {CASE_LAYERS} ({LAYER_HELP})")
            token = is_technical(claim.get("question"))
            if token:
                errors.append(f"{claim.get('id')}: question reads {token!r}; {PLAIN_LANGUAGE_HELP}")
        for key in ["expected", "source", "status", "evidence"]:
            if key not in claim:
                errors.append(f"{claim.get('id')}: missing {key}")
        if claim.get("status") not in ["passed", "failed", "inconclusive"]:
            errors.append(f"{claim.get('id')}: invalid status")
        if claim.get("status") in ["passed", "failed"] and not claim.get("evidence"):
            errors.append(f"{claim.get('id')}: measured claim needs evidence")
        for reference in claim.get("evidence", []):
            path = safe_path(directory, reference, f"claim {claim.get('id')} evidence")
            if not path.is_file():
                errors.append(f"Missing evidence: {reference}")
        needs_state = claim.get('layer') == 'interaction' or 'interaction' in claim.get('coverage', {}).get('required', [])
        if case.get('state_contract_version') == 1 and needs_state and claim.get('status') == 'passed' and not claim.get('state_checks'):
            errors.append(f"{claim.get('id')}: interaction pass requires state-check receipts")
        for reference in claim.get('state_checks', []):
            try:
                from state_checks import verify_receipt
                receipt = verify_receipt(safe_path(directory, reference, f"claim {claim.get('id')} state_checks"), directory)
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
        for index, record in enumerate(case.get(category, [])):
            owner = f"{category} {record.get('id', index)} evidence"
            for reference in record.get("evidence", []):
                if not isinstance(reference, str) or not safe_path(directory, reference, owner).is_file():
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
            actual = read(safe_path(directory, fact["evidence"], f"fact {fact.get('id')}"))
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
        if strict:
            token = is_technical(component.get("what_it_shows"))
            if token:
                errors.append(f"Component {component['id']}: what_it_shows reads {token!r}; {PLAIN_LANGUAGE_HELP}")
        if not component["scenarios"]:
            errors.append(f"Component {component['id']} has no captured scenarios")
        for scenario in component["scenarios"]:
            images = [e for e in scenario.get("evidence", []) if str(e).lower().endswith(".png")]
            if not scenario.get("description") or not images:
                errors.append(f"Component {component['id']} scenario {scenario.get('id')} needs a description and a screenshot")
            owner = f"component {component['id']} scenario {scenario.get('id')}"
            for reference in scenario.get("evidence", []):
                if not isinstance(reference, str) or not safe_path(directory, reference, owner).is_file():
                    errors.append(f"Component {component['id']}: missing scenario evidence {reference}")
    if strict:
        errors += orphan_evidence_errors(directory, case)
    for index, record in enumerate(case.get("review", [])):
        reason = review_record_problem(record, ids)
        if reason:
            errors.append(f"review[{index}] is not a reviewer decision record; the review block is filled only by "
                          f"qa.py review from an exported decisions file ({reason})")
    if any(r.get("decision") == "accepted" and not r.get("confirmation") for r in case.get("review", []) if isinstance(r, dict)):
        errors.append("Accepted decisions require explicit confirmation provenance")
    return case, errors


def review_record_problem(record, claim_ids):
    """Why this review entry is not a reviewer decision record, or None when it is one."""
    if not isinstance(record, dict):
        return f"expected an object, got {type(record).__name__}"
    if record.get("claim_id") not in claim_ids:
        return f"claim_id {record.get('claim_id')!r} is not a claim in this case"
    if record.get("decision") not in REVIEW_DECISIONS:
        return f"decision {record.get('decision')!r} is not one of {REVIEW_DECISIONS}"
    if not str(record.get("reviewer") or "").strip():
        return "reviewer is empty"
    if not record.get("recorded_at"):
        return "recorded_at is missing"
    return None


def orphan_evidence_errors(directory, case):
    """Attached run and detector directories that no record in case.json accounts for."""
    directory = Path(directory)
    errors = []
    runs = [c.get("run") for c in case.get("components", [])]
    for folder in sorted(p for p in (directory / "evidence/runs").glob("*") if (p / "journal.json").is_file()):
        name = folder.name
        owners = [r for r in runs if r == f"evidence/runs/{name}/journal.json"]
        if not owners:
            errors.append(f"evidence/runs/{name} is attached but no component records it; "
                          "do not hand-edit case.json, attach runs with qa.py component")
        elif len(owners) > 1:
            errors.append(f"evidence/runs/{name} is recorded by {len(owners)} components; "
                          "a run belongs to exactly one component")
    cited = [str(e) for claim in case.get("claims", []) for e in claim.get("evidence", []) if isinstance(e, str)]
    for folder in sorted(p for p in (directory / "evidence/detectors").glob("*") if p.is_dir()):
        if not any(e.startswith(f"evidence/detectors/{folder.name}/") for e in cited):
            errors.append(f"evidence/detectors/{folder.name} is attached but no claim cites it; "
                          "do not hand-edit case.json, attach detector runs with qa.py detect")
    return errors


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
    mismatches = [name for name, sha in manifest["files"].items()
                  if not safe_path(directory, name, f"manifest entry {name}").is_file()
                  or digest(safe_path(directory, name, f"manifest entry {name}")) != sha]
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
    destination = safe_path(directory, "evidence/retained/" + baseline.name, f"retained baseline {baseline.name}")
    if destination.exists():
        raise ValueError("This baseline subset was already retained; use a new case")
    if not files or any(name not in manifest["files"] for name in files):
        raise ValueError("Select files recorded in the verified baseline manifest")
    destination.mkdir(parents=True)
    records = []
    for name in files:
        target = safe_path(destination, name, f"retained file {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(safe_path(baseline, name, f"baseline file {name}"), target)
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

    Everything that can be refused is checked against the SOURCE run before a
    single file is copied, so a rejected spec leaves no half-attached run behind
    and the corrected spec still attaches.

    Two optional spec fields are written for the analyst rather than for the
    record: the component's `what_it_shows` (one or two plain sentences that head
    the sign-off card) and a claim's `question` (the one sentence the analyst
    answers). Both are refused by strict validation when they read like DAX;
    `definition`, `expected`, `observed` and `source` stay technical.
    """
    directory, run = Path(directory).resolve(), Path(run).resolve()
    ensure_evidence_writable(directory)
    if (directory / "manifest.json").exists():
        raise ValueError("Case is sealed; attach components before sealing")
    spec = read(spec_file)
    for key in ("id", "name", "definition", "claims"):
        if not spec.get(key):
            raise ValueError(f"Component spec needs a non-empty {key}")
    journal = read(run / "evidence/journal.json")
    if journal.get("status") != "completed":
        raise ValueError(f"Run did not complete cleanly: {journal.get('status')}; repair and rerun into a fresh directory")
    case = read(directory / "case.json")
    ids = {c["id"] for c in case["claims"]}
    destination = safe_path(directory, "evidence/runs/" + run.name, f"component {spec['id']} run")
    recovered = clear_leftover_copy(destination, directory, case, f"evidence/runs/{run.name}/journal.json")
    if any(c["id"] == spec["id"] for c in case.get("components", [])):
        raise ValueError("Component id already present")
    source = run / "evidence"
    states = {s["label"] for s in journal["states"]}
    from state_checks import verify_receipt
    for state in journal["states"]:
        label = state["label"]
        for suffix in ("screen.png", "state.json", "check.json"):
            if not (source / label / suffix).is_file():
                raise ValueError(f"State {label} lacks {suffix}")
        if verify_receipt(source / label / "check.json", run)["status"] != "passed":
            raise ValueError(f"State {label} has a failed receipt; it cannot become a reviewable scenario")
    for claim in spec["claims"]:
        for key in ("id", "expected", "observed"):
            if not claim.get(key):
                raise ValueError(f"Claim {claim.get('id')!r} in the spec needs a non-empty {key}")
        if claim["id"] in ids:
            raise ValueError(f"Claim {claim['id']} already exists in the case")
        layer = claim.get("layer", "interaction")
        if layer not in COMPONENT_LAYERS:
            raise ValueError(f"Claim {claim['id']}: unknown layer {layer!r}; use one of {sorted(COMPONENT_LAYERS)} ({LAYER_HELP})")
        unknown = [s for s in (claim.get("states") or sorted(states)) if s not in states]
        if unknown:
            raise ValueError(f"Claim {claim['id']} cites states that were not captured: {unknown}")
        ids.add(claim["id"])
    copied = False
    try:
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        copied = True
        if (run / "plan.json").is_file():
            shutil.copy2(run / "plan.json", destination / "plan.json")
        rel = lambda name: (destination / name).relative_to(directory).as_posix()
        scenarios, facts = [], []
        for state in journal["states"]:
            label = state["label"]
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
        for claim in spec["claims"]:
            chosen = claim.get("states") or sorted(states)
            layer = claim.get("layer", "interaction")
            layers = COMPONENT_LAYERS[layer]
            record = {"id": claim["id"], "expected": claim["expected"], "observed": claim["observed"],
                      "source": claim.get("source", "Observed implementation, verified controls and current DAX; business approval pending."),
                      "status": claim.get("status", "passed"), "layer": layer,
                      "coverage": {"required": layers, "observed": layers if claim.get("status", "passed") != "inconclusive" else claim.get("observed_layers", layers)},
                      "evidence": [rel(f"{s}/state.json") for s in chosen] + oracle_files + list(claim.get("extra_evidence", [])),
                      "state_checks": [rel(f"{s}/check.json") for s in chosen] if layer == "interaction" else []}
            if str(claim.get("question") or "").strip():
                record["question"] = str(claim["question"]).strip()
            case["claims"].append(record)
        component = {"id": spec["id"], "name": spec["name"], "page": spec.get("page", journal.get("page")),
                     "definition": spec["definition"], "claim_ids": [c["id"] for c in spec["claims"]] + list(spec.get("related_claim_ids", [])),
                     "scenarios": scenarios, "run": rel("journal.json")}
        if str(spec.get("what_it_shows") or "").strip():
            component["what_it_shows"] = str(spec["what_it_shows"]).strip()
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
    except BaseException:
        if copied:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    return {"component": component["id"], "scenarios": len(scenarios), "claims": [c["id"] for c in spec["claims"]],
            "facts": len(facts), "recovered_leftover_copy": recovered}


def clear_leftover_copy(destination, directory, case, reference):
    """An existing copy is an attachment only if a record in case.json points at it.

    Otherwise it is debris from an attempt that was refused after copying, and is
    removed so the corrected input can be attached. Returns True when that happened.
    """
    destination = Path(destination)
    if not destination.exists():
        return False
    kind = "run" if reference.startswith("evidence/runs/") else "detector run"
    owners = [c for c in case.get("components", []) if c.get("run") == reference] if kind == "run" else \
             [c for c in case.get("claims", []) if any(str(e).startswith(reference) for e in c.get("evidence", []))]
    if owners:
        raise ValueError(
            f"This {kind} is already attached: {destination.relative_to(Path(directory).resolve()).as_posix()} is recorded by "
            f"{sorted(o['id'] for o in owners)} in case.json. Nothing to do; to attach a changed run, rerun it into a fresh "
            "directory and attach that, or start a new case. If you meant to replace it, delete the recording and the copy together.")
    shutil.rmtree(destination)
    return True


def attach_detectors(directory, run, kind, component_id=None):
    """Attach a lint result or a probe run to an unsealed case as detector claims.

    kind='lint' expects <run>/lint.json (model_lint.py); every finding becomes an
    inconclusive cross-layer claim carrying the literal fragment. kind='probes'
    expects <run>/evidence/journal.json (probes.py); each probe becomes a claim
    whose status follows the probe (passed / failed / inconclusive for review).
    Claims are linked to the component when one is given, otherwise they appear
    under 'Other expectations' on the sign-off page. As with components, every
    refusal is decided from the source run before anything is copied.
    """
    directory, run = Path(directory).resolve(), Path(run).resolve()
    ensure_evidence_writable(directory)
    if (directory / "manifest.json").exists():
        raise ValueError("Case is sealed; attach detectors before sealing")
    if kind not in ("lint", "probes"):
        raise ValueError("kind must be lint or probes")
    case = read(directory / "case.json")
    ids = {c["id"] for c in case["claims"]}
    if component_id and not any(c["id"] == component_id for c in case.get("components", [])):
        raise ValueError(f"Component {component_id} is not attached yet; attach the component first")
    prefix = "evidence/detectors/" + run.name
    destination = safe_path(directory, prefix, f"detector run {run.name}")
    recovered = clear_leftover_copy(destination, directory, case, prefix + "/")
    rel = lambda name: f"{prefix}/{name}"
    new_claims, new_findings, new_facts = [], [], []
    if kind == "lint":
        source = run / "lint.json"
        result = read(source)
        evidence = rel("lint.json")
        grouped = {}
        for finding in result["findings"]:
            grouped.setdefault(finding["method"], []).append(finding)
        for method, hits in grouped.items():
            cid = "LINT-" + method.split(".", 1)[1].replace("_", "-")
            if cid in ids:
                raise ValueError(f"Claim {cid} already exists")
            shown = hits[:12]
            listing = "; ".join(f"{h['object']}: {h['fragment'][:120]}" for h in shown)
            more = f" ... and {len(hits) - len(shown)} more (see the lint file)" if len(hits) > len(shown) else ""
            new_claims.append({"id": cid, "expected": catalog_claim(method),
                               "observed": f"{len(hits)} object(s). {hits[0]['why']} Hits: {listing}{more}",
                               "source": f"Model lint {method} (severity {hits[0]['severity']}); agent explanation pending.",
                               "status": "inconclusive", "layer": "cross-layer",
                               "coverage": {"required": ["binding", "engine"], "observed": ["binding"]},
                               "evidence": [evidence], "detector": method, "severity": hits[0]["severity"], "hits": len(hits),
                               "objects": [h["object"] for h in hits]})
            ids.add(cid)
        new_facts.append({"id": f"LINT-count-{run.name}", "label": "Model lint findings", "evidence": evidence, "pointer": "/summary", "value": result["summary"], "unit": ""})
    elif kind == "probes":
        journal = read(run / "evidence/journal.json")
        if journal.get("status") != "completed":
            raise ValueError("Probe run did not complete")
        for record in journal["probes"]:
            probe_file = rel(f"probes/{record['id']}.json")
            probe = read(run / "evidence/probes" / f"{record['id']}.json")
            cid = f"PRB-{record['id']}"
            if cid in ids:
                raise ValueError(f"Claim {cid} already exists")
            status = {"passed": "passed", "failed": "failed"}.get(record["status"], "inconclusive")
            outcome = probe.get("outcome", {})
            observed = probe.get("error") or summarize_outcome(record["method"], outcome)
            new_claims.append({"id": cid, "expected": record.get("question") or catalog_claim(record["method"]),
                               "observed": f"{record['method']}: {observed}",
                               "source": "Detector probe on the live model in the component's filter context; agent explanation pending for review flags.",
                               "status": status, "layer": "engine",
                               "coverage": {"required": ["engine"], "observed": ["engine"]},
                               "evidence": [probe_file], "detector": record["method"], "severity": record.get("severity", "review"),
                               "probe_status": record["status"]})
            ids.add(cid)
            if record["status"] in ("failed", "review") and "status" in outcome:
                new_facts.append({"id": f"{cid}-status", "label": f"{record['id']} outcome", "evidence": probe_file, "pointer": "/outcome/status", "value": outcome["status"], "unit": ""})
            if record["status"] == "failed":
                new_findings.append({"id": f"FD-{cid}", "title": record.get("question") or record["id"], "severity": record.get("severity", "medium"),
                                     "claim_ids": [cid], "explanation": f"Probe {record['method']} failed: {observed[:400]}",
                                     "impact": "Stated invariant does not hold in the imported population; the affected visuals inherit the error.",
                                     "evidence": [probe_file]})
    copied = False
    try:
        if kind == "lint":
            destination.mkdir(parents=True)
            copied = True
            shutil.copy2(run / "lint.json", destination / "lint.json")
        else:
            shutil.copytree(run / "evidence", destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            copied = True
            if (run / "plan.json").is_file():
                shutil.copy2(run / "plan.json", destination / "plan.json")
        case["claims"] += new_claims
        case.setdefault("facts", []).extend(new_facts)
        case.setdefault("findings", []).extend(new_findings)
        if component_id:
            component = next(c for c in case["components"] if c["id"] == component_id)
            component["claim_ids"] += [c["id"] for c in new_claims]
        dump(directory / "case.json", case)
    except BaseException:
        if copied:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    return {"kind": kind, "claims": [c["id"] for c in new_claims], "findings": len(new_findings),
            "statuses": {s: sum(c["status"] == s for c in new_claims) for s in ("passed", "failed", "inconclusive")},
            "recovered_leftover_copy": recovered}


def summarize_outcome(method, outcome):
    """Analyst-readable sentence for a probe outcome; the raw method output stays in the evidence file."""
    status = outcome.get("status")
    if method == "stat.weekday_robust_band":
        flagged = outcome.get("flagged", [])
        if status == "inconclusive":
            return f"Not testable: {outcome.get('reason', 'too few points')} ({outcome.get('points', 0)} days)."
        if not flagged:
            return f"All {outcome.get('points')} days sit within their weekday band (robust z <= {outcome.get('threshold')})."
        items = "; ".join(f"{f['key']} ({f['weekday']}) {f['value']:,.0f} vs weekday median {f['weekday_median']:,.0f}, z {f['z']}" for f in flagged[:8])
        return f"{len(flagged)} day(s) outside the weekday band over {outcome.get('points')} days: {items}. Each needs a cause (holiday, campaign, import gap, duplicate load) before sign-off."
    if method == "stat.ratio_stability":
        flagged = outcome.get("flagged", [])
        bound = [f for f in flagged if f.get("reason") == "bound"]
        band = [f for f in flagged if f.get("reason") == "band"]
        text = f"{outcome.get('testable_points', 0)} days with enough volume (denominator >= {outcome.get('minimum_denominator')}); {len(outcome.get('low_volume_points', []))} low-volume days not judged."
        if bound:
            text += " Above the hard bound on " + "; ".join(f"{f['key']}: {f['numerator']}/{f['denominator']} = {f['ratio']:.2f}" for f in bound[:8]) + (f" and {len(bound) - 8} more" if len(bound) > 8 else "") + "."
        if band:
            text += " Outside the historical band on " + "; ".join(f"{f['key']}: {f['ratio']:.2f}" for f in band[:8]) + "."
        if not flagged and status == "clean":
            text += " No day breaks the bound or the band."
        return text
    if method == "stat.changepoints":
        shifts = outcome.get("shifts", [])
        if status == "inconclusive":
            return f"Not testable: {outcome.get('reason')}."
        if not shifts:
            return "No level shift beyond the noise threshold in the series."
        return "Level shift(s) at " + "; ".join(f"{s['key']}: median {s['before_median']:,.0f} -> {s['after_median']:,.0f} ({s['shift_sigma']} sigma)" for s in shifts) + ". Each needs a known business or tracking event."
    if method == "stat.population_stability":
        text = f"PSI {outcome.get('psi')} (threshold {outcome.get('threshold')})."
        moved = outcome.get("new_or_missing_categories", [])
        if moved:
            text += " New or vanished categories: " + ", ".join(m["label"] for m in moved[:10]) + "."
        top = outcome.get("top_contributors", [])[:3]
        if top:
            text += " Largest movers: " + ", ".join(f"{t['label']} {t['baseline_share']:.1%} -> {t['current_share']:.1%}" for t in top) + "."
        return text
    if method == "stat.ratio_of_totals_vs_mean_of_ratios":
        return f"Ratio of totals {outcome.get('ratio_of_totals')} versus mean of {outcome.get('members')} member ratios {outcome.get('mean_of_member_ratios')} (gap {outcome.get('gap')}). A displayed total equal to the mean of ratios is the wrong aggregation."
    if method == "dax.additivity":
        return f"Parts sum {outcome.get('parts_sum'):,.2f} vs total {outcome.get('total'):,.2f}; remainder {outcome.get('remainder'):,.2f} over {outcome.get('parts')} members (tolerance {outcome.get('tolerance')})."
    if method == "dax.fan_out":
        text = f"{outcome.get('base_rows'):,} rows; {outcome.get('joined_rows'):,} after the relationship path (multiplier {outcome.get('multiplier')})."
        if outcome.get("distinct_keys") is not None:
            text += f" {outcome['distinct_keys']:,} distinct business keys, {outcome.get('duplicate_rows')} duplicate row(s)."
        return text
    if method == "dax.assert":
        return f"Observed {outcome.get('actual')} {outcome.get('operator')} expected {outcome.get('expected')}" + (f" (tolerance {outcome.get('tolerance')})" if outcome.get("tolerance") is not None else "") + f": {'holds' if status == 'clean' else 'does not hold'}."
    return json.dumps({k: v for k, v in outcome.items() if k != "method"}, default=str)[:1200]


def catalog_claim(method):
    catalog = HERE.parent / "detectors/catalog.json"
    if catalog.is_file():
        for entry in read(catalog).get("detectors", []):
            if entry["id"] == method or entry.get("implemented_as") == method:
                return entry["claim"]
    return f"Detector {method} expectation holds."


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
    comp = sub.add_parser("component", help="Attach a completed pbi_cycle run as a reviewable component",
                          description="Attach a completed pbi_cycle run to an unsealed case as a reviewable component. "
                                      "Nothing is copied until the spec, the run's receipts and the cited states all pass, "
                                      "so a refused attempt can be corrected and retried.")
    comp.add_argument("--case", required=True); comp.add_argument("--run", required=True)
    comp.add_argument("--spec", required=True, help="Component spec JSON: {id, name, page, definition, visual_ids, claims:[{id, expected, "
                                                    "observed, layer, states}], experiments}. Claim layer is one of "
                                                    + ", ".join(sorted(COMPONENT_LAYERS)) + " (" + LAYER_HELP + ").")
    det = sub.add_parser("detect"); det.add_argument("--case", required=True); det.add_argument("--run", required=True); det.add_argument("--kind", choices=["lint", "probes"], required=True); det.add_argument("--component")
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
    elif a.command == "detect":
        result = attach_detectors(a.case, a.run, a.kind, a.component)
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
