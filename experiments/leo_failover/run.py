"""Reproduce CosmoSim capacity calculations; no live satellite measurements.

The upstream GPL code remains in its separate, pinned source checkout. This
runner uses its beam allocation and routing policies, with exact PyEphem range
checks after conservative spatial pruning. See protocol.json for scope.
"""
import argparse
import contextlib
import csv
import hashlib
import io
import json
import math
import pickle
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'sources/CosmoSim'
sys.path.insert(0, str(SOURCE))
import ephem
import h3
import networkx as nx
import numpy as np
from scipy.spatial import cKDTree
from astropy import units as u
from utils.distance_tools import (geodetic2cartesian, distance_m_ground_station_to_satellite,
                                 distance_m_ground_station_to_cell, distance_m_between_satellites)
from utils.ground_stations import read_ground_stations_extended
from utils.tles import read_tles
from utils.isls import read_isls
from spectrum_management.beam_mapping import beam_mapping
from workflows.generate_capacities import generate_capacities

COUNTRIES = ['tonga', 'haiti', 'lithuania', 'ghana', 'britain', 'southafrica']
PUBLISHED = dict(zip(COUNTRIES, [41, 1389, 2005, 2163, 3530, 4653]))
LOST = dict(zip(COUNTRIES, [320, 320, 101, 83700, 400000, 31700]))

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def populations(country):
    with (SOURCE / f'inputs/cells/{country}.txt').open() as f:
        return {r['h3_index']: int(float(r['population'])) for r in csv.DictReader(f)}

def allocation(pop, count, policy, ku, variant):
    """Keep requested/deployed totals separate; Hamilton rounding for density."""
    ordered = sorted(pop, key=lambda c: (-pop[c], c))
    out = dict.fromkeys(ordered, 0)
    if policy == 'population':
        total = sum(pop.values())
        quotas = {c: count * pop[c] / total for c in ordered}
        out = {c: math.floor(quotas[c]) for c in ordered}
        for c in sorted(ordered, key=lambda c: (-(quotas[c] - out[c]), c))[:count - sum(out.values())]:
            out[c] += 1
    elif policy == 'uniform':
        q, rem = divmod(count, len(ordered))
        out = {c: q + (i < rem) for i, c in enumerate(ordered)}
    else:
        cap = int(policy.split('-')[1])
        eligible = [c for c in ordered if pop[c] >= cap and (variant == 'paper' or pop[c] > 0)]
        if not eligible:
            return out
        batch = 200 if variant == 'paper' else int(ku * 80)
        remaining = count
        for c in eligible:
            out[c] = min(batch, remaining)
            remaining -= out[c]
        if remaining and (variant == 'paper' or cap == 0):
            q, rem = divmod(remaining, len(eligible))
            for i, c in enumerate(eligible):
                out[c] += q + (i < rem)
    assert 0 <= sum(out.values()) <= count
    return out

