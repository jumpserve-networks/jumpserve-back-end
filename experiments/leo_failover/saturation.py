"""Execute the separately frozen terminal-budget saturation follow-up."""
import json
import time
from pathlib import Path
from run import ROOT, COUNTRIES, scenario, digest
def main():
    protocol=ROOT/'saturation-protocol.json'
    folder=ROOT/'results/saturation'; folder.mkdir(exist_ok=True)
    jobs=[]
    for country in COUNTRIES:
        for count in [500000,1000000,2000000,4000000]: jobs.append((country,'starlink_5shells',count,'gcb-0','greedy-coordinated',1.28,'paper',0))
        for second in range(1,15): jobs.append((country,'starlink_5shells',2000000,'gcb-0','greedy-coordinated',1.28,'paper',second))
        jobs.append((country,'starlink_5shells',4000000,'gcb-0','greedy-coordinated',1.28,'artifact',0))
    (folder/'schedule.json').write_text(json.dumps(jobs,indent=2)+'\n')
    records=folder/'samples.jsonl'
    completed={r['id'] for r in map(json.loads,records.read_text().splitlines())} if records.exists() else set()
    for i,job in enumerate(jobs):
        key='-'.join(map(str,job))
        if key in completed: continue
        started=time.time(); row=scenario(*job)
        row.update(wall_seconds=time.time()-started,runner_sha256=digest(ROOT/'run.py'),protocol_sha256=digest(protocol),followup_runner_sha256=digest(Path(__file__)))
        if row['validation_errors']: raise RuntimeError(row['validation_errors'])
        with records.open('a') as f:f.write(json.dumps(row)+'\n')
        print(f'{i+1}/{len(jobs)} {country}: {row["capacity_gbps"]:.3f} Gbps ({row["requested_terminals"]} terminals, t={row["second"]})',flush=True)
if __name__=='__main__':main()
