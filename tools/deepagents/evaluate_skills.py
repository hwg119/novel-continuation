"""Live read-only dialogue evaluation using synthetic fiction, never a real book."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from core.config_manager import load_config,get_llm_config
from core.project_manager import save_project_settings
from web.chat_store import ChatStore
from web.chat_artifacts import ChatArtifacts
from web.deepagents_trial import run_trial


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='确认调用配置中的模型，只发送合成测试资料')
    parser.add_argument('--model-config',default='deepseek-flash')
    args=parser.parse_args()
    if not args.live: parser.error('需要 --live 明确启用模型测试')
    config=get_llm_config(load_config(str(ROOT/'config.json')),args.model_config)
    with tempfile.TemporaryDirectory(prefix='dialogue_skill_') as directory:
        path=Path(directory)
        beats=[
            {'num':1,'name':'旧信','desc':'早晨孩子的来信只写同学名字青禾，没有写姓氏。妻子暂未动笔回信。'},
            {'num':2,'name':'告知','desc':'当天傍晚妻子写好回信，翻出旧信，其中记载一户姓青禾的人家。她当面告诉丈夫，确认孩子同学也姓青禾。'},
            {'num':3,'name':'计划','desc':'丈夫把青禾与仓库工号写在同一张便笺上，因此确认两者有关。他此时不知道碎纸片存在，却决定明天比对那张碎纸片。'},
            {'num':4,'name':'发现','desc':'深夜丈夫首次偶然发现并取得碎纸片，夹进便笺，决定明天比对。'}]
        save_project_settings(directory,{'chapter_number':2,'chapter_title':'合成规划','beats':beats})
        store=ChatStore(path/'chat.sqlite3'); session=store.create('synthetic',2)
        backend=ChatArtifacts(store,type('Jobs',(),{'get':lambda *_:None})(),directory,session)
        previous='早晨她收到孩子来信，孩子只写同学的名字青禾，没写姓氏；她想着回信怎么开头，还没动笔。'
        context={**backend.context(2),'current_chapter':2,
                 'previous_chapter':{'chapter':1,'text':previous,'kind':'saved_body_tail','truncated':False},
                 'conversation':[{'role':'assistant','source_state':'previous_opinion_not_evidence',
                    'data':{'text':'此前我认为分幕已达标，只需精简动作；可能清晨没写信而傍晚封信算时间冲突。'}}]}
        events=[]
        result=run_trial(config,context,'重新评估当前分幕是否还需要优化。','你是小说创作工作台助手。',
                         backend,lambda text,data:events.append((text,data)),lambda:True)
        used=[data for text,data in events if text=='已加载专业 Skill']
        print(json.dumps({'skills_loaded':used,'proposed_operation':result['tool'],
                          'actions_created':len(store.actions(session)),'reply':result.get('_reply_text') or result.get('message')},
                         ensure_ascii=False,indent=2))
        if not any(row['name']=='chapter-planning' and row['reference']=='discussion' for row in used):
            raise SystemExit('本次未加载分幕讨论 Skill，请检查模型行为与工具日志')
        if result['tool']!='reply' or store.actions(session):
            raise SystemExit('评估不应提出创作操作')


if __name__=='__main__': main()