def graph(country, constellation, second):
    """Spatial index only removes distant candidates; all edges use upstream ranges."""
    cache = ROOT / f'results/raw/{constellation}/{country}/graph_{second * 1000000000}.txt'
    if cache.exists():
        return cache
    directory = SOURCE / f'constellation_configurations/configs/{constellation}'
    tle = read_tles(str(directory / 'tles.txt'))
    sats, epoch = tle['satellites'], tle['epoch']
    desc = dict(line.strip().split('=', 1) for line in (directory / 'description.txt').read_text().splitlines())
    sizes = [a*b for a,b in zip(json.loads(desc['num_orbits']), json.loads(desc['num_sats_per_orbit']))]
    shell = np.repeat(np.arange(len(sizes)), sizes)
    limits = np.array(json.loads(desc['max_gsl_length_m']))[shell]
    isl_limits = np.array(json.loads(desc['max_isl_length_m']))[shell]
    date = str(epoch + second * u.s)
    coords = []
    for sat in sats:
        sat.compute(date)
        coords.append(geodetic2cartesian(math.degrees(sat.sublat), math.degrees(sat.sublong), sat.elevation))
    tree = cKDTree(coords)
    # 100 km margin exceeds WGS72 coordinate conversion error by several orders.
    pruning_radius = float(max(limits)) + 100000
    gs = read_ground_stations_extended(str(SOURCE / 'inputs/groundstations/ground_stations_starlink.txt'))
    g = nx.DiGraph()
    g.add_nodes_from(range(len(sats) + len(gs)))
    checks = 0
    for station in gs:
        pos = geodetic2cartesian(float(station['latitude_degrees_str']), float(station['longitude_degrees_str']), station['elevation_m_float'])
        for sid in sorted(tree.query_ball_point(pos, pruning_radius)):
            d = distance_m_ground_station_to_satellite(station, sats[sid], str(epoch), date)
            checks += 1
            if d <= limits[sid]:
                g.add_edge(len(sats) + station['gid'], sid, capacity=0, weight=round(d))
    for a,b in read_isls(str(directory / 'isls.txt'), len(sats)):
        d = distance_m_between_satellites(sats[a], sats[b], str(epoch), date)
        if d <= isl_limits[a]:
            g.add_edge(a,b,capacity=100000,weight=round(d))
            g.add_edge(b,a,capacity=100000,weight=round(d))
    for cell in populations(country):
        lat,lon = h3.cell_to_latlng(cell)
        pos = geodetic2cartesian(lat,lon,0)
        g.add_node(cell)
        for sid in sorted(tree.query_ball_point(pos, pruning_radius)):
            d = distance_m_ground_station_to_cell(cell, sats[sid], str(epoch), date)
            checks += 1
            if d < limits[sid]:
                g.add_edge(sid,cell,capacity=1280,weight=round(d))
    # Audit pruning against exhaustive PyEphem for the first and last cell.
    for cell in [next(iter(populations(country))), list(populations(country))[-1]]:
        exact = {sid for sid,sat in enumerate(sats) if distance_m_ground_station_to_cell(cell,sat,str(epoch),date) < limits[sid]}
        if exact != set(g.predecessors(cell)):
            raise RuntimeError('Spatial pruning lost a visible satellite')
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open('wb') as f:
        pickle.dump(g,f)
    cache.with_suffix('.json').write_text(json.dumps({'sha256':digest(cache),'nodes':len(g),'edges':g.number_of_edges(),'exact_range_checks':checks,'pruning_audit_cells':2,'epoch':str(epoch),'second':second}))
    return cache

def scenario(country, constellation, count, policy, beam, ku, variant, second):
    import utils.global_variables as globals
    pop = populations(country)
    assigned = allocation(pop,count,policy,ku,variant)
    graph_path = graph(country,constellation,second)
    with graph_path.open('rb') as f:
        g = pickle.load(f)
    directory = SOURCE / f'constellation_configurations/configs/{constellation}'
    desc = dict(line.strip().split('=',1) for line in (directory/'description.txt').read_text().splitlines())
    sizes = [a*b for a,b in zip(json.loads(desc['num_orbits']),json.loads(desc['num_sats_per_orbit']))]
    total = sum(sizes)
    ranges=[]
    start=0
    for size in sizes:
        ranges.append((start,start+size)); start+=size
    cell_sats={c:[s for s in g.predecessors(c) if g.in_degree(s)>0] for c,n in assigned.items() if n>0}
    cells=[{'cell':c,'num_terminals':n} for c,n in assigned.items() if n>0]
    with contextlib.redirect_stdout(io.StringIO()):
        mapping=beam_mapping(beam,cells,{s for v in cell_sats.values() for s in v},{},cell_sats,'jumpserve',ranges,max(1,math.floor(ku*10)),pop)
    demands=np.zeros(total)
    for c,n in assigned.items():
        channels=[f'{c}_{ch}' for ch in range(8) if f'{c}_{ch}' in mapping]
        if not channels: continue
        q,rem=divmod(n,len(channels))
        for i,ch in enumerate(channels):
            demands[int(mapping[ch].split('_')[1])]+=min((q+(i<rem))*100,ku*1000)
    key=f'{country}-{constellation}-{count}-{policy}-{beam}-{ku}-{variant}-{second}'
    output=ROOT / 'results/raw/scenarios' / key
    output.mkdir(parents=True,exist_ok=True)
    np.savetxt(output/'demands.txt',demands)
    terminals=output/'terminals.txt'
    terminals.write_text(''.join(f'{c},{n}\n' for c,n in assigned.items()))
    # Preserve released gateway provisioning and TE, excluding cost-only minimization.
    # maximum_flow gives identical aggregate capacity; min-cost changes only routes.
    import workflows.generate_capacities as capacities
    original=capacities.max_flow_min_cost
    capacities.max_flow_min_cost=lambda g,s,t: nx.maximum_flow(g,s,t)[1]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            flow,_=generate_capacities(output,graph_path.parent,constellation,'ground_stations_starlink',terminals,country,second,beam,ku,duration_s=1)
    finally:
        capacities.max_flow_min_cost=original
    value=float(np.loadtxt(flow,delimiter=',')[1])/1000
    rf=float(demands.sum())/1000
    cell_bound=len(pop)*8*ku
    terminal_bound=sum(assigned.values())*0.1
    errors=[]
    if not (math.isfinite(value) and -1e-9<=value<=min(rf,cell_bound,terminal_bound)+1e-6):
        errors.append('capacity exceeds physical demand/RF/terminal bounds')
    return {'id':key,'country':country,'constellation':constellation,'satellites':total,'requested_terminals':count,
            'deployed_terminals':sum(assigned.values()),'placement':policy,'beam_policy':beam,'ku_gbps':ku,
            'variant':variant,'second':second,'capacity_gbps':value,'rf_demand_gbps':rf,'cell_bound_gbps':cell_bound,
            'lost_capacity_gbps':LOST[country],'failover_percent':100*value/LOST[country],
            'published_capacity_gbps':PUBLISHED[country],'cells':len(pop),'served_cells':len({c.split('_')[0] for c in mapping}),
            'allocated_beams':len(mapping),'validation_errors':errors,'graph_sha256':digest(graph_path),
            'demands_sha256':digest(output/'demands.txt'),'terminal_sha256':digest(terminals)}

