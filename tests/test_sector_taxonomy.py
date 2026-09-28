from sector_taxonomy import classify_sectors, filter_sector
import pytest

def ids(item):return {s['id'] for s in classify_sectors(item)}

def test_multiple_sectors_with_source_evidence_and_ax_boundaries():
    assert ids({'text':'AI 전환으로 임상 의료 모델을 도입하고 환자 안전과 사이버보안을 확인한다'})=={'ax','medical','technology','safety','security'}
    assert 'ax' not in ids({'text':'FAX AX210 hardware'})
    assert 'ax' not in ids({'text':'AX series'})
    assert 'ax' in ids({'text':'기업 AX와 AI 도입'})

def test_do_not_classify_from_url_or_failed_or_generated_analysis():
    row={'text':'뉴스 https://example.com/healthcare','source_context':{'status':'failed','text':'의료'},'strategic_analysis':{'summary':'의료'}}
    assert ids(row)==set()
    row['source_context']['status']='fetched'
    assert ids(row)=={'medical'}

def test_security_and_safety_do_not_infer_each_other_or_national_security():
    assert ids({'text':'national security'})==set()
    assert ids({'text':'AI safety'})=={'safety'}
    assert ids({'text':'prompt injection'})=={'security'}
    with pytest.raises(ValueError):filter_sector([], 'unknown')
