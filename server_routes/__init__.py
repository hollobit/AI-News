"""Ordered, explicit domain dispatch with transport-independent handlers."""
from . import wiki, articles, sources, operations, intelligence, strategy, papers, risks, simulation, research, news, assets

GET_ROUTES = (wiki.get, articles.get, sources.get, operations.get, intelligence.get, strategy.get, papers.get, risks.get, simulation.get, research.get, news.get, assets.get,)
POST_ROUTES = (wiki.post, articles.post, sources.post, operations.post, intelligence.post, strategy.post, papers.post, simulation.post, research.post, news.post,)