def schedule():
    for country in COUNTRIES:
        counts=[100,200,500,1000,2000,5000,10000,20000] if country=='tonga' else [1000,5000,10000,20000,50000,100000,200000]
        for count in counts:
            for policy in ['population','gcb-0','gcb-1000','gcb-10000','gcb-100000']:
                if not any(p>=int(policy.split('-')[1]) for p in populations(country).values()) if policy.startswith('gcb') else False:
                    continue
                yield (country,'starlink_5shells',count,policy,'greedy-coordinated',1.28,'paper',0)
        for second in range(1,15):
            yield (country,'starlink_5shells',200000,'gcb-0','greedy-coordinated',1.28,'paper',second)
        for second in [21600,43200,64800]:
            yield (country,'starlink_5shells',200000,'gcb-0','greedy-coordinated',1.28,'paper',second)
        for variant in ['paper','artifact']:
            for policy in ['population','gcb-0','gcb-1000','gcb-10000','gcb-100000']:
                if policy=='gcb-100000' and country=='tonga': continue
                for beam in ['greedy-coordinated','greedy-uncoordinated']:
                    yield (country,'starlink_5shells',200000,policy,beam,1.28,variant,0)
        for ku in [0.956,2.5]:
            yield (country,'starlink_5shells',200000,'gcb-0','greedy-coordinated',ku,'paper',0)
        for constellation in ['starlink_double','starlink_all']:
            for count in ([1000,2000,5000] if country=='tonga' else [200000,500000,1000000]):
                yield (country,constellation,count,'uniform','greedy-coordinated',1.28,'paper',0)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot',action='store_true')
    args=parser.parse_args()
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip()!='57af374432fbde4cd7b1f2dade64095f87296e05':
        raise RuntimeError('Upstream commit changed')
    protocol=json.loads((ROOT/'protocol.json').read_text())
    output=ROOT/'results'
    output.mkdir(exist_ok=True)
    records=output/('pilot.jsonl' if args.pilot else 'samples.jsonl')
    completed={r['id'] for r in map(json.loads,records.read_text().splitlines())} if records.exists() else set()
    jobs=[('tonga','starlink_5shells',200000,'gcb-0','greedy-coordinated',1.28,'paper',0)] if args.pilot else list(dict.fromkeys(schedule()))
    (output/'schedule.json').write_text(json.dumps(jobs,indent=2))
    for i,job in enumerate(jobs):
        key='-'.join(map(str,job))
        if key in completed: continue
        started=time.time()
        try:
            result=scenario(*job)
        except Exception as exc:
            failure={'id':key,'job':job,'error':str(exc),'type':type(exc).__name__}
            with (output/'failures.jsonl').open('a') as f: f.write(json.dumps(failure)+'\n')
            raise
        result['wall_seconds']=time.time()-started
        result['runner_sha256']=digest(Path(__file__))
        result['protocol_sha256']=digest(ROOT/'protocol.json')
        with records.open('a') as f: f.write(json.dumps(result)+'\n')
        print(f'{i+1}/{len(jobs)} {result["id"]}: {result["capacity_gbps"]:.3f} Gbps ({result["wall_seconds"]:.1f}s)',flush=True)
        if result['validation_errors']: raise RuntimeError(result['validation_errors'])

if __name__=='__main__': main()
