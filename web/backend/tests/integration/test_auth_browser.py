"""Real Chromium over real HTTPS/WSS against the formal app, inside the VMware Windows guest.

The earlier version of this test launched an isolated Linux network through WSL, which the
project retired on 2026-11-12. This version runs natively: the formal ASGI app is served by
uvicorn with TLS on literal loopback, backed by the disposable PostgreSQL of the `auth`
fixture, and driven by Playwright's Chromium.

TLS trust differs from the Linux run and is weaker. Chromium on Windows has no per-profile
trust store and the guest's OS store is deliberately not modified, so the browser allow-lists
exactly this run's server key. Hostname and chain validation are therefore not exercised;
the negative control is a second listener with another key, which Chromium must refuse.
"""
import base64,hashlib,http.server,ipaddress,json,os,shutil,socket,ssl,subprocess,threading,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from test_authentication import auth,PASSWORD

ROOT=Path(__file__).resolve().parents[4]


def _port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def _certificate(folder,name,tag):
    """A run-scoped root and a 127.0.0.1 leaf; returns cert path, key path and the leaf SPKI pin."""
    now=datetime.now(timezone.utc);root_key=ec.generate_private_key(ec.SECP256R1());leaf_key=ec.generate_private_key(ec.SECP256R1())
    issuer=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'PR03b isolated root '+tag)])
    def build(subject,key,ca):
        builder=(x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1)).add_extension(x509.BasicConstraints(ca=ca,path_length=0 if ca else None),critical=True))
        if not ca:
            builder=(builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
                .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),critical=False))
        return builder.sign(root_key,hashes.SHA256())
    root=build(issuer,root_key,True);leaf=build(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'127.0.0.1')]),leaf_key,False)
    cert=folder/(name+'.pem');key=folder/(name+'-key.pem')
    cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM)+root.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    spki=leaf_key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
    return cert,key,base64.b64encode(hashlib.sha256(spki).digest()).decode('ascii')


def test_real_Chromium_login_and_WSS_revocation(auth,tmp_path):
    import uvicorn
    from starlette.staticfiles import StaticFiles
    from classroom_web.application.course_read import CourseReader
    from classroom_web.entrypoints.http import create_app
    a=auth;tag=uuid4().hex[:12]
    dist=ROOT/'work/pr09a/dist';module=ROOT/'web/frontend/node_modules/@playwright/test/index.mjs'
    node=shutil.which('node') or str(ROOT/'work/pr03b/vmware-broker/payload/node/node.exe')
    browsers=os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or str(ROOT/'work/pr03b/vmware-broker/payload/ms-playwright')
    assert (dist/'index.html').is_file(),'build the frontend first (run the frontend checks)'
    assert module.is_file() and Path(node).is_file() and Path(browsers).is_dir(),'prepare node, Playwright and its Chromium first'

    port=_port();origin=f'https://127.0.0.1:{port}'
    cert,key,pin=_certificate(tmp_path,'leaf',tag);other_cert,other_key,other_pin=_certificate(tmp_path,'unpinned',tag);assert pin!=other_pin
    contract=json.loads((ROOT/'docs/web-alpha/architecture/contracts/openapi.json').read_text(encoding='utf8'))
    app=create_app(auth=a.service,contract=contract,origin=origin,websocket_admission=a.ws_admission,business=CourseReader(auth=a.service,database=a.pg.db))
    app.mount('/',StaticFiles(directory=dist,html=True))
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,ssl_certfile=str(cert),ssl_keyfile=str(key),proxy_headers=False,access_log=False,
        log_level='warning',ws='websockets-sansio',ws_max_size=4096,timeout_graceful_shutdown=3))
    thread=threading.Thread(target=server.run,daemon=True)
    # Negative control: same host, a key the browser was not told about.
    unpinned=http.server.ThreadingHTTPServer(('127.0.0.1',0),http.server.BaseHTTPRequestHandler)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(str(other_cert),str(other_key))
    unpinned.socket=tls.wrap_socket(unpinned.socket,server_side=True)
    unpinned_thread=threading.Thread(target=unpinned.serve_forever,daemon=True)
    screenshot=tmp_path/'browser.png'
    config=dict(playwrightModule=str(module),tlsMode='pinned_key',spki=pin,origin=origin,unpinnedOrigin=f'https://127.0.0.1:{unpinned.server_address[1]}',
                email=a.email,password=PASSWORD,retryCredentials=True,screenshot=str(screenshot))
    thread.start();unpinned_thread.start()
    try:
        until=time.monotonic()+15
        while not server.started and thread.is_alive() and time.monotonic()<until:time.sleep(.02)
        assert server.started,'owned HTTPS server did not start'
        environment={**os.environ,'PLAYWRIGHT_BROWSERS_PATH':browsers}
        run=subprocess.run([node,str(ROOT/'tools/pr03b/product/browser_live.mjs')],input=json.dumps(config),capture_output=True,text=True,encoding='utf8',
            timeout=150,env=environment,cwd=str(ROOT/'web/frontend'),creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    finally:
        server.should_exit=True;thread.join(15);unpinned.shutdown();unpinned.server_close();unpinned_thread.join(5)
    rows=[json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')]
    browser=rows[-1] if rows else None
    assert browser is not None,dict(exit_code=run.returncode,stderr=run.stderr[-2000:])
    assert run.returncode==0 and browser['passed'],browser
    steps=browser['steps']
    assert 'TLS_unpinned_key_rejected' in steps and 'HTTPS_served_by_run_certificate' in steps
    # These two are only produced by the isolated-CA mode; this run must not claim them.
    assert 'TLS_invalid_hostname_rejected' not in steps and 'HTTPS_CA_chain_validated' not in steps
    assert 'real_https_authenticated' in steps and 'secure_httponly_nonpersistent_cookie' in steps
    assert 'real_WSS_single_ticket_authentication' in steps and 'real_WSS_revocation_after_logout' in steps
    assert 'wrong_password_then_correct_login_without_reload' in steps
    assert 'expired_session_clears_UI_and_returns_to_login' in steps
    assert 'second_tab_logout_then_sensitive_403_restores_login_without_replay' in steps
    assert browser['secret_values_exported'] is False and browser['external_web_requests_allowed'] is False
    assert screenshot.is_file() and not thread.is_alive()
