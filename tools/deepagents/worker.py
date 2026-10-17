"""Deep Agents dialogue worker. JSON-lines IPC; only host tools access novels."""
import json
import sys
import threading
import uuid
from pathlib import Path
from typing import Literal
RPC_LOCK=threading.Lock()


def send(value):
    print(json.dumps(value,ensure_ascii=False,default=str),flush=True)


def rpc(name, arguments):
    # ToolNode may execute multiple native tool calls concurrently. Serialize
    # only the IPC request/response pair so replies cannot cross callers.
    with RPC_LOCK:
        ident=uuid.uuid4().hex
        send({'type':'tool_call','id':ident,'name':name,'arguments':arguments})
        response=json.loads(sys.stdin.readline())
        if response.get('id')!=ident: raise ValueError('工具响应编号不匹配')
        return response.get('result')


def build_agent(model, system_prompt, call, callback=None):
    from deepagents import create_deep_agent, register_harness_profile, HarnessProfile, GeneralPurposeSubagentProfile
    from langchain_core.tools import tool
    # Trial exposes project-scoped tools, not a host filesystem or subagents.
    register_harness_profile('openai',HarnessProfile(
        excluded_tools={'ls','read_file','write_file','edit_file','glob','grep'},
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))

    @tool
    def load_skill(name:str, reference:str|None=None) -> dict:
        """按任务加载 available_skills 目录中的专业规则。分幕讨论评估可用 chapter-planning/discussion；其他任务选择相应模式。只读取规则，不执行创作或写文件。"""
        return call('skill',{'name':name,'reference':reference})

    @tool
    def read_conversation_turn(turn_id:str,offset:int=0) -> dict:
        """按需读取当前会话某轮的用户原话与助手回复，用于承接建议或核对历史意见。每次最多8000字，可翻页；聊天意见不是小说事实，不修改记录。"""
        return call('conversation',{'turn_id':turn_id,'offset':offset})

    @tool
    def list_artifacts(chapter:int, kind:str|None=None) -> dict:
        """列出本对话指定章节的作品对象版本；kind 可为 plan/text/audit/illustration。"""
        return call('read',{'tool':'list_artifacts','chapter':chapter,'kind':kind})

    @tool
    def read_artifact(chapter:int, artifact_id:str, offset:int=0) -> dict:
        """读取具体版本内容。长内容依据 next_offset 翻页，不能把候选当成已保存正文。"""
        return call('read',{'tool':'read_artifact','chapter':chapter,'artifact_id':artifact_id,'offset':offset})

    @tool
    def read_evidence(tool_name:Literal['read_chapter','read_summary','search_history','search_wiki'], chapter:int|None=None, offset:int=0,
                      terms:list[str]|None=None, query:str='') -> dict:
        """按疑问查正文、摘要、历史或 Wiki；tool_name 为 read_chapter/read_summary/search_history/search_wiki。"""
        arguments={'tool':tool_name,'offset':offset}
        if chapter is not None: arguments['chapter']=chapter
        if terms is not None: arguments['terms']=terms
        if query: arguments['query']=query
        return call('read',arguments)

    @tool
    def propose_action(tool_name:Literal['plan','plan_revision','generate','consistency','revise','illustration','view','navigate'], chapter:int, requirements:str='', target_artifact_id:str|None=None,
                       selection_reason:str='', source_request_turn:str|None=None,
                       navigation:list[str]|None=None) -> dict:
        """提出操作而非直接执行。plan=从零构建；plan_revision=修改已有分幕；generate=续写；consistency=审校保存正文；revise=修改正文；illustration=生成插图；view=查看正文；navigate=页面导航。修改已有分幕选 plan_revision 并给 plan 对象 ID；修改正文选 revise 并给 text 对象 ID。用户明确要求执行才调用，讨论不要调用。"""
        return call('propose',{'tool':tool_name,'chapter':chapter,'requirements':requirements,
            'target_artifact_id':target_artifact_id,'artifact_selection_reason':selection_reason,
            'source_request_turn':source_request_turn,'navigation':navigation or []})

    return create_deep_agent(model=model,tools=[load_skill,read_conversation_turn,list_artifacts,read_artifact,read_evidence,propose_action],
        system_prompt=system_prompt + '\n你是对话创作助手。用工具按需了解对象和证据，自主决定下一步。'
        '讨论时直接自然语言回答；执行时调用 propose_action。工具失败可调整参数，不要宣称操作已完成。'
        'available_skills 提供可选专业方法。判断当前任务所需的技能，通过 load_skill 按需加载后再处理；'
        '专业创作评估使用对应方法，不以项目导航规则代替审查。普通交流不必加载技能。'
        '旧助手结论只是待核对意见，不是证据；重新评估既不要求赞同旧结论，也不要求找出新问题。'
        '结合完整对话判断承接关系和实际目标，requirements 必须是自包含、具体的业务操作要求，'
        '包含用户确认采用的前文建议与补充限制。用户的确认、评价和版本选择不是创作内容指令；'
        '上下文不足以确定目标时才追问。每次只提出一个操作；提出成功后简短说明已生成操作卡片、等待用户执行。'
        '只要答复表示“现在提交、开始修改、请稍候”等即将执行的含义，就必须在同一轮调用 propose_action；'
        '没有调用工具时只能说明建议或缺少的信息，严禁用将来时伪装成正在后台执行。不要输出路由 JSON。'
        '展示协议：内部分析、英文规划草稿、自我讨论和工具选择推演不要放进回复正文。'
        '最终给用户的答复使用用户语言，放在唯一的 <final>...</final> 中；其中可用 Markdown。'
        '工具调用阶段不输出最终答复。仅报告简洁结论、必要依据和可执行建议，不展示思考过程。',
        subagents=[])


