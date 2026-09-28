import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime

from arxiv_papers import PaperService, discover_papers, init_papers, paper, parse_arxiv_id, parse_atom, read_papers


def atom(identity='2609.12345v3', published='2026-09-10T00:00:00Z'):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>http://arxiv.org/abs/{identity}</id><title>Physical AI for clinical safety</title>
      <summary>Physical AI improves robot monitoring.</summary><published>{published}</published><updated>2026-09-15T00:00:00Z</updated>
      <author><name>First Author</name></author><category term="cs.RO"/><category term="cs.AI"/>
      <arxiv:primary_category term="cs.RO"/><arxiv:doi>10.1234/example</arxiv:doi><arxiv:journal_ref>Example Journal</arxiv:journal_ref></entry></feed>'''.encode()


class ArxivPaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'news.sqlite3'
        self.db = sqlite3.connect(self.path)
        self.db.execute('CREATE TABLE news(chat_id TEXT,message_id INTEGER,text TEXT,title TEXT,day TEXT)')
        init_papers(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def news(self, index, text):
        self.db.execute('INSERT INTO news VALUES (?,?,?,?,?)', ('channel',index,text,'논문 소개','2026-11-20'))
        self.db.commit()

    def store(self, identity, published):
        self.news(identity, 'https://arxiv.org/abs/'+identity)
        discover_papers(self.db)
        metadata = parse_atom(atom(identity+'v2',published), {identity})[identity]
        self.db.execute("UPDATE arxiv_papers SET metadata_json=?,status='fetched' WHERE paper_id=?", (json.dumps(metadata),identity))
        self.db.commit()

    def test_strict_hosts_identifiers_versions_and_legacy_formats(self):
        for url, expected, version in [('https://arxiv.org/pdf/2609.12345v3.pdf','2609.12345',3),
            ('https://export.arxiv.org/abs/hep-th/9901001v2','hep-th/9901001',2),
            ('https://arxiv.org/html/2609.12345v1','2609.12345',1), ('2609.12345','2609.12345',None)]:
            parsed = parse_arxiv_id(url)
            self.assertEqual((parsed['paper_id'],parsed['version']), (expected,version))
        for url in ['https://arxiv.org.evil.test/abs/2609.12345', 'https://evil@arxiv.org/abs/2609.12345',
                    'https://arxiv.org:9999/abs/2609.12345', 'https://arxiv.org/abs/2609.12345/evil',
                    '2613.12345', '2609.12345v0', 'https://arxiv.org/api/query?url=http://127.0.0.1']:
            self.assertIsNone(parse_arxiv_id(url))

    def test_raw_versions_and_hidden_urls_deduplicate_one_paper(self):
        self.news(1, 'https://arxiv.org/abs/2609.12345v1 https://arxiv.org/pdf/2609.12345v2.pdf')
        self.db.execute('CREATE TABLE archived_urls(chat_id TEXT,message_id INTEGER,original_url TEXT,title TEXT,active INTEGER)')
        self.db.execute('INSERT INTO archived_urls VALUES (?,?,?,?,?)', ('channel',2,'https://arxiv.org/html/2609.12345v3','숨은 논문',1))
        self.db.commit()
        self.assertEqual(discover_papers(self.db),1)
        discover_papers(self.db)
        result = paper(self.db,'2609.12345')
        self.assertEqual(result['observed_versions'],[1,2,3])
        self.assertEqual(result['mention_count'],2)
        self.assertEqual(result['metadata_status'],'pending')
        self.assertEqual(result['abstract'],'')
        self.assertEqual(result['title_source'],'telegram_mention')

    def test_atom_metadata_scope_and_xml_entity_rejection(self):
        metadata = parse_atom(atom(), {'2609.12345'})['2609.12345']
        self.assertEqual(metadata['metadata_version'],3)
        self.assertEqual(metadata['authors'],['First Author'])
        self.assertEqual(metadata['primary_category'],'cs.RO')
        self.assertEqual(metadata['doi'],'10.1234/example')
        self.assertEqual(metadata['evidence_scope'],'official_metadata_and_abstract')
        with self.assertRaises(ValueError):
            parse_atom(b'<!DOCTYPE feed [<!ENTITY foo "bad">]>'+atom(), {'2609.12345'})
        self.assertEqual(parse_atom(atom(), {'2501.12345'}),{})

    def test_trends_use_publication_dates_not_telegram_mentions_or_versions(self):
        self.store('2609.10001','2026-09-01T00:00:00Z')
        self.store('2609.10002','2026-09-10T00:00:00Z')
        self.store('2609.10003','2026-09-15T00:00:00Z')
        self.news(99, 'https://arxiv.org/pdf/2609.10003v4.pdf')
        result = read_papers(self.db, {'window':['14']})
        self.assertEqual(result['total'],3)
        self.assertEqual(result['trends']['end'],'2026-09-15')
        self.assertEqual(result['trends']['current_documents'],2)
        self.assertEqual(result['trends']['previous_documents'],1)
        keyword = next(k for k in result['trends']['keywords'] if k['label']=='Physical AI')
        self.assertEqual((keyword['current'],keyword['previous'],keyword['growth_pct']),(2,1,100.0))
        self.assertEqual(len(read_papers(self.db,{'category':['cs.RO'],'page_size':['1']})['items']),1)
        self.assertEqual(read_papers(self.db,{'sector':['medical']})['total'],3)

    def test_metadata_job_caches_success_and_failures_without_fake_abstracts(self):
        self.news(1,'https://arxiv.org/abs/2609.12345')
        calls=[]
        service=PaperService(self.path,fetcher=lambda ids: calls.append(ids) or parse_atom(atom(),set(ids)))
        try:
            first=service.refresh()
            deadline=time.monotonic()+3
            while service.active and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertIsNone(service.active)
            self.assertEqual(service.status()['counts']['fetched'],1)
            self.assertEqual(service.refresh()['status'],'cached')
            self.assertEqual(len(calls),1)
        finally:
            service.close()
        self.news(2,'https://arxiv.org/abs/2609.99999')
        service=PaperService(self.path,fetcher=lambda ids: {})
        try:
            with patch('arxiv_papers.time.sleep'):
                with service.db() as db:
                    discover_papers(db)
                service._run('missing-job',['2609.99999'])
            result=paper(self.db,'2609.99999')
            self.assertEqual(result['metadata_status'],'failed')
            self.assertEqual(result['abstract'],'')
            self.assertTrue(result['metadata_error'])
            self.assertEqual(service.refresh(ids=['2609.99999'])['status'],'cached')
        finally:
            service.close()

    def test_unchanged_discovery_does_not_rewrite_mentions(self):
        self.news(1,'https://arxiv.org/abs/2609.12345')
        discover_papers(self.db)
        changed=self.db.total_changes
        discover_papers(self.db)
        self.assertEqual(self.db.total_changes,changed)

    def test_api_lock_enforces_spacing_between_requests(self):
        self.news(1,'https://arxiv.org/abs/2609.12345')
        discover_papers(self.db)
        service=PaperService(self.path,fetcher=lambda ids: parse_atom(atom(),set(ids)))
        waits=[]
        try:
            with patch('arxiv_papers.time.sleep',side_effect=lambda delay: waits.append(delay)):
                service._run('test1',['2609.12345'])
                service._run('test2',['2609.12345'])
            self.assertGreaterEqual(waits[-1],2.8)
            self.assertLessEqual(waits[-1],3)
        finally:
            service.close()

    def test_rate_limit_blocks_other_ids_and_survives_new_service(self):
        self.news(1,'https://arxiv.org/abs/2609.12345')
        self.news(2,'https://arxiv.org/abs/2609.99999')
        discover_papers(self.db)
        calls=[]
        def limited(ids):
            calls.append(ids)
            raise HTTPError('https://export.arxiv.org/api/query',429,'limited',{'Retry-After':'7200'},None)
        service=PaperService(self.path,fetcher=limited)
        try:
            service._run('limited',['2609.12345'])
            retry=datetime.fromisoformat(service.status()['retry_at'])
            self.assertGreater((retry-datetime.now(timezone.utc)).total_seconds(),7100)
        finally:
            service.close()
        other=PaperService(self.path,fetcher=limited)
        try:
            self.assertEqual(other.refresh(ids=['2609.99999'])['status'],'rate_limited')
            other._run('racing-job',['2609.99999'])
            self.assertEqual(len(calls),1)
            self.assertEqual(paper(self.db,'2609.99999')['metadata_status'],'pending')
        finally:
            other.close()

    def test_retry_after_http_date_is_honored(self):
        self.news(1,'https://arxiv.org/abs/2609.12345')
        discover_papers(self.db)
        retry=datetime.now(timezone.utc)+timedelta(hours=3)
        def unavailable(ids):
            raise HTTPError('https://export.arxiv.org/api/query',503,'busy',{'Retry-After':format_datetime(retry)},None)
        service=PaperService(self.path,fetcher=unavailable)
        try:
            service._run('dated',['2609.12345'])
            self.assertGreaterEqual(datetime.fromisoformat(service.status()['retry_at']),retry-timedelta(seconds=1))
        finally:
            service.close()


if __name__=='__main__':
    unittest.main()
