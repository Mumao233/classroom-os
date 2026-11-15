"""Detached fixed coordinator with explicit process return-code evidence."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import common as b
def main():
    b.require(sys.argv[1:]==['fixed-run'],'fixed_coordinator_arguments')
    with (b.HOME/'coordinator.stdout.log').open('x',encoding='utf-8') as out,(b.HOME/'coordinator.stderr.log').open('x',encoding='utf-8') as err:
        sys.stdout=out;sys.stderr=err
        try:
            import runner
            # Before any secret is generated, permit only the administrator
            # observer/OS to read the coordinator process memory.
            sys.path[:0]=[str(b.BASE),str(b.ROOT/'tools/pr03b')]
            import windows
            windows.protect_process()
            code=runner.main()
        except BaseException as ex:
            code=1;print('{"status":"STOP","phase":"coordinator_entry"}')
        finally:
            try:b.close_source_pin()
            except BaseException:code=1;print('{"status":"STOP","phase":"source_pin_cleanup"}')
            out.flush();err.flush()
        b.write(b.HOME/'EXPECTED-EXIT.json',{'expected_exit_code':code,'database_execution_allowed':False})
        return code
if __name__=='__main__':raise SystemExit(main())
