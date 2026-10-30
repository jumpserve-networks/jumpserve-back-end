"""Fixed Linux-only duration-based TCP loss sweep, never submitted source code.

One invocation is one host/replication containing randomized loss conditions.
Only private veth paths are tested. All command outputs are retained in the
observation provenance, including failures. No observed value is imputed.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import shutil
import statistics
import subprocess
import time
import uuid

VERSION = 'netem-iperf-v1'
MAX_COUNTER_BYTES = 20000000000
METRICS = {'throughput_mbit_s': 'Mbit/s', 'received_bytes': 'bytes', 'duration_seconds': 's', 'idle_rtt_ms': 'ms', 'retransmits': 'count'}
FIELDS = {'cca', 'loss_levels_pct', 'rate_mbit', 'one_way_delay_ms', 'duration_seconds', 'queue_packets', 'mtu', 'socket_buffer_bytes', 'seed', 'loss_seed', 'ping_count'}


def validate(config):
    if not isinstance(config, dict) or set(config) != FIELDS: raise ValueError('Declare all duration-based namespace configuration fields.')
    bounds = dict(rate_mbit=(1,1000), one_way_delay_ms=(1,500), duration_seconds=(1,120), queue_packets=(16,10000), mtu=(1280,9000), socket_buffer_bytes=(4096,16777216), seed=(0,2147483647), ping_count=(3,20))
    for key, (lo,hi) in bounds.items():
        if type(config[key]) is not int or not lo <= config[key] <= hi: raise ValueError('Invalid '+key+'.')
    if config['loss_seed'] is not None and (type(config['loss_seed']) is not int or not 0<=config['loss_seed']<=2147483647): raise ValueError('Loss seed must be an explicit integer or null (uncontrolled kernel PRNG).')
    losses = config['loss_levels_pct']
    if not isinstance(losses,list) or not 1 <= len(losses) <= 8 or any(type(x) not in (int,float) or not math.isfinite(x) or not 0 <= x <= 30 for x in losses) or len(set(losses)) != len(losses): raise ValueError('Choose 1..8 distinct independent forward loss percentages from 0 through 30.')
    if config['cca'] not in ('cubic','reno'): raise ValueError('Only installed CUBIC or Reno is supported; exact kernel version is recorded.')
    return config


def minimum_wall(config):
    return 300 + len(config['loss_levels_pct']) * (config['duration_seconds'] + config['ping_count'] + 20)


def observation_key(loss):
    # Preserve the historical representation, including 0 versus 0.0.
    return 'loss:' + str(loss)


def observation_contract(config=None):
    return dict(version='netem-observation-identities-v1',
        key_rule="'loss:' + str(loss_pct) using the original JSON number; 0 -> loss:0, 0.0 -> loss:0.0. Do not add replication to the key.",
        metrics=METRICS, observations=None if config is None else [dict(observation_key=observation_key(loss),loss_pct=loss,metrics=list(METRICS)) for loss in validate(config)['loss_levels_pct']],
        replication='Each host/run is a separate replication; the same condition key repeats across hosts. Select by configuration identity, metric and observation key.',
        idle_rtt_ms='Median successful ICMP reply times, measured before the transfer on the same lossy path. Lost replies are not replaced; no successful replies means missing/null.',
        missing='Every condition emits every metric. Missing or invalid values are null with status=missing and a reason; recorded zero remains zero.',
        order='Conditions execute in seeded randomized order; identify them by key and loss_pct, never output position.')


def network_budget(config):
    # Multiple veth interface counters count forwarded bytes repeatedly. This is
    # an enforcement allowance, not billed external traffic or predicted goodput.
    return math.ceil(config['rate_mbit'] * 1_000_000 / 8 * config['duration_seconds'] * len(config['loss_levels_pct']) * 10 + 10_000_000)


def parse_transfer(raw):
    data = json.loads(raw)
    if data.get('error'): raise ValueError('iperf3 reported an error.')
    end = data['end']; rx = end['sum_received']; sent = end.get('sum_sent',{})
    values = dict(received_bytes=rx.get('bytes'), duration_seconds=rx.get('seconds'), throughput_mbit_s=None, retransmits=sent.get('retransmits'))
    for key in ('received_bytes','duration_seconds'):
        x = values[key]
        if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or x < 0: raise ValueError('Missing or invalid receiver byte/time counter.')
    if values['duration_seconds'] <= 0: raise ValueError('Receiver duration must be positive.')
    values['throughput_mbit_s'] = 8 * values['received_bytes'] / values['duration_seconds'] / 1_000_000
    bps = rx.get('bits_per_second')
    if isinstance(bps,bool) or not isinstance(bps,(int,float)) or not math.isfinite(bps) or not math.isclose(values['throughput_mbit_s'] * 1_000_000, bps, rel_tol=1e-6, abs_tol=1): raise ValueError('iperf3 receiver counters violate bytes/time/rate consistency.')
    return values


def run(spec):
    if spec.get('implementation_sha256') and hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=spec['implementation_sha256']: raise ValueError('Frozen built-in implementation hash differs on the experiment host.')
    config = validate(spec['config']); tick = time.monotonic(); commands = []; observations = []; server = None
    names = ['js'+uuid.uuid4().hex[:10]+s for s in ('s','r','d')]; sender,router,receiver = names
    created = []; order = list(config['loss_levels_pct']); random.Random(config['seed'] + spec['replication']).shuffle(order)
    provenance = dict(runner=VERSION, implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), kernel=platform.release(),
                      requested=config, replication=spec['replication'], condition_order=order, commands=commands,
                      data_origin='new-empirical', environment='Single isolated Linux host; namespace emulation. Conditions on the same host are dependent.',
                      seed_scope='seed randomizes condition order; loss_seed=null leaves the kernel loss PRNG uncontrolled. No loss seed is claimed when null.',
                      limitation='No causal window/queue mechanism identification, competing flows, external Internet measurement or independent-host population inference.')
    def command(args, ns=None, check=True, timeout=10):
        argv = (['ip','netns','exec',ns] if ns else []) + args
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=dict(os.environ,LC_ALL='C'))
        commands.append(dict(argv=argv, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr))
        if result.returncode and check: raise ValueError('Linux experiment command failed: '+args[0])
        return result.stdout
    def shape(dev, loss, seed):
        command(['tc','qdisc','del','dev',dev,'root'],router,check=False)
        command(['tc','qdisc','replace','dev',dev,'root','handle','1:','htb','default','10'],router)
        command(['tc','class','replace','dev',dev,'parent','1:','classid','1:10','htb','rate',str(config['rate_mbit'])+'mbit','ceil',str(config['rate_mbit'])+'mbit'],router)
        # seed is a netem option (not an argument to loss random).
        args=['tc','qdisc','replace','dev',dev,'parent','1:10','handle','10:','netem','limit',str(config['queue_packets']),'delay',str(config['one_way_delay_ms'])+'ms','loss','random',str(loss)+'%']
        if config['loss_seed'] is not None: args+=['seed',str(seed)]
        command(args,router)
    try:
        if platform.system() != 'Linux' or os.geteuid() != 0: raise ValueError('An isolated Linux host with network namespace privileges is required.')
        for tool in ('ip','tc','iperf3','ping','ethtool','sysctl'):
            if not shutil.which(tool): raise ValueError('Required Linux tool unavailable: '+tool)
        provenance['iperf_version'] = command(['iperf3','--version']); provenance['iproute_version'] = command(['tc','-V'])
        for ns in names:
            command(['ip','netns','add',ns]); created.append(ns); command(['ip','link','set','lo','up'],ns)
        for left,right,lns,rns,laddr,raddr in [('js0','jr0',sender,router,'10.224.0.2/30','10.224.0.1/30'),('jr1','jd0',router,receiver,'10.225.0.1/30','10.225.0.2/30')]:
            # Create inside the namespace: concurrent invocations have no host
            # interface-name collision or persistent host networking changes.
            command(['ip','link','add',left,'type','veth','peer','name',right],lns)
            command(['ip','link','set',right,'netns',rns],lns)
            for dev,ns,address in [(left,lns,laddr),(right,rns,raddr)]:
                command(['ip','addr','add',address,'dev',dev],ns)
                command(['ip','link','set',dev,'mtu',str(config['mtu']),'up'],ns)
                command(['ethtool','-K',dev,'tso','off','gso','off','gro','off'],ns)
                command(['ethtool','-k',dev],ns)
        command(['ip','route','add','default','via','10.224.0.1'],sender)
        command(['ip','route','add','default','via','10.225.0.1'],receiver)
        command(['sysctl','-w','net.ipv4.ip_forward=1'],router)
        # net.core maxima are not exposed in child network namespaces on all
        # kernels. Record host defaults without changing the host; iperf3's
        # explicit -w sets each test socket or fails visibly if unsupported.
        command(['sysctl','net.core.rmem_max','net.core.wmem_max'],check=False)
        for ns in (sender,receiver):
            command(['sysctl','-w','net.ipv4.tcp_congestion_control='+config['cca']],ns)
            command(['sysctl','net.ipv4.tcp_congestion_control','net.ipv4.tcp_rmem','net.ipv4.tcp_wmem'],ns)
        for loss in order:
            key = observation_key(loss); metadata = dict(loss_pct=loss, replication=spec['replication'], requested_rtt_ms=2*config['one_way_delay_ms'])
            values = {}; reason = None
            try:
                seed = ((config['loss_seed'] or 0) + spec['replication']*101 + config['loss_levels_pct'].index(loss)) % 2147483647
                shape('jr1',loss,seed); shape('jr0',0,seed)
                # Ping uses the configured lossy path. No successful sample is
                # replaced. Empty samples remain missing.
                ping = command(['ping','-n','-c',str(config['ping_count']),'-W','2','10.225.0.2'],sender,check=False,timeout=config['ping_count']*3+5)
                samples = [float(x) for x in re.findall(r'time[=<]([0-9.]+)\s*ms',ping)]
                values['idle_rtt_ms'] = statistics.median(samples) if samples else None
                metadata['ping_received'] = len(samples); metadata['ping_requested'] = config['ping_count']
                metadata['load_before'] = os.getloadavg()
                server = subprocess.Popen(['ip','netns','exec',receiver,'iperf3','-s','-1','-J'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                # Readiness uses ss, not a TCP probe which consumes iperf3's
                # single server connection. No measured trial is retried.
                for _ in range(30):
                    if ':5201' in command(['ss','-ltn'],receiver): break
                    if server.poll() is not None: raise ValueError('iperf3 server exited before readiness.')
                    time.sleep(.1)
                else: raise ValueError('iperf3 server readiness deadline exceeded.')
                raw = command(['iperf3','-c','10.225.0.2','-t',str(config['duration_seconds']),'-C',config['cca'],'-w',str(config['socket_buffer_bytes']),'-J','--get-server-output'],sender,timeout=config['duration_seconds']+20)
                values.update(parse_transfer(raw))
                out,err = server.communicate(timeout=10); commands.append(dict(argv=['iperf3','server'],returncode=server.returncode,stdout=out,stderr=err)); server = None
                if values['duration_seconds'] < config['duration_seconds']*.9: raise ValueError('Actual receiver transfer duration is shorter than the declared trial.')
                metadata['load_after'] = os.getloadavg()
                for dev in ('jr0','jr1'):
                    command(['tc','-s','-j','qdisc','show','dev',dev],router)
                    command(['tc','-s','-j','class','show','dev',dev],router)
                for ns in names: command(['ip','-s','-j','link','show'],ns)
            except (ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
                reason = type(error).__name__+': '+str(error); values = {}
            finally:
                if server:
                    server.terminate()
                    try: out,err = server.communicate(timeout=3)
                    except subprocess.TimeoutExpired: server.kill(); out,err = server.communicate(timeout=3)
                    commands.append(dict(argv=['iperf3','server'],returncode=server.returncode,stdout=out,stderr=err)); server = None
            for metric,units in METRICS.items():
                value = values.get(metric)
                valid = not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value) and value >= 0
                observations.append(dict(observation_key=key,metric=metric,units=units,value=value if valid else None,status='recorded' if valid else 'missing',reason=None if valid else reason or 'Metric unavailable in retained tool output.',metadata=metadata))
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        provenance['failure']=type(error).__name__+': '+str(error)
        for loss in order:
            if not any(r['metadata']['loss_pct']==loss for r in observations):
                observations.extend(dict(observation_key=observation_key(loss),metric=m,units=u,value=None,status='missing',reason=provenance['failure'],metadata=dict(loss_pct=loss,replication=spec['replication'])) for m,u in METRICS.items())
    finally:
        for ns in reversed(created): command(['ip','netns','del',ns],check=False)
    return dict(version='network-observations-v1',observations=observations,provenance=provenance,
                usage=dict(process_wall_seconds=time.monotonic()-tick,measured_charges_usd=None))


if __name__ == '__main__':
    result = run(json.loads(Path(os.environ['JUMPSERVE_SPEC']).read_text()))
    Path(os.environ['JUMPSERVE_OUTPUT']).write_text(json.dumps(result,allow_nan=False,separators=(',',':'))+'\n')
