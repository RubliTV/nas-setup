from pathlib import Path
import pytest
import yaml
from nas_setup.models import SetupError
from nas_setup.compose_import import parse_compose


def parse(text, tmp_path, variables=None):
    return parse_compose(text, variables or {}, [tmp_path])


def test_variable_questions_and_secret_free_preview(tmp_path):
    text='services:\n  app:\n    image: nginx:1.28\n    environment:\n      PASSWORD: ${PASSWORD}\n    ports: ["${PORT:-8080}:80"]\n'
    result=parse(text,tmp_path)
    assert result['needs']==[{'name':'PASSWORD','secret':True}]
    result=parse(text,tmp_path,{'PASSWORD':'top-secret'})
    assert result['config']['services']['app']['environment']['PASSWORD']=='top-secret'
    assert 'top-secret' not in str(result['summary'])
    assert result['summary']['services'][0]['ports']==[{'host':8080,'container':80,'protocol':'tcp'}]


@pytest.mark.parametrize('body',[
 'privileged: true','network_mode: host','pid: host','build: .','env_file: .env',
 'devices: [/dev/sda]','cap_add: [SYS_ADMIN]','extends: {file: x.yml}',
 'volumes: [/var/run/docker.sock:/var/run/docker.sock]',
 'volumes: [../../etc:/data]','ports: ["8443:80"]',
 'image: nginx:latest\n    image: nginx:1.28',
 'environment: &env {A: x}\n    labels: *env',
])
def test_rejects_host_privileges_ambiguous_or_unsupported_input(tmp_path,body):
    with pytest.raises(SetupError):parse('services:\n  app:\n    image: nginx:1.28\n    '+body+'\n',tmp_path)


def test_mounts_stay_under_existing_volume_and_reject_symlink(tmp_path):
    media=tmp_path/'media';media.mkdir()
    text='services:\n  app:\n    image: nginx:1.28\n    volumes:\n      - '+str(media)+':/data:ro\n'
    result=parse(text,tmp_path)
    assert result['config']['services']['app']['volumes'][0]['bind']['create_host_path'] is False
    media.rmdir();media.symlink_to('/etc')
    with pytest.raises(SetupError,match='unsafe_storage'):parse(text,tmp_path)


def test_named_volumes_dependency_and_unknown_top_keys(tmp_path):
    doc={'services':{'app':{'image':'nginx:1.28','volumes':['data:/data'],'depends_on':['db']},'db':{'image':'postgres:17'}},'volumes':{'data':{}}}
    assert len(parse(yaml.safe_dump(doc),tmp_path)['summary']['services'])==2
    doc['volumes']['data']={'driver_opts':{'device':'/etc','type':'none','o':'bind'}}
    with pytest.raises(SetupError):parse(yaml.safe_dump(doc),tmp_path)


def test_paths_in_volume_allow_hidden_ancestors_but_not_private_child(tmp_path):
    root=tmp_path/'.host'/'data';root.mkdir(parents=True);media=root/'media';media.mkdir()
    text=f'services: {{web: {{image: "nginx:1.28", volumes: ["{media}:/data"]}}}}'
    assert parse_compose(text,{},[root])['config']
    hidden=root/'.nas-manager';hidden.mkdir()
    with pytest.raises(SetupError):parse_compose(text.replace(str(media),str(hidden)),{},[root])


def test_scalar_list_top_level_and_nested_variable_are_explained(tmp_path):
    for value in ['[]','services: []','services: {a: {image: x:1, depends_on: [2]}}','services: {a: {image: "${A:-${B}}"}}']:
        with pytest.raises(SetupError):parse(value,tmp_path)


@pytest.mark.parametrize('fragment',['image: "nginx:${APP_PASSWORD}"','ports: ["${API_TOKEN}:80"]','volumes: ["${SECRET_PATH}:/data"]'])
def test_secret_variables_cannot_be_interpolated_into_public_preview_fields(tmp_path,fragment):
    with pytest.raises(SetupError,match='compose_secret_field'):
        parse('services:\n  web:\n    image: nginx:1.28\n    '+fragment if not fragment.startswith('image:') else 'services:\n  web:\n    '+fragment,tmp_path,{'APP_PASSWORD':'hidden','API_TOKEN':'8080','SECRET_PATH':str(tmp_path)})


def test_variable_expansion_is_bounded_before_preview(tmp_path):
    text='services: {web: {image: "nginx:1.28", environment: {VALUE: "'+('${VALUE}'*100)+'"}}}'
    with pytest.raises(SetupError,match='compose_too_large'):parse(text,tmp_path,{'VALUE':'x'*4096})
