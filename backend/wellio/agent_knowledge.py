"""Run/context-bound evidence receipts. Retrieval is not medical claim validation."""
from copy import deepcopy
import re
from .errors import BackendError


def _positive_clauses(text):
    """Ignore only complete, explicit prohibitions, never an arbitrary suffix.

    'Do not give advice, but recommend...' must retain its positive request.
    These phrases affect retrieval classification only, never mutation authority.
    """
    prohibited = (r'(?:change (?:anything|any records)|modify (?:anything|any records)|record (?:food|meals?)|'
                  r'(?:give|provide) (?:(?:health|medical|nutrition|training|workout) )?advice|'
                  r'make (?:(?:health|medical|nutrition|training|workout) )?recommendations)')
    english = rf"(?:please )?(?:do not|don't|never) {prohibited}(?: (?:or|and) {prohibited})*"
    chinese_action = r'(?:(?:修改|改动|改变)(?:任何)?(?:内容|记录|数据)|记录(?:食物|餐食)|(?:给出|提供)(?:健康|营养|训练|医疗)?(?:建议|推荐))'
    chinese = rf'(?:请)?(?:不要|不用|不必){chinese_action}(?:[或和及]{chinese_action})*'
    return [clause.strip() for clause in re.split(r'[.!?。！？;；\n,，]+', text)
            if clause.strip() and not re.fullmatch(rf'(?:{english}|{chinese})', clause.strip(), re.I)]


def _saved_fact_summary(text):
    english_fact = (r"(?:(?:my|the|today's|current|saved|recorded|scheduled) )*"
                    r'(?:breakfast|lunch|dinner|snack|meals?|workout(?: plan)?|training(?: plan| history)?|'
                    r'readiness(?: score)?|sleep|weight|calories|protein|carbs|fat|intake)'
                    r'(?: (?:today|for today|so far))?')
    chinese_fact = (r'(?:我|今天|今日|的|已记录的|记录的|保存的|安排的|当前)*'
                    r'(?:早餐|午餐|晚餐|加餐|餐食|训练计划|训练|准备度|睡眠|体重|热量|蛋白质|碳水|脂肪|摄入)')
    patterns = (rf'(?:(?:please|briefly) ){{0,2}}(?:summari[sz]e|recap|describe|show|list) {english_fact}(?: and {english_fact})*',
                rf'(?:请|帮我|简单|简要){{0,3}}(?:概括|总结|汇总|列出){chinese_fact}(?:[和及与、]{chinese_fact})*')
    return any(re.fullmatch(pattern, text, re.I) for pattern in patterns)


def _public_menu_lookup(clauses):
    if not clauses or not re.fullmatch(
            r'(?:please )?(?:search|look up|find|read) (?:the |a )?(?:public |online )?menu (?:of|for|at) [^;。]{1,200}', clauses[0], re.I):
        return False
    if re.search(r'\b(?:and|then|but)\s+(?:give|make|build|create|plan|choose|pick|tell|explain|advise)\b', clauses[0], re.I):
        return False
    # A restaurant/location is data. Any further instructions must be limited
    # to reproducing menu facts and their sources; new advice stays gated.
    source_items = (r'(?:give|show|list)(?: me)? (?:one|two|three|four|five|several|a few|[1-9]) (?:menu )?items '
                    r'(?:supported by (?:the )?(?:retrieved )?sources(?: and their source links)?|with (?:their )?source links)')
    return all(re.fullmatch(source_items, clause, re.I) for clause in clauses[1:])


