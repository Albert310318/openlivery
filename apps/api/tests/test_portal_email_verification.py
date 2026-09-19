"""Focused integration tests in a disposable schema; never reset existing tables."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models import Agency, Client, User, now_utc
from app.security import hash_password, verify_password
from app.services import portal_verification as verification
from app.services.email import EmailDeliveryError


@pytest.fixture(autouse=True)
def clean_database(monkeypatch):
    """Overrides the destructive parent fixture, with our own unique schema."""
    url = os.environ['TEST_DATABASE_URL']
    schema = 'portal_verify_test_' + uuid.uuid4().hex
    admin_engine = create_engine(url)
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(get_settings(), 'rate_limit_enabled', False)
    monkeypatch.setattr(get_settings(), 'allow_multi_agency', True)
    def db_override():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = db_override
    yield factory
    app.dependency_overrides.clear()
    engine.dispose()
    with admin_engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin_engine.dispose()


@pytest.fixture
def setup(clean_database, monkeypatch):
    sent = []
    monkeypatch.setattr(verification, 'send_verification_email', lambda email, code: sent.append((email, code)))
    with TestClient(app) as admin:
        assert admin.post('/api/auth/register', json={'agency_name': 'Agency A', 'name': 'Admin', 'email': 'admin@example.com', 'password': 'admin-password'}).status_code == 201
        client = admin.post('/api/clients', json={'name': 'Alpha'}).json()
        assert admin.patch(f"/api/clients/{client['id']}/portal", json={'portal_enabled': True, 'portal_email': 'alpha@example.com', 'portal_password': 'portal-password'}).status_code == 200
        yield admin, client, sent, clean_database


def login(browser, email='alpha@example.com'):
    return browser.post('/api/auth/unified-login', json={'email': email, 'password': 'portal-password'})


def confirm(browser, code):
    return browser.post('/api/portal/email-verification/confirm', json={'code': code})


def change(factory, client, **values):
    with factory() as db:
        row = db.get(Client, uuid.UUID(client['id']))
        for key, value in values.items():
            setattr(row, key, value)
        db.commit()


def test_correct_consumed_and_verified_access(setup):
    _, client, sent, factory = setup
    with TestClient(app) as browser:
        assert login(browser).json()['status'] == 'verification_required'
        token = browser.cookies.get(verification.COOKIE)
        assert not browser.cookies.get('portal_access_token')
        with factory() as db:
            row = db.get(Client, uuid.UUID(client['id']))
            assert verify_password(sent[-1][1], row.portal_verification_code_hash)
            assert row.portal_verification_code_hash != sent[-1][1]
        assert confirm(browser, sent[-1][1]).status_code == 200
        assert browser.get(f"/api/portal/{client['portal_slug']}/me").status_code == 200
        browser.cookies.set(verification.COOKIE, token, path='/api/portal/email-verification')
        assert confirm(browser, sent[-1][1]).status_code == 401
        assert login(browser).json()['principal_type'] == 'portal'


def test_incorrect_attempt_limit(setup):
    _, client, sent, factory = setup
    wrong = '000000' if sent[-1][1] != '000000' else '000001'
    with TestClient(app) as browser:
        login(browser)
        for _ in range(4):
            assert confirm(browser, wrong).status_code == 400
        assert confirm(browser, wrong).status_code == 429
        assert confirm(browser, sent[-1][1]).status_code == 429
    with factory() as db:
        row = db.get(Client, uuid.UUID(client['id']))
        assert row.portal_verification_attempts == 5
        assert row.portal_email_verified_at is None


def test_expired(setup):
    _, client, sent, factory = setup
    change(factory, client, portal_verification_expires_at=now_utc()-timedelta(seconds=1))
    with TestClient(app) as browser:
        login(browser)
        assert confirm(browser, sent[-1][1]).status_code == 400


def test_resend_invalidates_and_limits(setup):
    _, client, sent, factory = setup
    old = sent[-1][1]
    with TestClient(app) as browser:
        login(browser)
        assert browser.post('/api/portal/email-verification/resend').status_code == 429
        for _ in range(4):
            change(factory, client, portal_verification_last_sent_at=now_utc()-timedelta(seconds=61))
            assert browser.post('/api/portal/email-verification/resend').status_code == 200
        change(factory, client, portal_verification_last_sent_at=now_utc()-timedelta(seconds=61))
        assert browser.post('/api/portal/email-verification/resend').status_code == 429
        assert sent[-1][1] != old
        assert confirm(browser, old).status_code == 400
        assert confirm(browser, sent[-1][1]).status_code == 200


def test_pending_both_logins_and_admin(setup):
    admin, client, _, _ = setup
    with TestClient(app) as browser:
        assert login(browser).json()['status'] == 'verification_required'
        assert browser.get(f"/api/portal/{client['portal_slug']}/me").status_code == 401
        assert browser.post(f"/api/portal/{client['portal_slug']}/login", json={'email': 'alpha@example.com', 'password': 'portal-password'}).json()['status'] == 'verification_required'
        assert not browser.cookies.get('portal_access_token')
    for endpoint in ['login', 'unified-login']:
        assert admin.post('/api/auth/'+endpoint, json={'email': 'admin@example.com', 'password': 'admin-password'}).status_code == 200
    assert admin.get('/api/auth/me').status_code == 200


def test_email_change_revokes_even_after_new_verification(setup):
    admin, client, sent, factory = setup
    with TestClient(app) as browser:
        login(browser); confirm(browser, sent[-1][1])
        old_token = browser.cookies.get('portal_access_token')
        change(factory, client, portal_verification_last_sent_at=now_utc()-timedelta(seconds=61))
        assert admin.patch(f"/api/clients/{client['id']}/portal", json={'portal_email': 'new@example.com'}).status_code == 200
        assert browser.get(f"/api/portal/{client['portal_slug']}/me").status_code == 401
        login(browser, 'new@example.com'); assert confirm(browser, sent[-1][1]).status_code == 200
        with TestClient(app) as old:
            old.cookies.set('portal_access_token', old_token)
            assert old.get(f"/api/portal/{client['portal_slug']}/me").status_code == 401


def test_clients_and_agencies_isolated(setup):
    admin, alpha, sent, factory = setup
    alpha_code = sent[-1][1]
    with TestClient(app) as other_admin:
        other_admin.post('/api/auth/register', json={'agency_name': 'Agency B', 'name': 'Other', 'email': 'other@example.com', 'password': 'admin-password'})
        beta = other_admin.post('/api/clients', json={'name': 'Beta'}).json()
        other_admin.patch(f"/api/clients/{beta['id']}/portal", json={'portal_enabled': True, 'portal_email': 'beta@example.com', 'portal_password': 'portal-password'})
        assert other_admin.patch(f"/api/clients/{alpha['id']}/portal", json={'portal_email': 'stolen@example.com'}).status_code == 404
    with TestClient(app) as browser:
        login(browser, 'beta@example.com')
        assert confirm(browser, alpha_code).status_code == 400
        token = browser.cookies.get(verification.COOKIE)
        payload = jwt.decode(token, get_settings().secret_key, algorithms=['HS256'])
        payload['sub'] = alpha['id']  # Even a signed token with mismatched agency is rejected.
        browser.cookies.clear()
        browser.cookies.set(verification.COOKIE, jwt.encode(payload, get_settings().secret_key, algorithm='HS256'), path='/api/portal/email-verification')
        assert confirm(browser, alpha_code).status_code == 401


def test_smtp_failure_pending_and_retry(setup, monkeypatch):
    admin, client, sent, factory = setup
    change(factory, client, portal_verification_last_sent_at=now_utc()-timedelta(seconds=61))
    def fail(*args):
        raise EmailDeliveryError('unavailable')
    monkeypatch.setattr(verification, 'send_verification_email', fail)
    assert admin.patch(f"/api/clients/{client['id']}/portal", json={'portal_email': 'failed@example.com'}).status_code == 503
    with factory() as db:
        row = db.get(Client, uuid.UUID(client['id']))
        assert row.portal_email == 'failed@example.com'
        assert row.portal_email_verified_at is None and row.portal_verification_code_hash is None
    with TestClient(app) as browser:
        assert login(browser, 'failed@example.com').json()['status'] == 'verification_required'
        change(factory, client, portal_verification_last_sent_at=now_utc()-timedelta(seconds=61))
        monkeypatch.setattr(verification, 'send_verification_email', lambda email, code: sent.append((email, code)))
        assert browser.post('/api/portal/email-verification/resend').status_code == 200
        assert confirm(browser, sent[-1][1]).status_code == 200


def test_simultaneous_confirmation_consumed_once(setup):
    _, client, sent, _ = setup
    with TestClient(app) as browser:
        login(browser)
        token = browser.cookies.get(verification.COOKIE)
    def attempt():
        with TestClient(app) as browser:
            browser.cookies.set(verification.COOKIE, token, path='/api/portal/email-verification')
            return confirm(browser, sent[-1][1]).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == [200, 401]


def test_same_email_keeps_verification_and_no_send(setup):
    admin, client, sent, _ = setup
    with TestClient(app) as browser:
        login(browser); confirm(browser, sent[-1][1])
    result = admin.patch(f"/api/clients/{client['id']}/portal", json={'portal_email': 'ALPHA@example.com', 'portal_title': 'New title'})
    assert result.status_code == 200 and result.json()['portal_email_verified_at']
    assert len(sent) == 1


def test_concurrent_resend_only_one_email(setup):
    _, client, sent, factory = setup
    with TestClient(app) as browser:
        login(browser)
        token = browser.cookies.get(verification.COOKIE)
    change(factory, client, portal_verification_last_sent_at=now_utc()-timedelta(seconds=61))
    def attempt():
        with TestClient(app) as browser:
            browser.cookies.set(verification.COOKIE, token, path='/api/portal/email-verification')
            return browser.post('/api/portal/email-verification/resend').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == [200, 429]
    assert len(sent) == 2


def test_temporary_token_cannot_access_portal_or_admin(setup):
    _, client, _, _ = setup
    with TestClient(app) as browser:
        login(browser)
        token = browser.cookies.get(verification.COOKIE)
        browser.cookies.set('portal_access_token', token)
        browser.cookies.set('access_token', token)
        assert browser.get(f"/api/portal/{client['portal_slug']}/me").status_code == 401
        assert browser.get('/api/auth/me').status_code == 401


def test_email_change_does_not_reset_send_budget(setup):
    admin, client, _, factory = setup
    change(factory, client, portal_verification_send_count=5)
    response = admin.patch(f"/api/clients/{client['id']}/portal", json={'portal_email': 'changed@example.com'})
    assert response.status_code == 429
    with factory() as db:
        row = db.get(Client, uuid.UUID(client['id']))
        assert row.portal_email == 'changed@example.com'
        assert row.portal_verification_send_count == 5
        assert row.portal_email_verified_at is None


def test_smtp_transport_tls_and_failure(monkeypatch):
    from unittest.mock import MagicMock
    from app.services import email
    settings = get_settings()
    for key, value in {'smtp_host': 'smtp.invalid', 'smtp_from_email': 'noreply@example.com', 'smtp_use_tls': True, 'smtp_username': 'test', 'smtp_password': 'test'}.items():
        monkeypatch.setattr(settings, key, value)
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value.send_message.return_value = {}
    monkeypatch.setattr(email.smtplib, 'SMTP', smtp)
    email.send_verification_email('recipient@example.com', '012345')
    connection = smtp.return_value.__enter__.return_value
    connection.starttls.assert_called_once()
    connection.login.assert_called_once_with('test', 'test')
    message = connection.send_message.call_args.args[0]
    assert '012345' in message.get_content()
    connection.send_message.side_effect = OSError('offline')
    with pytest.raises(EmailDeliveryError):
        email.send_verification_email('recipient@example.com', '012345')
