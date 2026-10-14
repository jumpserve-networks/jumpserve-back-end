"""Versioned executable networking contracts. Descriptions are not executable code.

One job is one experimental invocation, not a packet, client or time sample.
Built-in runners retain their original measurement stores. Custom packages run
on isolated experiment machines and return an explicit observation envelope.
"""
import copy
import hashlib
import math
import re
from workflow import canonical, digest, identifier

VERSION = "network-execution-v1"
RUNNERS = {
    "emulated-netem-v1": dict(name="Emulated networking", mode="emulated", module="congestion-control-emulated", maximum_instances=1),
    "real-world-iperf-v1": dict(name="Real-world networking", mode="real-world", module="congestion-control-real-world", maximum_instances=18),
    "custom-package-v1": dict(name="Versioned experiment package", mode="package", module="research-verification", maximum_instances=1),
}
BUILTIN_METRICS = {
    'emulated-netem-v1': {'flow_completion_time_ms':'ms'},
    'real-world-iperf-v1': {'throughput_mbit_s':'Mbit/s','received_bytes':'bytes','duration_seconds':'s','rtt_median_ms':'ms','retransmits':'count'},
}
MAX_JOBS = 32

def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} through {high}.")
    return value

def number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} is outside its declared bounds.")
    return value

def placement(value):
    if not isinstance(value, dict) or set(value) != {"region", "zone_id", "instance_type"}:
        raise ValueError("Every real-world machine needs region, zone_id and instance_type.")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", str(value['region'])) or not re.fullmatch(r"[a-z0-9-]+-az\d+", str(value['zone_id'])):
        raise ValueError("Invalid placement identity.")
    if value['instance_type'] not in ('t3.small', 't3.medium', 't3.large'):
        raise ValueError("Unsupported instance type.")

