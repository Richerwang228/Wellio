import os
from .app import create_app
from .search import ExaSearchService
from .knowledge import KnowledgeService, KnowledgeStore, OpenRouterEmbeddings


def agent_is_configured(environment):
    # The Node runtime uses a fixed OpenRouter endpoint and a default model ID.
    key = environment.get('OPENROUTER_API_KEY', '').strip()
    return bool(environment.get('WELLIO_AGENT_TOKEN', '').strip() and key) and not any(char in key for char in '\r\n')


def application():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError('DATABASE_URL_REQUIRED: configure a PostgreSQL connection URL')
    return create_app(
        database_url=database_url,
        agent_token=os.environ.get('WELLIO_AGENT_TOKEN'),
        agent_enabled=agent_is_configured(os.environ),
        search_service=ExaSearchService(api_key=os.environ.get('EXA_API_KEY', '')),
        knowledge_service=KnowledgeService(KnowledgeStore(database_url), OpenRouterEmbeddings()),
        attachments_path=os.environ.get('WELLIO_ATTACHMENTS_PATH') or None,
        public_origins=tuple(filter(None, os.environ.get('WELLIO_PUBLIC_ORIGIN', '').split(','))),
        cookie_secure=None if 'WELLIO_COOKIE_SECURE' not in os.environ else os.environ['WELLIO_COOKIE_SECURE'] == '1',
    )
