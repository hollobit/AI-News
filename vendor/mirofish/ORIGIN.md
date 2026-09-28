# MiroFish attribution

This directory contains code copied from [MiroFish](https://github.com/666ghj/MiroFish),
licensed under the GNU Affero General Public License version 3.

- Upstream commit: `39d849138ef254f6c737ab4c4705e5545dbe31d4`
- Copied source: `backend/app/utils/ontology.py`
- Local path: `vendor/mirofish/ontology.py`
- License: `vendor/mirofish/LICENSE`

`ontology.py` is retained verbatim. The local `knowledge_graph.py` module imports
`normalize_ontology_source_targets` and adapts the ontology validation workflow to
the existing Telegram evidence model. It does not copy or run MiroFish's Zep-backed
graph service, frontend, or credential configuration.

