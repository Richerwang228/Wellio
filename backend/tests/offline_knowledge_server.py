"""Explicit offline HTTP smoke factory; production never imports this module."""
import os
from wellio.app import create_app
from wellio.errors import BackendError


class FixtureKnowledge:
    version = 'test-release-1'
    empty = False
    failure = False
    calls = 0

    def current_version(self):
        return self.version

    async def search(self, query, top_k=4, topic=None):
        self.calls += 1
        if self.failure:
            raise BackendError('EMBEDDING_UNAVAILABLE', 503)
        return {'status': 'no_results' if self.empty else 'ok', 'knowledgeVersion': self.version,
                'results': [] if self.empty else [{'chunkId': 'sleep-chunk-1', 'documentId': 'sleep-paper',
                    'title': 'Sleep and performance', 'sourceUrl': 'https://example.org/sleep', 'publisher': 'Test institution',
                    'text': 'Sleep loss can impair exercise performance; individual outcomes vary.',
                    'population': 'healthy_adults', 'limitations': ['Not an individual risk score.'], 'headingPath': ['Conclusions']}]}

    async def close(self):
        pass


def application():
    return create_app(os.environ['DATABASE_URL'], attachments_path=os.environ['WELLIO_ATTACHMENTS_PATH'],
                      agent_token=os.environ['WELLIO_AGENT_TOKEN'], agent_enabled=True,
                      public_origins=(os.environ['WELLIO_PUBLIC_ORIGIN'],), knowledge_service=FixtureKnowledge())
