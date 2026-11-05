from pathlib import Path
import shutil

import pytest

from newbing.core import ROOT, Project


@pytest.fixture
def project(tmp_path):
    shutil.copytree(ROOT/'config',tmp_path/'config')
    shutil.copytree(ROOT/'schemas',tmp_path/'schemas')
    shutil.copytree(ROOT/'prompts',tmp_path/'prompts')
    (tmp_path/'.env.local').write_text('NEWBING_API_KEY=local-test-dummy\n')
    p=Project(tmp_path)
    p.policy['download']['initial_per_host_requests_per_second']=100000
    yield p
    p.db.close()
