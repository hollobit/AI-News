"""Explicit service/query dependencies shared by domain route handlers."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable


if TYPE_CHECKING:
    from agent_reach_service import AgentReachService
    from article_explanations import ArticleExplanations
    from arxiv_papers import PaperService
    from baseline_jobs import BaselineJobs
    from corpus_status import CorpusStatus
    from graph_questions import GraphQuestions
    from knowledge_wiki import KnowledgeWiki
    from mirofish_service import MiroFishService
    from observatory_runtime import ObservatoryRuntime
    from paper_analysis import PaperAnalysisService
    from paper_pipeline import PaperPipeline
    from reach_pipeline import ReachPipeline
    from recursive_improvement import RecursiveImprovementService
    from research import ResearchService
    from semantic import AnalysisService
    from source_enrichment import SourceService
    from strategic_hub import StrategicHub
    from strategic_workflow import WorkflowService


@dataclass(frozen=True)
class ServerContext:
    analysis_service: AnalysisService
    article_service: ArticleExplanations
    baseline_service: BaselineJobs
    connect: Callable
    corpus_status: CorpusStatus
    graph_service: AnalysisService
    improvement_catalog: Callable
    improvement_service: RecursiveImprovementService
    intelligence_service: StrategicHub
    observatory_service: ObservatoryRuntime
    paper: Callable
    paper_analysis_service: AnalysisService
    paper_pipeline: PaperPipeline
    paper_service: PaperService
    path: str
    port: int
    public_improvement: Callable
    question_service: GraphQuestions
    reach_pipeline: ReachPipeline
    reach_service: AgentReachService
    read_papers: Callable
    research_service: ResearchService
    simulation_service: MiroFishService
    source_service: SourceService
    wiki_service: KnowledgeWiki
    workflow_service: WorkflowService
    ROOT: Path
    configured_channels: Callable
    find_link_group: Callable
    graph_input: Callable
    hidden_link_rows: Callable
    read_briefing: Callable
    read_links: Callable
    simulation_news: Callable
    source_bundle: Callable
    unindexed_link_rows: Callable
