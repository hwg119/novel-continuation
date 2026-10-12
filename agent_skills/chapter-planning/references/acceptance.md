# 重写验收

对照初审问题与原稿，核对重写是否实质解决问题；只改名称或措辞不算解决。检查修补所牵动的时间、位置、信息权限、因果和跨幕状态，以及是否仍有整幕重复。不能以编造历史解释来宣布通过。

对每个带 issue_id 的初审问题输出 checked_issues：[{"issue_id":"原编号","resolved":true,"change":"具体哪幕改变了什么，如何解决原问题"}]。重复问题要说明新增变化，因果问题要说明新增关联依据或如何收回过强结果；换形容词不算解决。resolved=false 的项目同时进入 unresolved，保留同一 issue_id；不能漏项后宣布通过。没有编号的旧记录按原方式验收。

同时验收本次原始创作要求，输出 requirement_checks：[ {"requirement":"本次要求中的连续短原句","met":true,"evidence":"具体幕的事件、选择或后果如何落实","plan_quote":"重写稿中的连续短原句","suggestion":"未落实时的调整方向"} ]。覆盖实际目标，不以初审问题解决代替目标达成；增强波澜/推进不能仅靠限定措辞或解释。没有本次要求输出 []。met=false 必须说明补修方向，程序会记为未解决目标。无需重复列入 unresolved。

场景内容在结尾状态中被概括不是剧情重复；不得因此要求加入额外事件。

文字规范问题只列 editorial_notes，不判剧情失败。允许日常习惯、回顾、情绪延续与正常创作；没有提及不是从未发生。具体日期依据前章结束时段而不是前章开头。

只输出 JSON：{"passed":true,"checked_issues":[{"issue_id":"初审原编号","resolved":true,"change":"具体变化及其解决作用"}],"unresolved":[{"issue_id":"初审原编号","scope":"幕","problem":"未解决问题","suggestion":"最小修补"}],"new_issues":[{"scope":"幕","problem":"修补引入的剧情问题","suggestion":"最小修补"}],"editorial_notes":[{"scope":"幕","problem":"文字提醒","suggestion":"规范方式"}]}。
只有 unresolved 或 new_issues 非空才判 passed=false；不要另起与修补无关的全面审稿。
