"""Project capability IDs resolve to existing frontend views, never model-provided URLs."""
FEATURES = {
    'project_import': {'view': 'projects', 'label': '打开工程与母本', 'help': '新建工程；导入已有工程目录或 project.json；选择 TXT 母本文件切分导入；更新向量库。'},
    'model_config': {'view': 'config', 'label': '打开全局配置', 'help': '配置大模型、默认模型、向量模型并测试连接。'},
    'chapter_settings': {'view': 'settings', 'label': '打开续写设定', 'help': '全书规则、当前章回目分幕、摘要和 SVG 插图生成。'},
    'novel': {'view': 'write', 'label': '打开小说全文', 'help': '查看正文、阅读模式、书签、语义搜索、英语学习版和 Word 导出。'},
    'story_review': {'view': 'quality', 'label': '打开审校与修订', 'help': '故事审校、导入修订要求、执行修订、查看并应用修订稿。'},
    'revisions': {'view': 'revision', 'label': '查看修订稿', 'help': '章节修订版本与正文高亮差异比较。'},
    'wiki': {'view': 'wiki', 'label': '打开小说 Wiki', 'help': '编纂与增量更新、二次复核、人物档案、原文证据和章节过滤。'},
    'relationships': {'view': 'relationships', 'label': '打开人物关系图', 'help': '人物关系网络，点击关系线查看相关原文记录。'},
    'runs': {'view': 'runs', 'label': '打开运行记录', 'help': '按时间查看后台任务结果与日志。'},
    'home': {'view': 'home', 'label': '打开首页', 'help': '近期章节与开始续写入口。'},
}


def navigation_links(ids, chapter=None):
    if not isinstance(ids, list):
        return []
    number = chapter if type(chapter) is int and chapter > 0 else None
    links, seen = [], set()
    for ident in ids:
        if not isinstance(ident, str) or ident not in FEATURES or ident in seen:
            continue
        seen.add(ident)
        feature = FEATURES[ident]
        links.append({'view': feature['view'], 'label': feature['label'],
                      'chapter': number if feature['view'] in ('write', 'revision', 'quality', 'settings') else None})
        if len(links) == 3:
            break
    return links
