# 起草

按指定幕数规划，title 只含回目，不含章号。先确定当前已有状态，再安排本幕变化和可供下一幕使用的结束状态。通常聚焦一至两条人物线，需要时可以增加；不重演已经完成的事件，不一次耗尽全部悬念。

只输出完整 JSON：
{"title":"回目","beats":[{"num":"一","name":"幕名","pov":"视角人物","time":"相对时间","location":"地点","known_before":["已知信息"],"new_facts":["本幕新信息"],"new_elements":[{"name":"主要新增元素","type":"类别","source_or_entry":"进入剧情的过程","future_use":"后续用途"}],"desc":"具体事件与人物选择","end_state":"结束状态及未完成线索"}]}

没有主要新增元素则 new_elements 为 []。不输出思考、解释或 Markdown。
