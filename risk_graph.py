"""Evidence-grounded relationship graph for reviewed risks and scenarios.

This projection deliberately labels shared evidence and retrieval similarity;
it must not be read as a causal model or a forecast.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from link_groups import canonical_url
from risk_analysis import read_risks
from risk_views import priority_score
from graph_rag import _json, _table_rows, _workflow_graph


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return "".join(ch for ch in _clean(value).casefold() if ch.isalnum())


def _one(params: dict[str, Any], key: str, default: str = "") -> str:
    value = params.get(key, default)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    return str(value or default).strip()


def _risk_matches(risk: dict[str, Any], params: dict[str, Any]) -> bool:
    query = _one(params, "q").casefold()
    domain = _one(params, "domain")
    severity = _one(params, "severity")
    likelihood = _one(params, "likelihood")
    status = _one(params, "status")
    if domain == "all": domain = ""
    if severity == "all": severity = ""
    if likelihood == "all": likelihood = ""
    if status == "all": status = ""
    if status in {"withdrawn", "history", "resolved", "superseded"}:
        lifecycle = risk.get("lifecycle")
        if status == "withdrawn" and lifecycle != "withdrawn_source_mismatch": return False
        if status == "superseded" and lifecycle != "superseded": return False
        if status == "history" and not risk.get("resolution"): return False
        if status == "resolved" and risk.get("resolution", {}).get("verification_status") != "resolved": return False
    elif risk.get("lifecycle") != "active":
        return False
    return ((not query or query in " ".join(str(risk.get(key, "")) for key in ("title", "current_basis", "scenario", "uncertainty")).casefold())
            and (not domain or domain in risk.get("impact_domains", []))
            and (not severity or risk.get("current_severity") == severity)
            and (not likelihood or risk.get("future_likelihood") == likelihood)
            and (not status or status in {"withdrawn", "history", "resolved", "superseded"}
                 or risk.get("status") == status))


def _node_id(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha256(value.encode()).hexdigest()[:20]}"


def load_risk_graph(db, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    # The map is a read-only projection of independently admitted risk reports.
    # Avoid the heavier archive/source-state audit used by the paginated review
    # page so opening this secondary visualization does not block the list.
    risks = read_risks(db, unlimited=True, include_source_evidence=True)["risks"]
    for risk in risks:
        risk["lifecycle"] = "active"
        risk["source_status"] = "reviewed_at_analysis"
        risk["priority"] = priority_score(risk)
        risk["status"] = risk["priority"]["status"]
    risks = [risk for risk in risks if _risk_matches(risk, params)]
    risks.sort(key=lambda item: (item.get("priority", {}).get("range", [0, 0])[0], item.get("id", "")), reverse=True)
    requested = max(1, min(2000, int(_one(params, "risk_limit", "48"))))
    risks = risks[:requested]

    # Build the GraphRAG slice from only the workflow runs represented by the
    # selected risks. Calling the global graph builder here would repeatedly
    # scan the whole corpus while the collector is updating its revision token.
    workflow_rows = {row["id"]: row for row in _table_rows(db, "strategic_workflow_runs")}
    final_rows = {row["run_id"]: _json(row.get("payload_json")) for row in _table_rows(db, "strategic_workflow_artifacts")
                  if row.get("stage") == "final" and isinstance(_json(row.get("payload_json")), dict)}
    graph_nodes, graph_edges, graph_evidence = [], [], []
    for run_id in {risk.get("workflow_run_id") for risk in risks}:
        payload = final_rows.get(run_id)
        row = workflow_rows.get(run_id)
        if not payload or not row or row.get("status") != "complete" or row.get("error"): continue
        source = _workflow_graph(payload, run_id)
        graph_nodes.extend(source.get("nodes", [])); graph_edges.extend(source.get("edges", [])); graph_evidence.extend(source.get("evidence", []))
    graph_evidence = {item["id"]: item for item in graph_evidence if isinstance(item, dict) and item.get("id")}
    by_url = defaultdict(set)
    by_title = defaultdict(set)
    for evidence_id, item in graph_evidence.items():
        url = canonical_url(item.get("source_url") or item.get("url") or "")
        if url: by_url[url].add(evidence_id)
        title = _norm(item.get("title"))
        if title: by_title[title].add(evidence_id)

    nodes, edges, edge_keys = [], [], set()
    risk_evidence_sets = {}
    matched_graph_evidence = set()
    def add_edge(source, target, relation, score=None, evidence_ids=None, meaning=""):
        key = (source, target, relation)
        if key in edge_keys: return
        edge_keys.add(key)
        edges.append({"source": source, "target": target, "relation": relation,
                      "meaning": meaning, "similarity": score,
                      "evidence_ids": sorted(evidence_ids or [])})

    for risk in risks:
        scenario = bool(_clean(risk.get("scenario")))
        nodes.append({"id": risk["id"], "label": risk["title"], "name": risk["title"],
                      "type": "ConditionalScenario" if scenario else "RiskAssessment",
                      "kind": "risk", "severity": risk.get("current_severity"),
                      "likelihood": risk.get("future_likelihood"), "status": risk.get("status"),
                      "day": str(risk.get("analysis_at") or "")[:10],
                      "days": sorted({str(day)[:10] for evidence in (risk.get("evidence") or []) for day in (evidence.get("days") or [evidence.get("day")]) if day}),
                      "workflow_run_id": risk.get("workflow_run_id"),
                      "summary": _clean(risk.get("scenario") or risk.get("current_basis"))[:280]})
        refs = set()
        for raw in risk.get("raw_evidence") or risk.get("evidence", []):
            url = canonical_url(raw.get("url") or raw.get("source_url") or "")
            title = _clean(raw.get("title"))
            matched = set(by_url.get(url, ())) | set(by_title.get(_norm(title), ()))
            evidence_key = url or _norm(title) or _clean(raw.get("id"))
            evidence_id = _node_id("risk-evidence", evidence_key)
            refs.add(evidence_id)
            if not any(item["id"] == evidence_id for item in nodes):
                nodes.append({"id": evidence_id, "label": title or url or "검토 근거", "name": title or url or "검토 근거",
                              "type": "SourceDocument", "kind": "evidence", "source_url": url,
                              "day": raw.get("day") or raw.get("published_at") or "",
                              "summary": _clean(raw.get("text") or raw.get("excerpt"))[:280]})
            add_edge(risk["id"], evidence_id, "검토 근거", 1.0, matched, "위험·시나리오 평가가 인용한 검토 근거")
            matched_graph_evidence.update(matched)
        risk_evidence_sets[risk["id"]] = refs

    # Risk-to-risk similarity is only shared reviewed source material, never causality.
    risk_ids = list(risk_evidence_sets)
    for index, left in enumerate(risk_ids):
        for right in risk_ids[index + 1:]:
            shared = risk_evidence_sets[left] & risk_evidence_sets[right]
            if shared:
                score = round(len(shared) / max(1, len(risk_evidence_sets[left] | risk_evidence_sets[right])), 3)
                add_edge(left, right, "공유 검토 근거 · 유사성", score, shared,
                         "동일한 검토 근거를 공유하는 검색 유사성; 인과관계 아님")

    selected_graph_nodes = {item["id"]: item for item in graph_nodes
                            if isinstance(item, dict) and set(item.get("evidence_ids", [])) & matched_graph_evidence}
    for item in selected_graph_nodes.values():
        node_id = "graphrag:" + item["id"]
        nodes.append({"id": node_id, "label": item.get("name") or item["id"], "name": item.get("name") or item["id"],
                      "type": item.get("type") or "Concept", "kind": "graphrag", "summary": item.get("summary", "")})
        refs = set(item.get("evidence_ids", [])) & matched_graph_evidence
        for risk in risks:
            relevant = set()
            for raw in risk.get("raw_evidence") or risk.get("evidence", []):
                url = canonical_url(raw.get("url") or raw.get("source_url") or "")
                title = _norm(raw.get("title"))
                relevant |= by_url.get(url, set()) | by_title.get(title, set())
            shared = refs & relevant
            if shared:
                add_edge(risk["id"], node_id, "GraphRAG 근거 연결", 1.0, shared,
                         "위험 평가 근거와 GraphRAG 노드가 같은 문서에 연결됨")
    for edge in graph_edges:
        if not isinstance(edge, dict): continue
        source, target = "graphrag:" + str(edge.get("source")), "graphrag:" + str(edge.get("target"))
        if source in selected_graph_nodes and target in selected_graph_nodes:
            refs = set(edge.get("evidence_ids", [])) & matched_graph_evidence
            add_edge(source, target, edge.get("relation") or "GraphRAG 관계", None, refs,
                     edge.get("meaning") or "검토된 GraphRAG 관계")

    visible = {item["id"] for item in nodes}
    edge_limit = max(1, min(12000, int(_one(params, "edge_limit", "400"))))
    edges = [edge for edge in edges if edge["source"] in visible and edge["target"] in visible][:edge_limit]
    # A larger node means stronger review priority plus more support from
    # neighbouring, similarly evidenced risks. This is a graph weight, not a
    # probability or a prediction of harm.
    risk_nodes = {item["id"]: item for item in nodes if item["kind"] == "risk"}
    similar_by_risk = defaultdict(list)
    for edge in edges:
        if edge["relation"] != "공유 검토 근거 · 유사성":
            continue
        similar_by_risk[edge["source"]].append(edge)
        similar_by_risk[edge["target"]].append(edge)
    for risk_id, item in risk_nodes.items():
        source = next((risk for risk in risks if risk["id"] == risk_id), {})
        priority_range = (source.get("priority") or {}).get("range") or [0, 0]
        priority_base = min(1.0, max(0.0, float(priority_range[0]) / 100))
        neighbours = similar_by_risk.get(risk_id, [])
        shared_score = min(1.0, sum(float(edge.get("similarity") or 0) for edge in neighbours) / max(1, len(neighbours)))
        centrality = min(1.0, len(neighbours) / max(1, len(risk_nodes) - 1))
        weight = round(100 * (0.60 * priority_base + 0.25 * shared_score + 0.15 * centrality), 1)
        item.update({"weight": weight, "similar_risk_count": len(neighbours),
                     "shared_evidence_count": sum(len(edge.get("evidence_ids", [])) for edge in neighbours),
                     "weight_explanation": "검토 우선순위 60% + 공유 근거 유사성 25% + 유사 위험 연결 수 15%; 확률 아님"})
    kind_counts = defaultdict(int)
    for item in nodes: kind_counts[item["kind"]] += 1
    return {"nodes": nodes, "edges": edges,
            "coverage": {"risks": len(risks), "source_documents": kind_counts["evidence"],
                          "graphrag_nodes": kind_counts["graphrag"], "relationships": len(edges),
                          "shared_evidence_relationships": sum(edge["relation"] == "공유 검토 근거 · 유사성" for edge in edges)},
            "method": {"name": "검토 근거 기반 GraphRAG 관계 지도",
                       "similarity": "동일한 검토 문서·원문 URL을 공유하는 정도의 검색 유사성입니다.",
                       "causality": "위험·조건부 시나리오와 다른 노드 사이의 선은 인과관계·발생 예측·실제 도입을 뜻하지 않습니다."},
            "warnings": ["검토 통과 위험·조건부 시나리오와 현재 GraphRAG에서 동일 근거로 연결되는 노드만 표시했습니다."]}
