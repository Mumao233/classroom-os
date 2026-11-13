"""Real CA validation + Chromium HTTPS/WSS in an owned isolated Linux network."""
import json,os,subprocess
from pathlib import Path


def test_real_Chromium_login_and_WSS_revocation():
    root=Path(__file__).resolve().parents[4]
    driver=root/'tools/pr03b/product/capacity_linux.py'
    linux='/mnt/j/class-assistant/worktrees/web-pr03b-sessions/'
    command=['wsl.exe','-d','Ubuntu','-u','root','--exec','unshare','--net','--mount','--fork',
        linux+'work/pr03b/product-completion-20261110-01/linux-python312/bin/python3','-I','-B',
        linux+'tools/pr03b/product/capacity_linux.py','browser']
    assert driver.is_file(),'prepare the documented isolated browser driver first'
    run=subprocess.run(command,capture_output=True,text=True,encoding='utf8',timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    rows=[json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')]
    ready=next((row for row in rows if row.get('ready') is True),None)
    assert ready is not None,dict(exit_code=run.returncode,records=rows)
    output=root/'outputs/pr03b/product-completion-20261110-01'/('capacity-'+ready['run'])
    outcome=json.loads((output/'FINAL.json').read_text())
    browser=json.loads((output/'BROWSER.json').read_text())
    assert run.returncode==0 and browser['passed'] and outcome['client_exit_code']==0
    assert 'TLS_invalid_hostname_rejected' in browser['steps'] and 'HTTPS_CA_chain_validated' in browser['steps']
    assert 'real_WSS_revocation_after_logout' in browser['steps']
    assert 'wrong_password_then_correct_login_without_reload' in browser['steps']
    assert 'expired_session_clears_UI_and_returns_to_login' in browser['steps']
    assert 'second_tab_logout_then_sensitive_403_restores_login_without_replay' in browser['steps']
    assert all(outcome['cgroups_empty'].values()) and outcome['ram_unmounted']
