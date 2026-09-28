from database import open_db
import document_features as features
from sector_taxonomy import classify_sectors
from strategic_value import evaluate_news


def test_persistent_features_reuse_exact_inputs_and_invalidate_source_or_rules(tmp_path,monkeypatch):
    item={'title':'의료 AI','text':'병원 진단 연구','source_context':{'status':'fetched','text':'정부 의료 사업'}}
    prepared=dict(item,sectors=classify_sectors(item),strategic_value=evaluate_news(item))
    with open_db(tmp_path/'news.db') as db:
        changes=db.total_changes
        features.persist(db,[prepared])
        assert db.total_changes==changes
        with features.cached_features(db) as store:
            assert features.read(store,item)=={'sectors':prepared['sectors'],'strategic_value':prepared['strategic_value']}
            assert features.read(store,dict(item,text='Changed')) is None
            assert features.read(store,dict(item,abstract='New medical evidence')) is None
            assert features.read(store,dict(item,source_context={'status':'fetched','text':'New evidence'})) is None
            monkeypatch.setattr(features,'rule_version',lambda:'next-rule')
            assert features.read(store,item) is None
