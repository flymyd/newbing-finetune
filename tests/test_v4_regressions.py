"""Regression checks for SSE channel separation and effective correction writes."""
import json
import runpy
import pytest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
synth = runpy.run_path(str(ROOT / 'scripts/synthesize_v4.py'))
build = runpy.run_path(str(ROOT / 'scripts/build_v4.py'))


class Response:
    status_code = 200
    def iter_lines(self):
        for kind, data in [
            ('response.reasoning_text.delta', {'delta': 'native '}),
            ('response.reasoning_text.delta', {'delta': 'reasoning'}),
            ('response.reasoning_text.done', {'text': 'native reasoning'}),
            ('response.output_text.delta', {'delta': '<think>...</think>'}),
            ('response.completed', {'response': {'usage': {'total_tokens': 7}}}),
        ]:
            yield 'data: ' + json.dumps({'type': kind, **data})


class Client:
    @contextmanager
    def stream(self, *args, **kwargs):
        yield Response()


def test_native_channel_never_regexes_visible_answer(tmp_path):
    think, usage = synth['qwen_stream'](Client(), 'test-secret', 'test prompt', tmp_path/'stream.sse')
    assert think == 'native reasoning'
    assert usage['total_tokens'] == 7
    assert 'test-secret' not in (tmp_path/'stream.sse').read_text()


@pytest.mark.skipif(not (ROOT/"exports/sft-20261106-v3/manifest.json").exists(), reason="local frozen evidence not distributed in Git")
def test_all_audit_corrections_change_returned_samples():
    for loc in json.loads((ROOT/'reports/v3-result-audit-20261106/confirmed-issue-locations.json').read_text()):
        candidates = [s for split in ('train','val','test') for s in json.loads((ROOT/'exports/sft-20261106-v3/sharegpt_all_sft'/f'{split}.json').read_text())]
        old = next(s for s in candidates if s['id'] == loc['id'])
        before = json.loads(json.dumps(old))
        corrected, changes = build['apply_known_corrections'](ROOT, old)
        index = loc['message_index_zero_based']
        assert corrected['conversations'][index]['value'] != old['conversations'][index]['value']
        assert old == before
        assert any(c['index'] == index for c in changes)
