import json
import stat

import pytest

from nas_setup.auth import Auth, issue_code
from nas_setup.models import SetupError


def account(directory):
    from nas_setup import manager_auth
    return manager_auth.ManagerAccount(directory)


def test_password_persists_only_as_salted_private_hash(tmp_path):
    first=account(tmp_path/'one'); second=account(tmp_path/'two')
    assert not first.exists()
    for store in (first,second):store.create('admin','A long test password')
    assert first.exists()
    assert account(tmp_path/'one').verify('admin','A long test password')
    assert not first.verify('other','A long test password')
    assert not first.verify('admin','wrong password')
    first_file=next((tmp_path/'one').iterdir()); second_file=next((tmp_path/'two').iterdir())
    assert 'A long test password' not in first_file.read_text()
    assert first_file.read_text()!=second_file.read_text()
    assert stat.S_IMODE(first_file.stat().st_mode)==0o600
    assert stat.S_IMODE(first_file.parent.stat().st_mode)==0o700
    with pytest.raises(SetupError,match='manager_account_exists'):first.create('other','Another password')


@pytest.mark.parametrize('username,password',[('', 'valid password'),('../admin','valid password'),('a'*65,'valid password'),('admin','short'),('admin','x'*129)])
def test_invalid_credentials_never_create_account(tmp_path,username,password):
    with pytest.raises(SetupError,match='manager_credentials_invalid'):account(tmp_path).create(username,password)
    assert not account(tmp_path).exists()


def test_login_logout_reset_and_expiry(tmp_path,monkeypatch):
    import nas_setup.auth as auth_module
    store=account(tmp_path);store.create('admin','Original password')
    auth=Auth(tmp_path)
    token,csrf=auth.login_password('admin','Original password','lan')
    assert auth.session(token)['csrf']==csrf
    assert auth.session(token)['username']=='admin'
    auth.logout(token)
    with pytest.raises(SetupError,match='login_required'):auth.session(token)
    token,_=auth.login_password('admin','Original password','lan')
    # A separate root CLI process can reset the account without restarting web.
    account(tmp_path).create('newadmin','Replacement password',replace=True)
    with pytest.raises(SetupError,match='login_required'):auth.session(token)
    assert not store.verify('admin','Original password')
    token,_=auth.login_password('newadmin','Replacement password','lan')
    now=auth_module.time.time();monkeypatch.setattr(auth_module.time,'time',lambda:now+3601)
    with pytest.raises(SetupError,match='login_required'):auth.session(token)


def test_password_failures_share_pairing_rate_limit_and_recover(tmp_path,monkeypatch):
    import nas_setup.auth as auth_module
    store=account(tmp_path);store.create('admin','A long test password')
    auth=Auth(tmp_path)
    for _ in range(5):
        with pytest.raises(SetupError,match='login_invalid'):auth.login_password('admin','wrong','peer')
    with pytest.raises(SetupError,match='rate_limited'):auth.login_password('admin','A long test password','peer')
    with pytest.raises(SetupError,match='rate_limited'):auth.login('bad','peer')
    token,_=auth.login_password('admin','A long test password','other-peer')
    assert auth.session(token)['username']=='admin'
    now=auth_module.time.time();monkeypatch.setattr(auth_module.time,'time',lambda:now+61)
    assert auth.login_password('admin','A long test password','peer')


def test_pairing_enrollment_is_invalidated_when_account_created(tmp_path):
    code=issue_code(tmp_path);auth=Auth(tmp_path)
    token,_=auth.login(code,'lan')
    assert auth.session(token).get('username') is None
    account(tmp_path).create('admin','A long test password')
    with pytest.raises(SetupError,match='login_required'):auth.session(token)
    token,_=auth.login_password('admin','A long test password','lan')
    assert auth.session(token)['username']=='admin'
    issue_code(tmp_path)
    with pytest.raises(SetupError,match='login_required'):auth.session(token)


def test_pending_recovery_code_does_not_block_password_login(tmp_path):
    account(tmp_path).create('admin','A long test password')
    code=issue_code(tmp_path);auth=Auth(tmp_path)
    token,_=auth.login_password('admin','A long test password','lan')
    assert auth.session(token)['username']=='admin'
    with pytest.raises(SetupError,match='login_invalid'):auth.login(code,'lan')


def test_damaged_account_fails_closed(tmp_path):
    store=account(tmp_path);store.create('admin','A long test password')
    next(tmp_path.iterdir()).write_text('{"username":"admin"}')
    assert store.exists()
    assert not store.verify('admin','A long test password')
    with pytest.raises(SetupError,match='login_invalid'):Auth(tmp_path).login_password('admin','A long test password','lan')


def test_password_login_after_recovery_invalidates_all_older_sessions(tmp_path):
    account(tmp_path).create('admin','A long test password')
    auth=Auth(tmp_path);old,_=auth.login_password('admin','A long test password','lan')
    issue_code(tmp_path)
    fresh,_=auth.login_password('admin','A long test password','lan')
    assert auth.session(fresh)['username']=='admin'
    with pytest.raises(SetupError,match='login_required'):auth.session(old)


def test_local_reset_revokes_an_outstanding_recovery_code(tmp_path):
    store=account(tmp_path);store.create('admin','A long test password')
    code=issue_code(tmp_path);auth=Auth(tmp_path)
    store.create('admin','Replacement password',replace=True)
    with pytest.raises(SetupError,match='login_invalid'):auth.login(code,'lan')


def test_fifth_successful_pairing_allows_immediate_enrollment_login(tmp_path):
    code=issue_code(tmp_path);auth=Auth(tmp_path)
    for _ in range(4):
        with pytest.raises(SetupError,match='login_invalid'):auth.login('incorrect-code','lan')
    token,_=auth.login(code,'lan')
    assert auth.session(token)['username'] is None
    account(tmp_path).create('admin','A long test password')
    token,_=auth.login_password('admin','A long test password','lan')
    assert auth.session(token)['username']=='admin'


def test_successful_password_login_resets_consecutive_bad_attempts(tmp_path):
    account(tmp_path).create('admin','A long test password');auth=Auth(tmp_path)
    for _ in range(4):
        with pytest.raises(SetupError,match='login_invalid'):auth.login_password('admin','incorrect','lan')
    token,_=auth.login_password('admin','A long test password','lan')
    assert auth.session(token)['username']=='admin'
    for _ in range(5):
        with pytest.raises(SetupError,match='login_invalid'):auth.login_password('admin','incorrect','lan')
    with pytest.raises(SetupError,match='rate_limited'):auth.login_password('admin','A long test password','lan')
