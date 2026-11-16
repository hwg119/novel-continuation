"""Deep Agents dialogue worker. JSON-lines IPC; only host tools access novels."""
import json
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Literal
RPC_LOCK=threading.Lock()
HARNESS_REGISTERED=False
MODEL_CALL_BUDGET=8


def model_request_options(cfg):
    """Provider-compatible options for the tool-calling dialogue model."""
    name=str(cfg.get('model_name') or '').lower()
    mode=str(cfg.get('thinking_mode') or 'auto').lower()
    if mode=='provider_default':
        extra={}
    elif name.startswith(('qwen3.5-','qwen3.6-','qwen3.7-','qwen3.8-','deepseek-v4')):
        extra={'enable_thinking':False}
    else:
        # Auto mode deliberately tries the common OpenAI-compatible control
        # for unknown models too. Explicit unsupported-parameter responses
        # are retried without it by run(), so new models do not require a
        # name allowlist and strict providers keep working.
        extra={'thinking':{'type':'disabled'}}
    configured=int(cfg.get('max_tokens') or 2400)
    return extra, min(max(configured,1600),4096)


def unsupported_thinking_parameter(exc):
    message=str(exc or '').lower()
    parameter=any(marker in message for marker in ('thinking','enable_thinking','reasoning'))
    unsupported=any(marker in message for marker in (
        'unknown','unrecognized','unsupported','not supported','invalid parameter',
        'extra fields','extra_forbidden','unexpected keyword'))
    return parameter and unsupported


def transient_connection_error(exc):
    """Retry transport failures, never provider validation or tool errors."""
    message=str(exc or '').lower()
    return any(marker in message for marker in (
        'connection error','connection reset','connection aborted',
        'remote protocol error','server disconnected','timed out','timeout'))