def config(runner, value):
    if not isinstance(value, dict): raise ValueError("test_config must be an object.")
    if runner == 'emulated-netem-v1':
        allowed = {'num_clients', 'client_delays_ms', 'client_ccas', 'client_file_sizes_mbytes', 'client_start_delays_ms', 'loss_pct', 'snapshot_interval_ms', 'script', 'topology', 'bottleneck_rates_mbit', 'bottleneck_buffers_kbytes', 'client_groups', 'bottleneck_all_client_rate_mbit', 'bottleneck_buffer_kbytes', 'snapshot_metrics_source'}
        if set(value) - allowed: raise ValueError("Unsupported emulated test fields.")
        n = integer(value.get('num_clients'), 'num_clients', 1, 10)
        for name, low, high in [('client_delays_ms', 0, 5000), ('client_file_sizes_mbytes', .1, 1000), ('client_start_delays_ms', 0, 600000)]:
            if name not in value and name == 'client_start_delays_ms': continue
            if not isinstance(value.get(name), list) or len(value[name]) != n: raise ValueError(name+' must match num_clients.')
            for v in value[name]: number(v, name, low, high)
        if not isinstance(value.get('client_ccas'), list) or len(value['client_ccas']) != n or any(v not in ('cubic','bbr','bbr2','bbr3','reno','vegas','htcp','highspeed','scalable','westwood') for v in value['client_ccas']): raise ValueError('Invalid congestion control algorithm.')
        if value.get('script', 'netem_nines.py') not in ('netem_nines.py','netem_multi_bottleneck.py','netem_cubic_benchmark_hotnets.py','netem_cubic_benchmark_nines.py'): raise ValueError('Unknown emulated workload.')
        if value.get('script')=='netem_nines.py' and n!=2: raise ValueError('The netem_nines.py workload requires exactly two clients; choose a general client-count workload otherwise.')
        if value.get('script') == 'netem_multi_bottleneck.py':
            if value.get('topology') not in ('parking-lot', 'dumbbell'): raise ValueError('Choose a supported topology.')
            for key, low, high in [('bottleneck_rates_mbit',1,10000),('bottleneck_buffers_kbytes',0,100000)]:
                if not isinstance(value.get(key),list) or len(value[key])!=2: raise ValueError('Two bottlenecks required.')
                for v in value[key]: number(v,key,low,high)
            if value['topology']=='dumbbell' and (not isinstance(value.get('client_groups'),list) or len(value['client_groups'])!=2 or any(type(v) is not int or v<1 for v in value['client_groups']) or sum(value['client_groups'])!=n): raise ValueError('Client groups must partition clients.')
        else:
            number(value.get('bottleneck_all_client_rate_mbit'), 'rate', 1, 10000)
            number(value.get('bottleneck_buffer_kbytes'), 'buffer', 0, 100000)
        if 'loss_pct' in value: number(value['loss_pct'],'loss_pct',0,100)
        if 'snapshot_interval_ms' in value: integer(value['snapshot_interval_ms'],'snapshot_interval_ms',1,60000)
        if value.get('snapshot_metrics_source','kernel') not in ('kernel','ss'): raise ValueError('Invalid snapshot source.')
        instances = 1
    elif runner == 'real-world-iperf-v1':
        if set(value) != {'server','bottleneck','receivers','cca','duration_seconds','rate_mbit','buffer_kbytes','notes'}: raise ValueError('Invalid real-world test configuration.')
        if not isinstance(value['receivers'],list) or not 1<=len(value['receivers'])<=16: raise ValueError('Choose 1–16 receivers.')
        for p in [value['server'],value['bottleneck'],*value['receivers']]: placement(p)
        if value['cca'] not in ('cubic','bbr','reno'): raise ValueError('Invalid real-world CCA.')
        integer(value['duration_seconds'],'duration_seconds',10,600)
        integer(value['rate_mbit'],'rate_mbit',1,1000)
        integer(value['buffer_kbytes'],'buffer_kbytes',2,10000)
        if not isinstance(value['notes'],str) or len(value['notes'])>4000: raise ValueError('Invalid notes.')
        instances = 2+len(value['receivers'])
    elif runner == 'custom-package-v1':
        if set(value)-{'mode','package','arguments','parameters','private_inputs'} or not {'mode','package','arguments','parameters'}<=set(value) or value['mode'] not in ('emulated','real-world'): raise ValueError('A package needs mode, package, arguments and parameters.')
        inputs=value.get('private_inputs',{})
        if not isinstance(inputs,dict) or len(inputs)>8: raise ValueError('At most eight private input files are supported.')
        for name,item in inputs.items():
            if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,99}',name) or not isinstance(item,dict) or set(item)!={'artifact_id','sha256','version'} or not re.fullmatch(r'[a-f0-9]{64}',str(item['sha256'])) or not isinstance(item['version'],str) or not item['version']: raise ValueError('Private files need safe names, immutable artifact identities, hashes and versions.')
            identifier(item['artifact_id'])
        package = value['package']
        if not isinstance(package,dict): raise ValueError('Versioned package required.')
        if package.get('kind') == 'python-script':
            if set(package) != {'kind','version','artifact_id','sha256'} or not re.fullmatch(r'[a-f0-9]{64}',str(package.get('sha256'))): raise ValueError('A Python package needs its private artifact_id and original-byte SHA256.')
            identifier(package['artifact_id'])
        elif package.get('kind') == 'container':
            if set(package) != {'kind','version','image'} or not re.fullmatch(r'(?:public\.ecr\.aws|docker\.io)/[a-z0-9._/-]+@sha256:[a-f0-9]{64}',str(package['image'])): raise ValueError('Use a public container image pinned by immutable sha256 digest.')
        else: raise ValueError('Choose a versioned Python script or container image.')
        if not isinstance(package.get('version'),str) or not 1<=len(package['version'])<=200: raise ValueError('Declare package version.')
        if not isinstance(value['arguments'],list) or len(value['arguments'])>32 or any(not isinstance(v,str) or len(v)>1000 for v in value['arguments']): raise ValueError('Arguments must be at most 32 bounded strings, not a shell command.')
        if not isinstance(value['parameters'],dict): raise ValueError('Package parameters must be an object.')
        instances = 1
    else: raise ValueError('No registered runner for this test.')
    return instances

