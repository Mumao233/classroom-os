"""Offline preregistration/analysis entry; never opens a DB or network."""
import argparse,json,pathlib,sys
sys.path[:0]=[str(pathlib.Path(__file__).resolve().parent.parent/'mechanisms')]
import enumeration_renewal as method

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='mode',required=True)
    planned=commands.add_parser('plan');planned.add_argument('--seed',type=int,required=True)
    analyzed=commands.add_parser('analyze');analyzed.add_argument('--plan',type=pathlib.Path,required=True)
    analyzed.add_argument('rows',type=pathlib.Path,nargs='+')
    args=parser.parse_args()
    if args.mode=='plan':
        design=method.plan(args.seed)
        result=dict(design=design,commitment=method.commitment(design),readiness=method.readiness(design,None),resource_estimate=method.resource_estimate(design))
    else:
        registration=json.loads(args.plan.read_text(encoding='utf8'));design=registration['design']
        assert registration['commitment']==method.commitment(design)
        # Require the exact current preregistered method; arbitrary lowered
        # thresholds, removed cells or a smaller comparison family are refused.
        assert design==json.loads(json.dumps(method.plan(design['seed'])))
        rows=[r for path in args.rows for r in json.loads(path.read_text(encoding='utf8'))]
        # This command cannot certify external controls. It can expose FAIL or
        # INCONCLUSIVE, but does not self-issue a whole-campaign approval.
        result=method.analyze(rows,design)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
