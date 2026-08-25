"""镜像 gold-bot `apps/app-agent/src/utils/logger.test.ts`。"""
import pytest

from backend.agents.utils.logger import get_logger, reset_logger


@pytest.fixture(autouse=True)
def _reset_logger():
    reset_logger()

def test_should_return_a_pino_logger():
    # TS: 'should return a pino logger'
    logger = get_logger()
    assert logger is not None
    assert callable(logger.info)
    assert callable(logger.error)
    assert callable(logger.debug)
    assert callable(logger.warn)


def test_should_return_the_same_instance_on_repeated_calls():
    # TS: 'should return the same instance on repeated calls'
    logger1 = get_logger()
    logger2 = get_logger()
    assert logger1 is logger2


def test_should_respect_log_level_env_var(monkeypatch):
    # TS: 'should respect LOG_LEVEL env var'
    monkeypatch.setenv("LOG_LEVEL", "debug")
    reset_logger()
    logger = get_logger()
    assert logger.level == "debug"
