"""Private Linux mount/NSS home; never imports a CA into a host/user store."""
import json,os,pathlib,subprocess,sys,uuid,resource,ctypes
cfg=json.loads(sys.stdin.buffer.read());W=pathlib.Path(cfg['work']);O=pathlib.Path(cfg['output']);RAM=pathlib.Path(cfg['ram'])
assert os.geteuid()==0
resource.setrlimit(resource.RLIMIT_CORE,(0,0));ctypes.CDLL(None).prctl(4,0,0,0,0)
subprocess.run(['mount','--make-rprivate','/'],check=True)
private=RAM/('browser-home-'+uuid.uuid4().hex);private.mkdir()
# Keep HOME unchanged. Only the private mount namespace overlays that existing
# home path with this run-owned directory; the underlying home is untouched.
home=pathlib.Path.home();assert home in (pathlib.Path('/root'),pathlib.Path('/home/mumao'))
subprocess.run(['mount','--bind',str(private),str(home)],check=True)
temp=RAM/('browser-tmp-'+uuid.uuid4().hex);temp.mkdir()
subprocess.run(['mount','--bind',str(temp),'/tmp'],check=True)
env=dict(os.environ,LD_LIBRARY_PATH=str(W/'linux-libs/usr/lib/x86_64-linux-gnu'),TMPDIR='/tmp')
certutil=W/'linux-libs/usr/bin/certutil'
for relative in ('.pki/nssdb','.local/share/pki/nssdb'):
    db=home/relative;db.mkdir(parents=True)
    subprocess.run([certutil,'-N','--empty-password','-d','sql:'+str(db)],env=env,check=True,capture_output=True)
    subprocess.run([certutil,'-A','-n','PR03b run-owned synthetic CA','-t','C,,','-i',str(O/'root-ca.pem'),'-d','sql:'+str(db)],env=env,check=True,capture_output=True)
cfg['strictLinuxTLS']=True
run=subprocess.run([W/'node-v24.19.0-linux-x64/bin/node',pathlib.Path(cfg['harness'])/'browser_live.mjs'],input=json.dumps(cfg).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=90)
sys.stdout.buffer.write(run.stdout)
raise SystemExit(run.returncode)
