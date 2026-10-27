"""Execute a newly frozen UDP program through the real package wrapper/runtime.

One offline Docker guest, no model, credentials or cloud resources. This exposed
software case is neither paper reproduction nor independent scientific evidence.
"""
import datetime as dt,json,shutil,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import generated_package,network_spec
from workflow import canonical,digest
ROOT=Path(__file__).resolve().parents[2]
IMAGE='jumpserve-netem-iperf-test@sha256:3bfc9b1b5ff3f4071334f4c0529ab5e8dc6188b13523bd9a8b4ce1259d669d01'
PROGRAM='''import json,os,socket,sys
from pathlib import Path
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as client:
 server.bind(('127.0.0.1',0));server.settimeout(2);client.settimeout(2)
 payload=b'jumpserve';client.sendto(payload,server.getsockname());received,peer=server.recvfrom(1024);server.sendto(received,peer);echo,_=client.recvfrom(1024)
 assert echo==payload
 output=dict(version='network-observations-v1',observations=[dict(observation_key='udp-echo',metric='received_bytes',units='bytes',value=len(echo),status='recorded',reason=None,metadata=dict(scope='One host-local UDP software smoke, no Internet inference'))],provenance=dict(python=sys.version,transport='UDP loopback'),usage=dict(measured_charges_usd=None))
 Path(os.environ['JUMPSERVE_OUTPUT']).write_text(json.dumps(output))
'''
def main():
 out=ROOT/'.test-artifacts/experiment-builder-packages'/dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
 project=out/'source';project.mkdir();(project/'experiment.py').write_text(PROGRAM);(project/'requirements.lock').write_bytes(b'');(project/'system-packages.lock.json').write_bytes(b'[]');(project/'build-evidence.json').write_bytes(canonical(dict(commands=[],checks=[],limitations=['Explicit exposed software fixture, not a model-built or independently reviewed experiment.'])))
 run=out/'run';run.mkdir();(run/'inputs').mkdir();raw=generated_package.freeze(project);(run/'inputs/project.zip').write_bytes(raw)
 wrapper=Path(generated_package.__file__).read_bytes();(run/'package.py').write_bytes(wrapper);shutil.copyfile(Path(generated_package.__file__).with_name('package_runtime.py'),run/'package_runtime.py')
 spec=dict(version=network_spec.VERSION,runner='built-package-v1',configuration_identity='udp-software-smoke',replication=0,config=dict(mode='emulated',package=dict(kind='python-script',version=generated_package.VERSION,sha256=digest(wrapper)),arguments=[],parameters={}),limits=dict(wall_seconds=60,max_instances=1,max_output_bytes=100000,max_network_bytes=1000000),metrics=[dict(name='received_bytes',units='bytes')]);(run/'spec.json').write_bytes(canonical(spec))
 report=dict(protocol='experiment-builder-validation-v1',status='failed',image=IMAGE,live_provider_calls=0,cloud_instances=0,measured_charges_usd=None,source_sha256=digest(PROGRAM.encode()),project_sha256=digest(raw),wrapper_sha256=digest(wrapper),scope=__doc__)
 try:
  completed=subprocess.run(['docker','run','--rm','--network','none','--cpus','1','--memory','512m','--pids-limit','128','-v',str(run)+':/experiment','-w','/experiment',IMAGE,'python3','package_runtime.py','--directory','/experiment','--deadline',str(time.time()+30)],capture_output=True,timeout=45)
  (out/'docker-stdout.bin').write_bytes(completed.stdout);(out/'docker-stderr.bin').write_bytes(completed.stderr)
  assert completed.returncode==0,(completed.returncode,completed.stderr.decode())
  output=network_spec.observations((run/'output.json').read_bytes(),spec['metrics'],1)
  assert output['observations'][0]['value']==9 and output['observations'][0]['units']=='bytes'
  receipt=json.loads((run/'runtime-receipt.json').read_bytes());assert receipt['exit_code']==0 and receipt['termination_reason'] is None
  report.update(status='passed',runtime_receipt=receipt,observations=1)
 finally:
  (out/'report.json').write_bytes(canonical(report));print(json.dumps(dict(status=report['status'],report=str(out/'report.json'))))
if __name__=='__main__':main()