def knowledge_policy(run):
    """Classify once from verified source, original text and server-derived intent.

    Read-only authorization alone does not imply a factual question: most requests
    for new professional advice are also read-only. Unknown requests stay gated.
    """
    if run['source'] in ('app_open', 'ui_proposal'):
        return {'required': True, 'reason': 'professional_advice'}
    intent = run.get('preparedIntent', {})
    kind = intent.get('kind')
    reason = {'meal': 'meal_receipt', 'undo': 'mutation_receipt', 'progress': 'mutation_receipt',
              'load_confirmation': 'mutation_receipt', 'needs_input': 'clarification'}.get(kind)
    if reason:
        return {'required': False, 'reason': reason}
    request = run['request']
    text = request.get('message', '').strip().rstrip('.。!！?？').strip()
    if re.fullmatch(r'(?:hi|hello|hey|你好|您好|嗨|哈喽|早上好|晚上好|谢谢|多谢|thanks|thank you)', text, re.I):
        return {'required': False, 'reason': 'greeting'}
    text = re.sub(r'^(?:hi|hello|hey|你好|您好)[,，!！:：]\s*', '', text, flags=re.I)
    clauses = _positive_clauses(text)
    text = '; '.join(clauses)
    if _saved_fact_summary(text):
        return {'required': False, 'reason': 'facts'}
    if re.fullmatch(r'(?:我(?:的)?|我今天|今天|今日)?(?:今天|今日)?(?:的)?(?:训练)?计划(?:是啥|是什么|咋样|怎么样|是什么样|有哪些)(?:呢|呀)?', text):
        return {'required': False, 'reason': 'facts'}
    advice = re.search(r'建议|推荐|应该|适合|调整|安排|能不能|够不够|为什么|如何|怎么|增肌|减脂|\b(?:recommend(?:ation)?s?|suggest(?:ion)?s?|should|advice|adjust|why|how to|can I|is it safe)\b', text, re.I)
    if not advice and _public_menu_lookup(clauses):
        return {'required': False, 'reason': 'facts'}
    menu_read = re.fullmatch(r'(?:|(?:请|帮我)?(?:识别|读一下|读取|看看|看一下)(?:这份|这个|图片里的|这张)?菜单|(?:这里|菜单里|这份菜单)(?:有|写了)(?:什么|哪些)(?:菜|内容)?|(?:please )?(?:read|identify|recognize|transcribe|show)(?: this| the)? menu|what (?:does this menu say|is on (?:this|the) menu))', text, re.I)
    if not advice and request.get('purpose') == 'menu' and menu_read:
        return {'required': False, 'reason': 'menu_recognition'}
    # Whole-question patterns avoid treating a factual prefix followed by a new
    # recommendation request as a bypass of the professional evidence policy.
    facts = (r'(?:今天|今日|我的|我今天|当前|已记录的|最近|过去\d+天|近\d+天)*(?:的)?(?:准备度|恢复分数|恢复度|睡眠|体重|训练|训练计划|饮食|餐食|摄入|热量|卡路里|蛋白质|碳水|脂肪|预算|场地|可用时间|训练记录|饮食记录|体重记录)(?:的)?(?:记录|情况|数据|总量|分数|时长|内容)?(?:是|有|为)?(?:多少|什么|哪些|几个|几分|几小时|几分钟)?',
             r'(?:我)?(?:今天|今日)?(?:已经|已|总共)?(?:吃了|摄入了|记录了|完成了|睡了)(?:多少|什么|哪些|几小时|几分钟)(?:热量|卡路里|蛋白质|碳水|脂肪|餐食|训练|动作)?',
             r'(?:what (?:is|are|was|were)|show|list|read|tell me) (?:(?:my|the|today\x27s|current|recorded|latest) )*(?:readiness(?: score)?|recovery(?: score)?|sleep(?: duration)?|weight|workout(?: plan)?|training(?: plan| history)?|meals?|food log|calories|protein|carbs|fat|intake|budget|gym|available time)(?: (?:today|for today|so far|records|history))?',
             r'how (?:many|much) (?:calories|protein|carbs|fat|meals|hours|minutes)(?: (?:have I|did I|I))? (?:eaten|eat|consumed|consume|logged|log|slept|sleep)(?: today| so far)?')
    if not advice and any(re.fullmatch(pattern, text, re.I) for pattern in facts):
        return {'required': False, 'reason': 'facts'}
    return {'required': True, 'reason': 'professional_advice'}


def unavailable_notice(locale, code):
    return {'errorCode': code, 'markdown': (
        '专业参考资料暂时不可用，本次无法继续生成有依据的训练或饮食建议。请稍后重试。'
        if locale == 'zh-CN' else
        'The expert reference material is temporarily unavailable, so I cannot continue with an evidence-based training or nutrition recommendation this time. Please try again later.')}


def evidence_required(db, run, now):
    from .agent_state import assert_run
    current = assert_run(db, run, now)
    if not current.get('knowledgePolicy', {'required': True})['required'] or current.get('knowledgeUnavailable'):
        return False
    receipt = current.get('knowledgeRead')
    return not receipt or receipt['contextReadId'] != current.get('lastContextReadId')


def validate_evidence(db, current, service, read_id, chunk_ids):
    receipt = current.get('knowledgeRead')
    if not receipt or receipt.get('contextReadId') != current.get('lastContextReadId'):
        raise BackendError('KNOWLEDGE_READ_REQUIRED', 409)
    # A successful-looking stale receipt never substitutes for a fresh fact read.
    db.get_context_read(current['sessionId'], receipt['contextReadId'], current['id'], current['resetEpoch'])
    if read_id != receipt['id']:
        raise BackendError('KNOWLEDGE_RECEIPT_MISMATCH', 409)
    if (not isinstance(chunk_ids, list) or not 1 <= len(chunk_ids) <= 4
        or any(not isinstance(id, str) for id in chunk_ids) or len(set(chunk_ids)) != len(chunk_ids)):
        raise BackendError('KNOWLEDGE_CITATIONS_REQUIRED', 409)
    found = {c['chunkId']:c for c in receipt['results']}
    if not all(id in found for id in chunk_ids):
        raise BackendError('KNOWLEDGE_CITATION_INVALID', 409)
    if service.current_version() != receipt['knowledgeVersion']:
        raise BackendError('KNOWLEDGE_VERSION_STALE', 409)
    return [deepcopy(found[id]) for id in chunk_ids]


def cited_markdown(markdown, sources, locale):
    def label(value):
        return str(value).replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]').replace('\n',' ')
    unique = {s['documentId']:s for s in sources}
    links = ['[' + label(s['title']) + '](<' + s['sourceUrl'].replace('>', '%3E').replace('<', '%3C') + '>)' for s in unique.values()]
    return markdown.rstrip() + '\n\n' + ('参考依据：' if locale == 'zh-CN' else 'Sources: ') + ' · '.join(links)
