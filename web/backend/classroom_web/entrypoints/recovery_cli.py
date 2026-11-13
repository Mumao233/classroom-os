"""Interactive recovery tool. No keys, passwords or session tokens in argv.

An explicitly installed trusted factory supplies a pinned LocalControlClient
and protected credential provider. It cannot install a server root or grant a
recovery role; the broker independently checks kernel peer and current D0.
"""
import argparse,importlib,json,re,sys
from uuid import uuid4
from .recovery_control import LocalControlClient
from ..domain.auth import AuthRejected
from ..infrastructure.recovery_transport_windows import process_sid


def expected(value,kind):
    if value.get('kind')=='identity_control.error':raise AuthRejected(str(value['code']).upper(),503)
    if value.get('kind')!=kind:raise AuthRejected('OUTPUT_INVALID',503)
    return value


def main():
    parser=argparse.ArgumentParser(description='Explicit local PR-03b recovery; protected prompts only.')
    parser.add_argument('--factory',required=True,help='Trusted installed module:function; returns (pinned client, credential provider).')
    parser.add_argument('--deployment-id',required=True)
    parser.add_argument('action',choices=('open','issue','reissue','complete','status'))
    parser.add_argument('--case-id');parser.add_argument('--op-id');parser.add_argument('--target-user-id')
    parser.add_argument('--recovery-type',choices=('student','admin'));parser.add_argument('--verification-ref')
    parser.add_argument('--receiver-id');parser.add_argument('--flow-generation',type=int)
    args=parser.parse_args();client=None
    if not sys.stdin.isatty():parser.error('An interactive protected terminal is required; use the SDK only for explicitly isolated synthetic tests.')
    try:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*',args.factory):raise ValueError()
        module,name=args.factory.split(':');client,credentials=getattr(importlib.import_module(module),name)()
        if not isinstance(client,LocalControlClient) or credentials.mode not in ('actor','authority'):raise ValueError()
        def request(kind,**body):return dict(protocol_version=1,kind=kind,**body)
        hello=expected(client.exchange(request('identity_control.hello.request',deployment_id=args.deployment_id,authentication_mode=credentials.mode)),'identity_control.hello.response')
        evidence=credentials.authenticate(hello,dict(provider='windows_sid',subject=process_sid()))
        expected(client.exchange(request('identity_control.authenticate.request',authentication_challenge_id=hello['authentication_challenge_id'],evidence=evidence)),'identity_control.authenticate.response')
        if args.action=='open':
            if not all((args.target_user_id,args.recovery_type,args.verification_ref)):parser.error('open requires target-user-id, recovery-type and verification-ref')
            result=expected(client.exchange(request('recovery.case.open.request',deployment_id=args.deployment_id,target_user_id=args.target_user_id,recovery_type=args.recovery_type,verification_record_ref=args.verification_ref)),'recovery.case.open.response')
        else:
            if not args.case_id:parser.error('case-id is required')
            if args.action=='status' and not args.op_id:parser.error('status requires the original op-id; unknown results are never retried as writes')
            if args.action!='status' and (not args.receiver_id or not args.flow_generation or args.flow_generation<1):parser.error('writes require the approved receiver-id and flow-generation')
            op=args.op_id or str(uuid4())
            challenge=expected(client.exchange(request('recovery.proof.challenge.request',deployment_id=args.deployment_id,case_id=args.case_id,op_id=op,action=args.action,
                receiver_id=None if args.action=='status' else args.receiver_id,expected_flow_generation=None if args.action=='status' else args.flow_generation)),'recovery.proof.challenge.response')['challenge']
            # Only the nonsecret exact plan is displayed; nonce/signatures and
            # the provider's session/reauth evidence never go to stdout/history.
            print(json.dumps(dict(plan=challenge['plan'],plan_hash=challenge['plan_hash']),ensure_ascii=False,indent=2))
            if args.action!='status' and input(f'Type {args.action.upper()} {op} to approve this exact plan: ')!=args.action.upper()+' '+op:
                raise AuthRejected('CANCELLED',403)
            evidence=credentials.prove(challenge)
            proof=expected(client.exchange(request('recovery.proof.verify.request',challenge_id=challenge['challenge_id'],evidence=evidence)),'recovery.proof.verify.response')
            kind='recovery.status' if args.action=='status' else 'recovery.complete' if args.action=='complete' else 'recovery.issue'
            result=expected(client.exchange(request(kind+'.request',case_id=args.case_id,op_id=op,proof_id=proof['proof_id'])),kind+'.response')
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except Exception as error:
        code=error.code if isinstance(error,AuthRejected) else 'UNAVAILABLE'
        print(json.dumps(dict(code=code,retry_writes=False,status_required_before_another_write=True)),file=sys.stderr);return 1
    finally:
        if client:client.close()


if __name__=='__main__':raise SystemExit(main())
