from .errors import BackendError
from .validation import strict_object, require_id, require_int


def parse_chat(value):
    common = {'requestId', 'resetEpoch', 'conversationId', 'locale', 'source', 'message', 'attachmentIds'}
    source = value.get('source') if isinstance(value, dict) else None
    optional = {'purpose', 'targetMealId', 'targetMealItemId', 'targetWorkoutId', 'targetExerciseId', 'targetOperationId'} if source == 'user' else {'checkMode'}
    strict_object(value, common, optional)
    require_id(value['requestId']); require_id(value['conversationId']); require_int(value['resetEpoch'])
    if value['locale'] not in ('en', 'zh-CN') or source not in ('user', 'app_open'):
        raise BackendError('INVALID_INPUT', 400)
    message, attachments = value['message'], value['attachmentIds']
    if not isinstance(message, str) or len(message.encode('utf-16-le')) // 2 > 4000 or not isinstance(attachments, list) or len(attachments) > 4:
        raise BackendError('INVALID_INPUT', 400)
    for item in attachments:
        require_id(item)
    if source == 'user':
        if not message.strip() and not attachments:
            raise BackendError('INVALID_INPUT', 400)
        if 'purpose' in value and value['purpose'] not in ('food', 'menu'):
            raise BackendError('INVALID_INPUT', 400)
        for key in optional - {'purpose'}:
            if key in value:
                require_id(value[key])
    elif message != '' or attachments or ('checkMode' in value and value['checkMode'] not in ('auto', 'retry')):
        raise BackendError('INVALID_INPUT', 400)
    return value