def friendly_model_error(exc):
    """Keep provider details, but explain tool-call capability failures plainly."""
    message=str(exc or '').strip()
    lowered=message.lower()
    if 'recursion limit' in lowered or 'graph_recursion_limit' in lowered:
        return ('对话代理连续调用工具后仍未收束。本轮没有自动执行新的任务；'
                '请重试，或把要求缩小为一个明确操作。')
    tool_markers=('tool calling','tool_call','tool call','tools are not supported',
                  'does not support tools','does not support tool','unsupported tools',
                  'function calling','function_call')
    if any(marker in lowered for marker in tool_markers):
        return ('当前模型或接口未能完成工具调用。对话创作需要模型支持 OpenAI 兼容的 '
                'tools/tool_calls；普通问答可能可用，但分幕、审校和修改等操作无法执行。'
                f' 服务返回：{message}')
    return message or '模型调用失败'


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
    global HARNESS_REGISTERED
    if not HARNESS_REGISTERED:
        register_harness_profile('openai',HarnessProfile(
            excluded_tools={'ls','read_file','write_file','edit_file','glob','grep'},
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))
        HARNESS_REGISTERED=True

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

    @tool(return_direct=True)
    def propose_action(tool_name:Literal['plan','plan_revision','generate','consistency','revise','illustration','blueprint','view','navigate'], chapter:int, requirements:str='', target_artifact_id:str|None=None,
                       selection_reason:str='', source_request_turn:str|None=None,
                       revision_scope:Literal['local','structural','regenerate']='local',
                       generation_mode:Literal['new','regenerate']='new',
                       blueprint_mode:Literal['create','adjust','regenerate']|None=None,
                       navigation:list[str]|None=None) -> dict:
        """提出操作而非直接执行。plan=从零构建；plan_revision=修改已有分幕，其中 revision_scope=local 是局部修补，structural 是保留正文事实但放弃旧幕结构，regenerate 是完全重新规划；generate=创作正文，首次续写用 generation_mode=new，已有正文但用户明确要求完全重新写、从零重写或不沿用旧稿时必须用 generation_mode=regenerate，不能改用 revise；revise=以某份现有正文为基底修改；consistency=审校保存正文；illustration=生成插图；blueprint=故事蓝图，必须指定 blueprint_mode；view=查看正文；navigate=页面导航。修改已有分幕选 plan_revision 并给 plan 对象 ID；修改正文选 revise 并给 text 对象 ID。用户明确要求执行才调用，讨论不要调用。"""
        result=call('propose',{'tool':tool_name,'chapter':chapter,'requirements':requirements,
            'target_artifact_id':target_artifact_id,'artifact_selection_reason':selection_reason,
            'source_request_turn':source_request_turn,'revision_scope':revision_scope,
            'generation_mode':generation_mode,
            'blueprint_mode':blueprint_mode,
            'navigation':navigation or []})
        if isinstance(result,dict) and result.get('error'):
            raise ValueError(str(result['error']))
        return result

    return create_deep_agent(model=model,tools=[load_skill,read_conversation_turn,list_artifacts,read_artifact,read_evidence,propose_action],
        system_prompt=system_prompt + '\n你是对话创作助手。用工具按需了解对象和证据，自主决定下一步。'
        '讨论时直接自然语言回答；执行时调用 propose_action。工具失败可调整参数，不要宣称操作已完成。'
        'available_skills 提供可选专业方法。判断当前任务所需的技能，通过 load_skill 按需加载后再处理；'
        '专业创作评估使用对应方法，不以项目导航规则代替审查。普通交流不必加载技能。'
        '旧助手结论只是待核对意见，不是证据；重新评估既不要求赞同旧结论，也不要求找出新问题。'
        '结合完整对话判断承接关系和实际目标，requirements 必须是自包含、具体的业务操作要求，'
        '包含用户确认采用的前文建议与补充限制。用户的确认、评价和版本选择不是创作内容指令；'
        '整理分幕生成或结构重写要求时，不得自行添加“至少/恰好/不少于几幕”等数量限制；只有用户当前明确要求固定幕数时才能保留。'
        '不得同时写入互相冲突的条件，例如“至少两次目击”与“不得第二次目击”；发现冲突时优先按用户最新确认的目标消解，无法判断才询问。'
        'requirements 应优先描述必须达到的剧情结果，不为每项结果指定一场独立执行流程，多项结果允许在同一自然场景中交织完成。'
        '修改已有分幕时判断范围：具体某幕、措辞、时间或局部因果用 local；用户认为节奏、故事方向、长期推进或整章结构有问题并要求重写时用 structural；明确要求完全另起方案时用 regenerate。'
        '蓝图操作必须区分：无现有蓝图时用 create；沿用现有结构并局部修改用 adjust；只保留正文事实和锁定项、重新设计其余结构用 regenerate。已有蓝图时，用户仅说“生成/建立蓝图”而没有说明沿用还是重做，必须先自然语言询问，不能擅自提交操作。'
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
    managed_model=model is None
    if managed_model:
        fmt=str(cfg.get('interface_format') or '').lower()
        extra,max_tokens=model_request_options(cfg)
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
        from llm_adapters import check_base_url
        # Do not infer capability from a provider label. Ollama and other local
        # servers can expose a valid OpenAI-compatible tools endpoint; let the
        # actual request establish support and report the provider error if not.
        api_key=cfg.get('api_key') or ('ollama' if fmt=='ollama' else None)
        def configured_model(body):
            return ChatOpenAI(model=cfg['model_name'],api_key=api_key,
                base_url=check_base_url(cfg.get('base_url') or '') or None,
                temperature=0.1,max_tokens=max_tokens,timeout=min(int(cfg.get('timeout') or 180),180),
                max_retries=0,extra_body=body or None,use_responses_api=False,
                stream_usage=False)
        model=configured_model(extra)
    class Logs(BaseCallbackHandler):
        calls=0
        responses=0
        def on_chat_model_start(self,serialized,messages,**kwargs):
            self.calls+=1
            # 一个工具回合至少需要“决定调用 + 接收结果 + 给出答复”三次模型
            # 调用。旧上限 6 会在最后一次工具调用后直接切断，无法收束回复。
            if self.calls>MODEL_CALL_BUDGET:
                raise RuntimeError('Deep Agents 模型调用预算已达上限')
            send({'type':'progress','text':'Deep Agents 正在判断下一步',
                  'details':{'event':'model_start','call':self.calls,
                    'messages':[[{'type':m.type,'content':m.content} for m in batch] for batch in messages]}})
        def on_llm_end(self,response,**kwargs):
            self.responses+=1
            send({'type':'progress','text':'Deep Agents 模型返回',
                  'details':{'event':'model_response','call':self.calls,'output':response.dict()}})
    logger=Logs()
    logger.raise_error=True
    budget_guidance=(f'本轮最多允许 {MODEL_CALL_BUDGET} 次模型调用。查阅资料时优先最小范围证据；'
                     '第 6 次调用后不得再发起新的查阅或技能加载，应基于已有资料调用操作或给出正式答复。')
    agent=build_agent(model,request['system_prompt']+'\n'+budget_guidance,call or rpc)
    # A tool round consumes both a model and a tool graph step.  Keep a small
    # margin for the final response while the independent model-call budget
    # remains the real hard stop.  Successful proposals terminate directly.
    invocation={'recursion_limit':MODEL_CALL_BUDGET*2+2,'callbacks':[logger]}
    try:
        result=agent.invoke({'messages':[{'role':'user','content':json.dumps(request['context'],ensure_ascii=False)+
            '\n【本次用户消息】\n'+request['text']}]},config=invocation)
    except Exception as exc:
        if managed_model and extra and unsupported_thinking_parameter(exc):
            send({'type':'progress','text':'当前接口不支持思考控制参数，已采用接口默认模式重试',
                  'details':{'event':'thinking_control_fallback','parameters':list(extra)}})
            agent=build_agent(configured_model({}),request['system_prompt']+'\n'+budget_guidance,call or rpc)
            try:
                result=agent.invoke({'messages':[{'role':'user','content':json.dumps(request['context'],ensure_ascii=False)+
                    '\n【本次用户消息】\n'+request['text']}]},config=invocation)
            except Exception as retry_exc:
                raise ValueError(friendly_model_error(retry_exc)) from retry_exc
        elif managed_model and logger.responses == 0 and transient_connection_error(exc):
            # Nothing has been returned and no tool can have run yet, so replaying
            # the first turn is safe. Never replay a later turn after tool effects.
            retry_exc = exc
            for retry_attempt in (2, 3):
                send({'type':'progress','text':'模型连接中断，正在重试首次请求',
                      'details':{'event':'connection_retry','attempt':retry_attempt}})
                if retry_attempt > 2:
                    time.sleep(1.0)
                agent=build_agent(configured_model(extra),
                                  request['system_prompt']+'\n'+budget_guidance, call or rpc)
                try:
                    result=agent.invoke({'messages':[{'role':'user','content':
                        json.dumps(request['context'],ensure_ascii=False)+
                        '\n【本次用户消息】\n'+request['text']}]},config=invocation)
                    break
                except Exception as caught:
                    retry_exc = caught
            else:
                raise ValueError(friendly_model_error(retry_exc)) from retry_exc
        else:
            raise ValueError(friendly_model_error(exc)) from exc
    message=result['messages'][-1]
    from tools.deepagents.reply_display import public_reply
    from tools.deepagents.reply_contract import unfulfilled_action_promise
    if getattr(message,'name','')=='propose_action':
        reply='已生成操作卡片，请确认后执行。'
    else:
        reply=public_reply(message.content)
    if unfulfilled_action_promise(reply):
        # Give the model one chance to honor the operation contract. This keeps
        # intent resolution with the model instead of hard-coding chapter rules.
        try:
            result=agent.invoke({'messages':result['messages']+[{'role':'user','content':
                '系统校验：上一条最终答复承诺即将执行操作，但没有调用 propose_action。'
                '请根据本次用户要求现在调用该工具；若不能执行，请明确说明缺少什么，不要让用户等待。'}]},
                config=invocation)
        except Exception as exc:
            raise ValueError(friendly_model_error(exc)) from exc
        message=result['messages'][-1]
        reply=public_reply(message.content)
    if not reply:
        # Some OpenAI-compatible reasoning models can still ignore the
        # disable-thinking flag or finish at the token limit inside <think>.
        # Preserve model-led intent resolution, but ask once for the missing
        # public/tool response instead of surfacing a misleading empty reply.
        try:
            result=agent.invoke({'messages':result['messages']+[{'role':'user','content':
                '系统校验：上一轮只返回了内部思考或在正式答复前被截断。'
                '不要继续分析，不要输出 <think>。若用户要求执行，请立即调用唯一必要的工具；'
                '否则直接在唯一的 <final>...</final> 中给出简洁中文答复。'}]},config=invocation)
        except Exception as exc:
            raise ValueError(friendly_model_error(exc)) from exc
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