def specs(document, verify_hashes=True):
    execution = document.get('execution')
    if execution is None: return []
    if not isinstance(execution,dict) or set(execution)-{'version','runner','replications','limits','exclusive_resources'} or not {'version','runner','replications','limits'}<=set(execution) or execution['version'] != VERSION or execution['runner'] not in RUNNERS: raise ValueError('Unsupported networking execution specification.')
    resources=execution.get('exclusive_resources',[])
    if not isinstance(resources,list) or len(resources)>16 or len(set(resources))!=len(resources) or any(not isinstance(v,str) or not re.fullmatch(r'[a-z0-9][a-z0-9:._/-]{0,99}',v) for v in resources): raise ValueError('Declare stable, shared exclusive resource identities.')
    replicas = integer(execution['replications'],'replications',1,MAX_JOBS)
    limits = execution['limits']
    if not isinstance(limits,dict) or set(limits) != {'wall_seconds','max_instances','max_output_bytes','max_network_bytes'}: raise ValueError('Networking execution needs explicit wall, instance, output and network bounds.')
    integer(limits['wall_seconds'],'wall_seconds',60,3600)
    integer(limits['max_instances'],'max_instances',1,RUNNERS[execution['runner']]['maximum_instances'])
    integer(limits['max_output_bytes'],'max_output_bytes',1024,10000000)
    if execution['runner']=='custom-package-v1' and limits['max_output_bytes']>3000000: raise ValueError('Custom package output is bounded to 3 MB for the private ingestion transport.')
    integer(limits['max_network_bytes'],'max_network_bytes',1024,10000000000)
    if limits['wall_seconds'] > document['resource_limits']['wall_seconds']: raise ValueError('Execution exceeds reviewed protocol wall-time budget.')
    output=[]
    planned_observations=sum(integer(m['planned_observations'],'planned_observations',1,document['resource_limits']['max_observations']) for m in document['metrics'] if m.get('planned_observations') is not None)
    if planned_observations>document['resource_limits']['max_observations']: raise ValueError('Declared observations exceed the reviewed per-invocation observation budget.')
    metrics=[dict(name=m['name'],units=m['units']) for m in document['metrics']]
    if execution['runner'] in BUILTIN_METRICS and any(BUILTIN_METRICS[execution['runner']].get(m['name'])!=m['units'] for m in metrics): raise ValueError('The built-in runner cannot collect a declared metric/unit. Use an applicable custom package or choose its recorded metrics.')
    for index, configuration in enumerate(document['configurations']):
        test = configuration['details'].get('test_config')
        if config(execution['runner'],test) > limits['max_instances']: raise ValueError('Requested topology exceeds instance budget.')
        for replica in range(replicas):
            output.append(dict(version=VERSION,runner=execution['runner'],configuration_index=index,configuration_identity=configuration['identity'],replication=replica,config=copy.deepcopy(test),limits=copy.deepcopy(limits),metrics=metrics))
    if len(output) > MAX_JOBS or document.get('planned_units') != len(output): raise ValueError('planned_units must equal configurations × replications, at most 32 invocations.')
    if verify_hashes:
        declared = {v['sha256'] for v in document['input_versions']}
        if any(digest(canonical(s)) not in declared for s in output): raise ValueError('Every networking specification needs its exact canonical original-byte hash in input_versions.')
    return output

def bind_inputs(document):
    """Explicit configuration-only compilation; never fills scientific methods."""
    value=copy.deepcopy(document)
    generated=specs(value,verify_hashes=False)
    value['input_versions']=[i for i in value.get('input_versions',[]) if not str(i.get('identity','')).startswith(VERSION+':')]
    value['input_versions'].extend(dict(identity=f"{VERSION}:{s['configuration_identity']}:{s['replication']}",sha256=digest(canonical(s))) for s in generated)
    return value

def observations(raw, declared_metrics, maximum):
    """Validate recorded observations without inferring claims or missing zeroes."""
    import json
    from runner import parsed_decimal
    value=json.loads(raw,parse_float=parsed_decimal,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite observation JSON.')))
    if not isinstance(value,dict) or set(value)!={'version','observations','provenance','usage'} or value['version']!='network-observations-v1': raise ValueError('Package must return network-observations-v1 observations, provenance and usage.')
    rows=value['observations']; declared={m['name']:m['units'] for m in declared_metrics}; specifications={m['name']:m for m in declared_metrics}; seen=set()
    if not isinstance(rows,list) or len(rows)>maximum: raise ValueError('Observation budget exceeded.')
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'observation_key','metric','units','value','status','reason','metadata'}: raise ValueError('Observation schema is incomplete.')
        identity=(row['observation_key'],row['metric'])
        if not isinstance(row['observation_key'],str) or not row['observation_key'] or identity in seen: raise ValueError('Duplicate or invalid observation identity.')
        seen.add(identity)
        if row['metric'] not in declared or row['units']!=declared[row['metric']]: raise ValueError('Observation units or metric differ from the frozen protocol.')
        if row['status'] not in ('recorded','missing','invalid','ambiguous','excluded') or not isinstance(row['metadata'],dict): raise ValueError('Invalid observation status or metadata.')
        if row['status']=='recorded':
            if isinstance(row['value'],dict) and set(row['value'])=={'invalid_numeric_lexeme'}:
                row['metadata']=dict(row['metadata'],original_numeric_lexeme=row['value']['invalid_numeric_lexeme'])
                row.update(status='invalid',value=None,reason='Numeric lexical precision or underflow cannot be represented; original bytes retained.')
                continue
            number(row['value'],'recorded value',-1e308,1e308)
            metric=specifications[row['metric']]
            if row['value']<metric.get('lower_bound',-1e308) or row['value']>metric.get('upper_bound',1e308):
                row['metadata']=dict(row['metadata'],original_reported_value=row['value'])
                row.update(status='invalid',value=None,reason='Recorded value is outside the frozen metric bounds; original reported value retained separately.')
        elif row['value'] is not None or not isinstance(row['reason'],str) or not row['reason'].strip(): raise ValueError('Unavailable observations require null value and a reason.')
    if not isinstance(value['provenance'],dict) or value['usage'] is not None and not isinstance(value['usage'],dict): raise ValueError('Record actual provenance and available usage; unknown usage stays null.')
    return value
