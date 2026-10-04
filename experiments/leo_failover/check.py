"""Check allocation, flow conservation and archived evidence without rerunning trials."""
import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache
import pickle
from collections import defaultdict
from pathlib import Path
import random
import unittest

import networkx as nx
import numpy as np
from run import ROOT, SOURCE, allocation, populations

@lru_cache(maxsize=2)
def archived_graph(path):
    with path.open('rb') as stream:
        return pickle.load(stream)


class ScientificChecks(unittest.TestCase):
    def test_exact_budget_and_population_eligibility(self):
        population = {'empty': 0, 'small': 999, 'eligible': 1000, 'large': 100000}
        for budget in (0, 1, 199, 200, 201, 10000):
            for policy in ('uniform', 'population', 'gcb-0', 'gcb-1000', 'gcb-100000'):
                with self.subTest(budget=budget, policy=policy):
                    assigned = allocation(population, budget, policy, 1.28, 'paper')
                    self.assertEqual(sum(assigned.values()), budget)
                    self.assertTrue(all(isinstance(n, int) and n >= 0 for n in assigned.values()))
                    if policy.startswith('gcb-'):
                        cutoff = int(policy.split('-')[1])
                        self.assertTrue(all(n == 0 for c, n in assigned.items() if population[c] < cutoff))
        # Distinct code/paper semantics must stay visible, rather than silently
        # making the same requested budget mean the same number of installations.
        self.assertEqual(sum(allocation(population, 10000, 'gcb-1000', 1.28, 'artifact').values()), 204)
        self.assertEqual(sum(allocation(population, 10000, 'gcb-0', 1.28, 'artifact').values()), 10000)
        self.assertEqual(allocation(population, 10000, 'gcb-0', 1.28, 'artifact')['empty'], 0)
        self.assertEqual(sum(allocation(population, 3, 'gcb-200000', 1.28, 'paper').values()), 0)

    def test_aggregate_flow_survives_removal_of_cost_objective(self):
        rng = random.Random(3730567)
        for trial in range(40):
            g = nx.DiGraph()
            g.add_nodes_from(range(8))
            for a in range(8):
                for b in range(8):
                    if a != b and rng.random() < .25:
                        g.add_edge(a, b, capacity=rng.randint(0, 20), weight=rng.randint(0, 100))
            with self.subTest(trial=trial):
                capacity, _ = nx.maximum_flow(g, 0, 7)
                cost_flow = nx.max_flow_min_cost(g, 0, 7)
                cost_capacity = sum(cost_flow[0].values()) - sum(cost_flow[n].get(0, 0) for n in g)
                self.assertEqual(capacity, cost_capacity)

    def test_archived_flow_conservation_and_input_integrity(self):
        checked = 0
        for folder in (ROOT/'results', ROOT/'results/saturation'):
            for line in (folder/'samples.jsonl').read_text().splitlines():
                row = json.loads(line)
                scenario = ROOT/'results/raw/scenarios'/row['id']
                graph = ROOT/f'results/raw/{row["constellation"]}/{row["country"]}/graph_{row["second"]*1000000000}.txt'
                with self.subTest(sample=row['id']):
                    for path, field in ((graph, 'graph_sha256'), (scenario/'demands.txt', 'demands_sha256'), (scenario/'terminals.txt', 'terminal_sha256')):
                        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row[field])
                    terminals = dict(line.rsplit(',', 1) for line in (scenario/'terminals.txt').read_text().splitlines())
                    self.assertEqual(sum(int(n) for n in terminals.values()), row['deployed_terminals'])
                    self.assertEqual(set(terminals), set(populations(row['country'])))
                    demands = np.atleast_1d(np.loadtxt(scenario/'demands.txt'))
                    self.assertEqual(len(demands), row['satellites'])
                    self.assertAlmostEqual(float(demands.sum())/1000, row['rf_demand_gbps'])
                    flow = json.loads((scenario/f'flow_dict_max_flow_{row["second"]}.json').read_text())
                    balance = defaultdict(float)
                    station_feed = defaultdict(float)
                    satellite_feed = defaultdict(float)
                    geometry = archived_graph(graph)
                    satellites = row['satellites']
                    for a, edges in flow.items():
                        for b, value in edges.items():
                            self.assertGreaterEqual(value, 0)
                            balance[a] -= value
                            balance[b] += value
                            if value == 0:
                                continue
                            if a == 'T':
                                self.assertTrue(b.isdecimal() and satellites <= int(b) < satellites+198)
                                self.assertLessEqual(value, 166400)
                            elif b == 'S':
                                self.assertTrue(a.startswith('D') and a[1:].isdecimal())
                                self.assertLessEqual(value, int(demands[int(a[1:])]))
                            elif b.startswith('D'):
                                self.assertEqual(b, 'D'+a)
                                self.assertLessEqual(value, int(demands[int(a)]))
                            else:
                                source, target = int(a), int(b)
                                self.assertTrue(geometry.has_edge(source, target), f'Invisible link {a}->{b}')
                                self.assertLess(target, satellites)
                                if source < satellites:
                                    multiplier = 2 if row['ku_gbps'] == 2.5 else 1
                                    self.assertLessEqual(value, geometry[source][target]['capacity']*multiplier)
                                else:
                                    station_feed[source] += value
                                    satellite_feed[target] += value
                    # Independent physical bounds, rather than copying the
                    # gateway provisioning algorithm into its own test.
                    self.assertTrue(all(value <= 166400 for value in station_feed.values()))
                    self.assertTrue(all(value <= 20800 for value in satellite_feed.values()))
                    self.assertAlmostEqual(-balance['T']/1000, row['capacity_gbps'])
                    self.assertAlmostEqual(balance['S']/1000, row['capacity_gbps'])
                    for node, value in balance.items():
                        if node not in ('S', 'T'):
                            self.assertAlmostEqual(value, 0, msg=f'Flow conservation at {node}')
                    # A geometry-independent bound is particularly tight for Tonga.
                    if row['country'] == 'tonga' and row['ku_gbps'] == 1.28:
                        self.assertLessEqual(row['capacity_gbps'], 4*8*1.28+1e-9)
                checked += 1
        self.assertEqual(checked, 562)


if __name__ == '__main__':
    result = unittest.main(verbosity=2, exit=False).result
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'checker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'command': 'experiments/leo_failover/.venv/bin/python -B experiments/leo_failover/check.py',
        'tests': result.testsRun,
        'passed': result.wasSuccessful(),
        'failures': len(result.failures),
        'errors': len(result.errors),
        'archived_states': 562,
        'random_flow_networks': 40,
        'checks': ['allocation budget and eligibility edge cases', 'aggregate max-flow versus min-cost equivalence',
                   'flow conservation', 'visible directed links', 'ISL and gateway/satellite feeder bounds',
                   'RF demand and terminal accounting', 'graph/demand/terminal input hashes'],
    }
    (ROOT/'evidence/checks.json').write_text(json.dumps(report, indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
