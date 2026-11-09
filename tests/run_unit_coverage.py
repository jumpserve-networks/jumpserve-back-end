"""Fixed local unit suites and full maintained Python-source coverage."""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
SUITES={
    'benchmark':['-m','unittest','test_benchmark_ingest','test_sender_metrics','test_kernel_metrics','test_run_queue','test_proxy_delay','test_netns_bench','test_multi_bottleneck'],
    'research':['-m','unittest','discover','-s','research_workflow/tests'],
    'supervisor':['-m','unittest','discover','-s','platform_supervisor/tests'],
    'real-world':['-m','unittest','discover','-s','real_world/tests'],
}
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='Require every measured line and branch to be covered.')
    parser.add_argument('--suite',choices=tuple(SUITES),action='append')
    parser.add_argument('--pattern',help='One test_*.py filename in a single discovery suite; writes a separate diagnostic report.')
    args=parser.parse_args()
    if args.pattern and (not re.fullmatch(r'test_[a-zA-Z0-9_]+\.py',args.pattern) or not args.suite or len(args.suite)!=1 or args.suite[0]=='benchmark'):
        parser.error('--pattern requires one discovery suite and one test_*.py filename.')
    import coverage
    # Exercise the tracer's positive and negative controls before starting any
    # measured suite. Nested tracers would interfere with the suite's trace hook.
    tracer=subprocess.run([sys.executable,str(ROOT/'tests/test_coverage_tracer_contract.py')],cwd=ROOT,capture_output=True,text=True)
    if tracer.returncode:
        print(tracer.stdout+tracer.stderr,flush=True)
        return 1
    diagnostic=bool(args.suite)
    directory=ROOT/'coverage'/('python-focused' if diagnostic else 'python')
    if diagnostic:directory=directory/('-'.join(args.suite))/(Path(args.pattern).stem if args.pattern else 'suite')
    directory.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,UNIT_COVERAGE_ROOT=str(ROOT),COVERAGE_FILE=str(directory/'.coverage'))
    config=ROOT/'.coveragerc'
    subprocess.run([sys.executable,'-m','coverage','erase','--rcfile',str(config)],cwd=ROOT,env=env,check=True)
    results=[]
    for name in args.suite or SUITES:
        started=time.monotonic()
        command=[*SUITES[name]]
        if args.pattern:command.extend(['-p',args.pattern])
        result=subprocess.run([sys.executable,'-m','coverage','run','--rcfile',str(config),*command],cwd=ROOT,env=env,capture_output=True,text=True)
        (directory/(name+'.log')).write_text(result.stdout+result.stderr)
        results.append(dict(suite=name,passed=result.returncode==0,seconds=round(time.monotonic()-started,2)))
        print(json.dumps(results[-1]),flush=True)
        if result.returncode:print((result.stdout+result.stderr)[-8000:],flush=True)
    os.environ.update(UNIT_COVERAGE_ROOT=str(ROOT),COVERAGE_FILE=str(directory/'.coverage'))
    cov=coverage.Coverage(config_file=str(config));cov.combine(strict=True,keep=True);cov.save()
    sources=sorted({str(p) for pattern in ('benchmark_ingest.py','netem_*.py','run_queue.py','research_workflow/**/*.py','platform_supervisor/**/*.py','real_world/**/*.py') for p in ROOT.glob(pattern) if 'tests' not in p.relative_to(ROOT).parts})
    cov.json_report(morfs=sources,outfile=str(directory/'coverage.json'))
    cov.xml_report(morfs=sources,outfile=str(directory/'coverage.xml'))
    cov.html_report(morfs=sources,directory=str(directory/'html'))
    report=json.loads((directory/'coverage.json').read_text());total=report['totals']
    expected={str(Path(path).resolve()) for path in sources}
    measured={str((ROOT/path).resolve()) for path in report['files']}
    missing_files=sorted(expected-measured);unexpected_files=sorted(measured-expected)
    inventory_valid=bool(expected) and not missing_files and not unexpected_files
    summary=dict(passed=all(r['passed'] for r in results),diagnostic_only=diagnostic,inventory_valid=inventory_valid,missing_files=missing_files,unexpected_files=unexpected_files,target_met=not diagnostic and inventory_valid and total['missing_lines']==0 and total.get('missing_branches',0)==0,
        files=len(report['files']),lines=dict(covered=total['covered_lines'],total=total['num_statements']),
        branches=dict(covered=total.get('covered_branches',0),total=total.get('num_branches',0)),suites=results)
    (directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)
    return 1 if not summary['passed'] or not inventory_valid else 2 if args.check and not summary['target_met'] else 0
if __name__=='__main__':raise SystemExit(main())
