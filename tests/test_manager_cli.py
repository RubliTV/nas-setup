import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nas_setup import cli
from nas_setup.manager_auth import ManagerAccount


@pytest.fixture
def terminal(tmp_path,monkeypatch):
    directory=tmp_path/'web';config=tmp_path/'config.json'
    config.write_text(json.dumps({'origin':'https://192.168.1.10:8443'}))
    real_path=Path
    def path(value):
        if value=='/var/lib/nas-setup/web':return directory
        if value=='/etc/nas-setup/config.json':return config
        return real_path(value)
    monkeypatch.setattr(cli,'Path',path)
    monkeypatch.setattr(cli.os,'geteuid',lambda:0)
    import pwd
    monkeypatch.setattr(pwd,'getpwnam',lambda _:SimpleNamespace(pw_uid=123,pw_gid=456))
    calls=[]
    def run(argv,**kwargs):
        calls.append((argv,kwargs))
        if argv[0]=='systemctl':return SimpleNamespace(stdout='',returncode=0)
        if 'input' in kwargs:
            assert kwargs['user']==123 and kwargs['group']==456 and kwargs['extra_groups']==[]
            payload=json.loads(kwargs['input'])
            ManagerAccount(directory).create(payload['username'],payload['password'],replace=payload['replace'])
            return SimpleNamespace(stdout='',returncode=0)
        from nas_setup.auth import issue_code
        return SimpleNamespace(stdout=issue_code(directory),returncode=0)
    monkeypatch.setattr(cli.subprocess,'run',run)
    return directory,calls


def invoke(monkeypatch,*args):
    monkeypatch.setattr(cli.sys,'argv',['nas-setup',*args]);cli.main()


def test_create_account_reads_tty_and_passes_secret_only_via_private_pipe(terminal,monkeypatch,capsys):
    directory,calls=terminal
    class TTY(io.StringIO):
        def isatty(self):return True
    tty=TTY('admin\n')
    def open_tty(name,*args,**kwargs):
        assert name=='/dev/tty';return tty
    monkeypatch.setattr(cli,'open',open_tty,raising=False)
    import getpass
    monkeypatch.setattr(getpass,'getpass',lambda *args,**kwargs:'A long test password')
    invoke(monkeypatch,'manager-account')
    assert ManagerAccount(directory).verify('admin','A long test password')
    assert 'A long test password' not in capsys.readouterr().out
    assert all('A long test password' not in repr(argv) for argv,_ in calls)


def test_manager_account_requires_root_before_prompt(terminal,monkeypatch):
    directory,_=terminal;monkeypatch.setattr(cli.os,'geteuid',lambda:1000)
    with pytest.raises(SystemExit):invoke(monkeypatch,'manager-account')
    assert not ManagerAccount(directory).exists()


def test_manager_account_requires_real_tty_without_stdin_fallback(terminal,monkeypatch):
    directory,_=terminal
    def fail(*args,**kwargs):raise OSError('no controlling terminal')
    monkeypatch.setattr(cli,'open',fail,raising=False)
    monkeypatch.setattr(cli.sys,'stdin',io.StringIO('admin\npassword\npassword\n'))
    with pytest.raises(SystemExit):invoke(monkeypatch,'manager-account')
    assert not ManagerAccount(directory).exists()


def test_existing_account_is_preserved_and_reopen_uses_password_login(terminal,monkeypatch,capsys):
    directory,calls=terminal;ManagerAccount(directory).create('admin','A long test password')
    invoke(monkeypatch,'manager-account','--if-missing')
    invoke(monkeypatch,'reopen')
    assert len(calls)==1 and calls[0][0][0]=='systemctl'
    assert not (directory/'access.json').exists()
    assert 'https://192.168.1.10:8443' in capsys.readouterr().out
    assert ManagerAccount(directory).verify('admin','A long test password')


def test_explicit_recovery_still_issues_code(terminal,monkeypatch,capsys):
    directory,_=terminal;ManagerAccount(directory).create('admin','A long test password')
    invoke(monkeypatch,'reopen','--pairing')
    assert (directory/'access.json').exists()
    assert 'Code (15 min)' in capsys.readouterr().out


def test_reset_invalidates_password_and_uses_explicit_flag(terminal,monkeypatch):
    directory,_=terminal;ManagerAccount(directory).create('admin','A long test password')
    class TTY(io.StringIO):
        def isatty(self):return True
    monkeypatch.setattr(cli,'open',lambda *a,**k:TTY('newadmin\n'),raising=False)
    import getpass
    monkeypatch.setattr(getpass,'getpass',lambda *a,**k:'Replacement password')
    invoke(monkeypatch,'manager-account','--reset')
    assert not ManagerAccount(directory).verify('admin','A long test password')
    assert ManagerAccount(directory).verify('newadmin','Replacement password')


def test_mismatched_confirmation_never_writes(terminal,monkeypatch):
    directory,_=terminal
    class TTY(io.StringIO):
        def isatty(self):return True
    monkeypatch.setattr(cli,'open',lambda *a,**k:TTY('admin\n'),raising=False)
    import getpass
    answers=iter(['A long test password','Different password'])
    monkeypatch.setattr(getpass,'getpass',lambda *a,**k:next(answers))
    with pytest.raises(SystemExit):invoke(monkeypatch,'manager-account')
    assert not ManagerAccount(directory).exists()


def test_manager_account_accepts_a_nonseekable_terminal(terminal,monkeypatch):
    import os
    import pty
    import signal
    import builtins
    import getpass
    directory,_=terminal
    previous=signal.signal(signal.SIGHUP,signal.SIG_IGN)
    master,slave=pty.openpty()
    try:
        os.write(master,b'admin\n')
        monkeypatch.setattr(cli,'open',lambda name,*a,**k:builtins.open(os.ttyname(slave),*a,**k),raising=False)
        monkeypatch.setattr(getpass,'getpass',lambda *a,**k:'A long test password')
        invoke(monkeypatch,'manager-account')
        assert ManagerAccount(directory).verify('admin','A long test password')
    finally:
        os.close(master);os.close(slave)
        signal.signal(signal.SIGHUP,previous)
