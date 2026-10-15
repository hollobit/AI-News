import os
import pytest

from app import load_local_env
import system_one


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.setattr(os, 'environ', os.environ.copy())


def test_local_settings_enable_jev_shadow_without_disclosing_key(tmp_path, monkeypatch, capsys):
    for key in ('TYPESAFE_API_KEY', 'NEWS_SYSTEM_ONE_MODE', 'NEWS_SYSTEM_ONE_MODEL', 'NEWS_QUESTION_PLAN_MODE'):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / '.env'
    env.write_text('TYPESAFE_API_KEY=local-test-secret\n'
                   'NEWS_SYSTEM_ONE_MODE=shadow\nNEWS_SYSTEM_ONE_MODEL=jev-1.14.0\n')
    load_local_env(env)
    settings = system_one.config()
    assert settings['configured'] and settings['enabled']
    assert settings['mode'] == 'shadow' and settings['model'] == 'jev-1.14.0'
    assert settings['applies_question_plans'] is True
    assert settings['applies_workflow_decisions'] is False
    assert settings['applies_evidence_selection'] is False
    assert 'local-test-secret' not in str(settings)
    assert capsys.readouterr() == ('', '')


def test_local_settings_can_disable_only_question_plans(tmp_path,monkeypatch):
    for key in ('TYPESAFE_API_KEY','NEWS_SYSTEM_ONE_MODE','NEWS_QUESTION_PLAN_MODE'):
        monkeypatch.delenv(key,raising=False)
    env=tmp_path/'.env'
    env.write_text('TYPESAFE_API_KEY=test-only\nNEWS_SYSTEM_ONE_MODE=shadow\nNEWS_QUESTION_PLAN_MODE=off\n')
    load_local_env(env)
    assert system_one.config()['enabled']
    assert not system_one.config()['applies_question_plans']


def test_environment_override_can_disable_local_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'inherited-test-secret')
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODE', 'off')
    env = tmp_path / '.env'
    env.write_text('TYPESAFE_API_KEY=local-test-secret\nNEWS_SYSTEM_ONE_MODE=shadow\n')
    load_local_env(env)
    assert os.environ['TYPESAFE_API_KEY'] == 'inherited-test-secret'
    assert system_one.config()['enabled'] is False


def test_local_env_remains_literal_and_allowlisted(tmp_path, monkeypatch):
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    monkeypatch.delenv('UNSUPPORTED_NEWS_TEST_SETTING', raising=False)
    marker = tmp_path / 'must-not-exist'
    literal = f'$(touch {marker})'
    env = tmp_path / '.env'
    env.write_text(f'TYPESAFE_API_KEY={literal}\nUNSUPPORTED_NEWS_TEST_SETTING=ignored\n')
    load_local_env(env)
    assert os.environ['TYPESAFE_API_KEY'] == literal
    assert 'UNSUPPORTED_NEWS_TEST_SETTING' not in os.environ
    assert not marker.exists()
