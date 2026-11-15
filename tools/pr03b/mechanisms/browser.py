"""Real Chromium CDP harness. No certificate/sandbox bypass flags."""
from __future__ import annotations
import json,secrets,subprocess,time
from pathlib import Path
import common as b
class CDPError(b.Stop):
    def __init__(self,method,error):
        self.method=method;self.code=error.get('code')
        super().__init__('CDP_method_failed:'+method)
class CDP:
    def __init__(self,url):
        from websockets.sync.client import connect
        self.ws=connect(url,open_timeout=5,max_size=2*1024*1024);self.seq=0
    def call(self,method,params=None,session=None):
        self.seq+=1;seq=self.seq;message={'id':seq,'method':method,'params':params or {}}
        if session:message['sessionId']=session
        self.ws.send(json.dumps(message))
        end=time.monotonic()+15
        while time.monotonic()<end:
            obj=json.loads(self.ws.recv(timeout=max(.01,end-time.monotonic())))
            if obj.get('id')==seq:
                if 'error' in obj:raise CDPError(method,obj['error'])
                return obj.get('result',{})
        raise b.Stop('CDP_deadline')
    def eval(self,expression,session):
        r=self.call('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True},session)
        b.require('exceptionDetails' not in r,'browser_JS_exception');return r['result'].get('value')
def trust(lab,install):
    # The dedicated guest's noninteractive CurrentUser Root API refuses UI.
    # Administrator installs ONLY this fresh synthetic public CA in LocalMachine;
    # Chromium remains reduced and cannot mutate this store. Exact cleanup below.
    path=str(lab.root/'ca.cer').replace("'","''");thumb=lab.ca_thumb
    if install:
        command=f"$ErrorActionPreference='Stop';$p='{path}';$c=[Security.Cryptography.X509Certificates.X509Certificate2]::new($p);$store='Cert:\\LocalMachine\\Root\\'+$c.Thumbprint;if($c.Thumbprint -cne '{thumb}' -or $c.Subject -notlike 'CN=PR03b S3 Synthetic CA *'){{throw 'CA_mismatch'}};if(Test-Path -LiteralPath $store){{throw 'CA_already_present'}};try{{& 'C:\\Windows\\System32\\certutil.exe' -f -addstore Root $p|Out-Null;if($LASTEXITCODE -ne 0){{throw 'CA_import_exit'}};$v=Get-Item -LiteralPath $store;if([Convert]::ToBase64String($v.RawData) -cne [Convert]::ToBase64String($c.RawData)){{throw 'CA_readback'}};Write-Output 'CA_INSTALLED'}}catch{{if(Test-Path -LiteralPath $store){{Remove-Item -LiteralPath $store -ErrorAction Stop}};throw}}"
    else:
        command=f"$ErrorActionPreference='Stop';$p='Cert:\\LocalMachine\\Root\\{thumb}';$c=Get-Item -LiteralPath $p;if($c.Subject -notlike 'CN=PR03b S3 Synthetic CA *'){{throw 'CA_subject_mismatch'}};Remove-Item -LiteralPath $p -ErrorAction Stop;if(Test-Path -LiteralPath $p){{throw 'CA_remaining'}};Write-Output 'CA_REMOVED'"
    r=subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','-NoProfile','-NonInteractive','-Command',command],capture_output=True,timeout=20)
    b.require(r.returncode==0,'guest_CA_install' if install else 'guest_CA_remove')
def experiments(lab,plan,env,check):
    from restricted_process import RestrictedJob
    chrome=b.ASSETS/'chrome-win64/chrome.exe';profile=plan.run_root/'chromium-profile';b.require(not profile.exists(),'fresh_browser_profile')
    trust(lab,True);job=None;client=None
    try:
        job=RestrictedJob(plan)
        args=[str(chrome),'--headless=new','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-component-update','--disable-sync','--disable-extensions','--disable-breakpad','--disable-crash-reporter',
            '--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--user-data-dir='+str(profile),'--host-resolver-rules=MAP app.test 127.0.0.1, MAP evil.test 127.0.0.1','about:blank']
        identity=job._start(tuple(args),chrome,b.sha(b.read(chrome,64*1024*1024)),env,plan.run_root/'chromium.stdout.log',plan.run_root/'chromium.stderr.log')
        check('Chromium_restricted_primary',job.token.process(identity.pid)['administrative_groups_enabled'] is False)
        active=profile/'DevToolsActivePort';end=time.monotonic()+20
        while not active.exists():b.require(time.monotonic()<end and job.contains(identity.pid),'Chromium_start_deadline');time.sleep(.1)
        lines=b.read(active,4096).decode().splitlines();b.require(len(lines)>=2 and lines[0].isdigit() and lines[1].startswith('/devtools/browser/'),'Chromium_debug_endpoint')
        client=CDP('ws://127.0.0.1:'+lines[0]+lines[1]);version=client.call('Browser.getVersion')
        target=client.call('Target.createTarget',{'url':'about:blank'})['targetId'];session=client.call('Target.attachToTarget',{'targetId':target,'flatten':True})['sessionId']
        client.call('Page.enable',session=session);client.call('Runtime.enable',session=session)
        client.call('Page.navigate',{'url':lab.origin+'/lab/page'},session)
        end=time.monotonic()+10
        while True:
            location=client.eval('location.href',session)
            if location==lab.origin+'/lab/page':break
            b.require(time.monotonic()<end,'browser_TLS_navigation');time.sleep(.1)
        check('Chromium_real_trusted_TLS_no_bypass',client.eval('window.isSecureContext',session) is True)
        expression="""(async()=>{let p=await fetch('/lab/preauth',{credentials:'include'});let d=await p.json();let r=await fetch('/lab/login',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json','X-CSRF-Token':d.csrf},body:JSON.stringify({username:'synthetic-student',password:PASSWORD,remember_me:false})});let v=await r.json();window.s3csrf=v.csrf;return {ok:r.status===200,state:v.state,cache:r.headers.get('Cache-Control')}})()""".replace('PASSWORD',json.dumps(lab.password))
        result=client.eval(expression,session);check('Chromium_actual_login_CSRF_cookie',result=={'ok':True,'state':'authenticated','cache':'no-store'})
        check('Chromium_HttpOnly_not_JS_readable','__Host-classroom_session' not in client.eval('document.cookie',session))
        cookies=client.call('Storage.getCookies')['cookies'];ours=[c for c in cookies if c['name']=='__Host-classroom_session']
        check('Chromium_cookie_attributes',len(ours)==1 and ours[0]['secure'] is True and ours[0]['httpOnly'] is True and ours[0]['sameSite']=='Lax' and ours[0]['path']=='/' and ours[0]['domain']=='app.test' and ours[0]['session'] is True)
        check('Chromium_no_long_term_localStorage',client.eval('localStorage.length===0 && sessionStorage.length===0',session) is True)
        control=client.call('Network.setCookie',{'name':'s3_domain_control','value':'synthetic-public','domain':'.app.test','path':'/','secure':True},session)
        check('Chromium_Domain_cookie_API_positive_control',control.get('success') is True)
        client.call('Network.deleteCookies',{'name':'s3_domain_control','domain':'.app.test','path':'/'},session)
        rejection_code=None
        try:
            bad=client.call('Network.setCookie',{'name':'__Host-classroom_session','value':'forged','domain':'.app.test','path':'/','secure':True},session)
            rejected=bad.get('success') is False
        except CDPError as ex:
            rejection_code=ex.code;rejected=ex.method=='Network.setCookie' and ex.code==-32602
        after=[c for c in client.call('Storage.getCookies')['cookies'] if c['name']=='__Host-classroom_session']
        check('Chromium_Host_prefix_Domain_injection_rejected',rejected and len(after)==1 and after[0]['domain']=='app.test' and after[0]['value']==ours[0]['value'],{'CDP_rejection_code':rejection_code,'cookie_jar_unchanged':True})
        # WebCrypto nonexportable P-256 key in real page memory; private part
        # cannot be exported, only public JWK and boolean assertions leave it.
        result=client.eval("""(async()=>{let k=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},false,['sign','verify']);let m=new TextEncoder().encode('PR03b synthetic receiver action');let s=await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'},k.privateKey,m);let ok=await crypto.subtle.verify({name:'ECDSA',hash:'SHA-256'},k.publicKey,s,m);let denied=false;try{await crypto.subtle.exportKey('jwk',k.privateKey)}catch(e){denied=true}return {ok,bytes:s.byteLength,private_export_denied:denied}})()""",session)
        check('Chromium_WebCrypto_P1363_nonexportable_key',result=={'ok':True,'bytes':64,'private_export_denied':True})
        # Genuine browser WSS includes HttpOnly cookie automatically.
        result=client.eval("""(async()=>{let r=await fetch('/lab/ws-ticket',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':window.s3csrf},body:'{}'});let t=await r.json();return await new Promise(resolve=>{let w=new WebSocket('WSS_URL','classroom.v1');let timer=setTimeout(()=>{w.close();resolve(false)},7000);w.onopen=()=>w.send(JSON.stringify({type:'authenticate',ticket:t.ticket}));w.onmessage=e=>{clearTimeout(timer);let ok=JSON.parse(e.data).type==='authenticated';w.close();resolve(ok)};w.onerror=()=>{clearTimeout(timer);resolve(false)}})})()""".replace('WSS_URL','wss://app.test:'+str(lab.ws_port)+'/lab/ws'),session)
        check('Chromium_actual_WSS_cookie_Origin_ticket',result is True)
        return {'version':version['product'],'TLS_validation_disabled':False,'sandbox_disabled':False,'profile_exported':False}
    finally:
        if client:
            try:client.call('Browser.close')
            except Exception:pass
            try:client.ws.close()
            except Exception:pass
        if job:job.close();check('Chromium_owned_job_closed',job.cleanup_confirmed is True)
        trust(lab,False);check('guest_synthetic_CA_removed',True)
