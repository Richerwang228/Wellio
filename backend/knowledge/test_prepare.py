import hashlib
import json
import unittest
from pathlib import Path

from bs4 import BeautifulSoup
from prepare import spans, table_markdown
from jats import article_metadata, article_text


class PreparationTests(unittest.TestCase):
    def test_jats_preserves_conditions_tables_captions_and_citations(self):
        raw = '''<article><front><article-meta><title-group><article-title>Sleep study</article-title></title-group>
        <contrib-group><contrib contrib-type="author"><name><surname>Chen</surname><given-names>Li</given-names></name></contrib></contrib-group>
        <article-id pub-id-type="doi">10.example/study</article-id><pub-date pub-type="epub"><year>2022</year></pub-date>
        <permissions><license>CC BY 4.0</license></permissions><abstract><p>Healthy adults only.</p></abstract></article-meta></front>
        <body><sec><title>Results</title><p>No significant difference <xref ref-type="bibr">1</xref>.</p>
        <sec><title>Conditions</title><p>Not a recommendation for children.</p></sec>
        <table-wrap><label>Table 1</label><caption><p>Dose and comparison.</p></caption><table><tr><th>Dose</th><th>Outcome</th></tr><tr><td>400 mg</td><td>Reduced sleep</td></tr></table><table-wrap-foot><fn><p>Small sample.</p></fn></table-wrap-foot></table-wrap>
        <fig><label>Figure 1</label><caption><p>Study timeline.</p></caption><graphic/></fig></sec></body>
        <back><ref-list><title>References</title><ref><label>1</label><mixed-citation>Original trial.</mixed-citation></ref></ref-list></back></article>'''
        text = article_text(raw)
        for expected in ['Healthy adults only.', 'No significant difference', '### Conditions',
                         'Not a recommendation for children.', '| 400 mg | Reduced sleep |',
                         'Small sample.', 'Study timeline.', 'Original trial.']:
            self.assertIn(expected, text)
        meta = article_metadata(raw)
        self.assertEqual(meta['author_reported'], ['Li Chen'])
        self.assertEqual(meta['identifiers_reported']['doi'], '10.example/study')
        self.assertEqual(meta['jats_inventory']['table'], 1)
        self.assertEqual(meta['license_reported'][0]['text'], 'CC BY 4.0')

    def test_jats_rejects_abstract_only_or_block_page(self):
        for raw in ['<html><body>Verify you are human</body></html>',
                    '<article><front><article-meta><article-title>A</article-title></article-meta></front></article>']:
            with self.assertRaisesRegex(ValueError, 'JATS_FULL_ARTICLE_REQUIRED'):
                article_text(raw)

    def test_table_keeps_false_true_labels_and_rowspan_context(self):
        table = BeautifulSoup('<table><tr><td rowspan="2">Myth 1</td><td>False</td><td>Claim</td></tr><tr><td>True</td><td>Correction</td></tr></table>', 'html.parser').table
        result = table_markdown(table)
        self.assertIn('| Myth 1 | False | Claim |', result)
        self.assertIn('| Myth 1 | True | Correction |', result)

    def test_chunks_keep_table_atomic_and_exact_source_offsets(self):
        text = '# Title\n\nIntro paragraph.\n\n## Evidence\n\n' + '| Food | Amount |\n' * 100 + '\n\nLast condition.'
        result = list(spans(text, limit=100))
        self.assertTrue(any(text[a:b].count('| Food | Amount |') == 100 for a, b, _ in result))
        self.assertTrue(all(text[a:b].strip() == text[a:b] for a, b, _ in result))

    def test_corpus_hashes_offsets_and_null_embeddings(self):
        root = Path(__file__).resolve().parents[1] / '.data/knowledge/nutrition-v1'
        if not (root / 'documents.jsonl').exists():
            self.skipTest('Local corpus is not distributed with source code')
        docs = {d['id']: d for d in map(json.loads, (root / 'documents.jsonl').read_text().splitlines())}
        chunks = list(map(json.loads, (root / 'chunks.jsonl').read_text().splitlines()))
        self.assertEqual(len(chunks), len({c['id'] for c in chunks}))
        for d in docs.values():
            self.assertEqual(d['document_version'], hashlib.sha256(d['text'].encode()).hexdigest())
            self.assertEqual(d['raw_sha256'], hashlib.sha256((root / d['raw_file']).read_bytes()).hexdigest())
            self.assertEqual(d['text'], (root / d['clean_file']).read_text())
            self.assertEqual(d['publication_status'], 'staging')
            self.assertTrue(any(c['document_id'] == d['id'] for c in chunks))
        for c in chunks:
            d = docs[c['document_id']]
            self.assertEqual(c['text'], d['text'][c['char_start']:c['char_end']])
            self.assertEqual(c['document_version'], d['document_version'])
            self.assertEqual(c['text_sha256'], hashlib.sha256(c['text'].encode()).hexdigest())
            self.assertIsNone(c['embedding'])


if __name__ == '__main__':
    unittest.main()
