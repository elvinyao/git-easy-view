"""Only used by isolated browser tests; never installed in deployment images."""
from app.demo import DemoProvider
from app.main import create_app

class FixtureQueue:
    def ensure(self, job_id): pass

def application():
    app = create_app()
    app.state.provider_factory = DemoProvider
    app.state.job_queue = FixtureQueue()
    return app
