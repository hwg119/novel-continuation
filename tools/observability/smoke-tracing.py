"""Send a synthetic planning trace. No external LLM or novel files are used."""
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.continuation import ContinuationLLM
from core.observability import flush
from core.plan_run_log import PlanRunLogger
from core.planning_workflow import run_planning_workflow


class SampleModel(ContinuationLLM):
    def _ollama_chat(self, prompt, system, temperature, num_predict, on_chunk=None):
        if '分类证据目录' in prompt:
            value = {'issues':[], 'uncertainties':[]}
        else:
            value = {'title':'新的选择', 'beats':[{
                'num':'一','name':'出发','pov':'人物甲','time':'次日','location':'路口',
                'known_before':['旧路已封闭'],'new_facts':['发现可绕行的路'],
                'new_elements':[], 'desc':'人物甲选择绕行，与同行者商量承担额外时间。',
                'end_state':'两人出发，旅途尚未结束。'}]}
        return json.dumps(value, ensure_ascii=False)


def main():
    os.environ['NOVEL_TRACE_ENABLED'] = '1'
    os.environ['NOVEL_TRACE_PROJECT'] = 'novel-tracing-smoke'
    ident = 'trace_smoke_' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='novel-tracing-smoke-') as root:
        model = SampleModel({'interface_format':'ollama','model_name':'synthetic-no-api'})
        audit = PlanRunLogger(root, ident, 'synthetic-no-api', 2)
        result = run_planning_workflow(root, ident, model, lambda *_:None, audit,
            initial={'settings':{'chapter_number':2}, 'chapter_number':2,
                     'num_beats':1, 'previous_context':'前章旧路封闭，两人在路口停下。',
                     'wiki_history':''})
        flush(timeout_ms=3000)
        print(json.dumps({'job':ident, 'title':result['chapter_title'],
                          'project':'novel-tracing-smoke'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
