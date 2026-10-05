# 重写验收

对照初审问题与原稿，核对重写是否实质解决问题；只改名称或措辞不算解决。检查修补所牵动的时间、位置、信息权限、因果和跨幕状态，以及是否仍有整幕重复。不能以编造历史解释来宣布通过。

文字规范问题只列 editorial_notes，不判剧情失败。允许日常习惯、回顾、情绪延续与正常创作；没有提及不是从未发生。具体日期依据前章结束时段而不是前章开头。

只输出 JSON：{"passed":true,"unresolved":[{"scope":"幕","problem":"未解决问题","suggestion":"最小修补"}],"new_issues":[{"scope":"幕","problem":"修补引入的剧情问题","suggestion":"最小修补"}],"editorial_notes":[{"scope":"幕","problem":"文字提醒","suggestion":"规范方式"}]}。
只有 unresolved 或 new_issues 非空才判 passed=false；不要另起与修补无关的全面审稿。
