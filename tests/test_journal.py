import importlib.util
import pytest


def journal(path):
    assert importlib.util.find_spec('nas_setup.journal'), 'operation journal missing'
    from nas_setup.journal import OperationJournal
    return OperationJournal(path)


def test_completed_destructive_operation_is_not_repeated(tmp_path):
    store=journal(tmp_path);calls=[]
    assert store.perform('format',lambda:calls.append(1) or {'uuid':'abc'},destructive=True)=={'uuid':'abc'}
    assert journal(tmp_path).perform('format',lambda:calls.append(2),destructive=True)=={'uuid':'abc'}
    assert calls==[1]


def test_uncertain_operation_never_reexecutes(tmp_path):
    store=journal(tmp_path);calls=[]
    def fail():calls.append(1);raise RuntimeError('power lost')
    with pytest.raises(RuntimeError):store.perform('format',fail,destructive=True)
    with pytest.raises(Exception,match='recovery_required'):journal(tmp_path).perform('format',lambda:calls.append(2),destructive=True)
    assert calls==[1]


def test_reconcile_requires_positive_evidence(tmp_path):
    store=journal(tmp_path)
    with pytest.raises(RuntimeError):store.perform('format',lambda:(_ for _ in ()).throw(RuntimeError()),destructive=True)
    result=store.perform('format',lambda:pytest.fail('repeated'),lambda record:{'uuid':'found'},destructive=True)
    assert result=={'uuid':'found'}
    with pytest.raises(Exception,match='recovery_required'):store.perform('format',lambda:None,lambda record:None,destructive=True)