def run(request, model=None, call=None):
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_openai import ChatOpenAI
    cfg=request['config']
    if model is None:
        fmt=str(cfg.get('interface_format') or '').lower()
        if fmt not in ('openai','deepseek'):
            raise ValueError('当前对话引擎需要支持原生工具调用的 OpenAI 兼容或 DeepSeek 配置')
        extra={}
        name=str(cfg.get('model_name') or '').lower()
        if fmt=='deepseek' or name=='deepseek-flash': extra={'thinking':{'type':'disabled'}}
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
        from llm_adapters import check_base_url
        model=ChatOpenAI(model=cfg['model_name'],api_key=cfg.get('api_key'),base_url=check_base_url(cfg.get('base_url') or '') or None,
            temperature=0.1,max_tokens=1600,timeout=min(int(cfg.get('timeout') or 180),180),
            max_retries=0,extra_body=extra or None,use_responses_api=False,
            stream_usage=False)
    class Logs(BaseCallbackHandler):
        calls=0
        def on_chat_model_start(self,serialized,messages,**kwargs):
            self.calls+=1
            if self.calls>6: raise RuntimeError('Deep Agents 模型调用预算已达上限')
            send({'type':'progress','text':'Deep Agents 正在判断下一步',
                  'details':{'event':'model_start','call':self.calls,
                    'messages':[[{'type':m.type,'content':m.content} for m in batch] for batch in messages]}})
        def on_llm_end(self,response,**kwargs):
            send({'type':'progress','text':'Deep Agents 模型返回',
                  'details':{'event':'model_response','call':self.calls,'output':response.dict()}})
    logger=Logs()
    logger.raise_error=True
    agent=build_agent(model,request['system_prompt'],call or rpc)
    invocation={'recursion_limit':16,'callbacks':[logger]}
    result=agent.invoke({'messages':[{'role':'user','content':json.dumps(request['context'],ensure_ascii=False)+
        '\n【本次用户消息】\n'+request['text']}]},config=invocation)
    message=result['messages'][-1]
    from tools.deepagents.reply_display import public_reply
    from tools.deepagents.reply_contract import unfulfilled_action_promise
    reply=public_reply(message.content)
    if unfulfilled_action_promise(reply):
        # Give the model one chance to honor the operation contract. This keeps
        # intent resolution with the model instead of hard-coding chapter rules.
        result=agent.invoke({'messages':result['messages']+[{'role':'user','content':
            '系统校验：上一条最终答复承诺即将执行操作，但没有调用 propose_action。'
            '请根据本次用户要求现在调用该工具；若不能执行，请明确说明缺少什么，不要让用户等待。'}]},
            config=invocation)
        message=result['messages'][-1]
        reply=public_reply(message.content)
    if not reply:
        raise ValueError('模型未返回可展示的正式答复，请重试')
    return {'reply':reply,'model_calls':logger.calls}


if __name__=='__main__':
    try:
        request=json.loads(sys.stdin.readline())
        send({'type':'result',**run(request)})
    except Exception as exc:
        send({'type':'error','message':str(exc)})
        sys.exit(1)
