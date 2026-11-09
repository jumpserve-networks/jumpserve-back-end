"""Owned loopback raw-frame controls, separately versioned from author reanalysis."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import platform
import random
import selectors
import socket
import struct
import subprocess
import time
from hpack import Encoder
from hyperframe.frame import Frame
ROOT=Path(__file__).resolve().parent
NODE='/opt/homebrew/opt/node/bin/node'
PREFACE=b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def frame(kind,payload=b'',flags=0,stream=0):
    assert 0<=len(payload)<2**24 and 0<=stream<2**31
    header=len(payload).to_bytes(3,'big')+bytes([kind,flags])+stream.to_bytes(4,'big')
    assert len(header)==9
    parsed,length=Frame.parse_frame_header(memoryview(header))
    assert length==len(payload) and parsed.stream_id==stream
    return header+payload
def cases():
    valid=frame(1,Encoder().encode([(':method','GET'),(':scheme','http'),(':authority','localhost'),(':path','/')]),flags=5,stream=1)
    return [
      ('valid-get',valid,'response',None,'RFC9113 4.1/8'),
      ('valid-ping',frame(6,b'12345678'),'ping-ack',None,'RFC9113 6.7'),
      ('unknown-frame-ignore',frame(255,b'')+valid,'response',None,'RFC9113 4.1'),
      ('settings-ack-payload',frame(4,b'\x00'*6,flags=1),'goaway',6,'RFC9113 6.5'),
      ('settings-on-stream',frame(4,stream=1),'goaway',1,'RFC9113 6.5'),
      ('settings-invalid-length',frame(4,b'\x00'),'goaway',6,'RFC9113 6.5'),
      ('ping-invalid-length',frame(6,b'1234567'),'goaway',6,'RFC9113 6.7'),
      ('ping-on-stream',frame(6,b'12345678',stream=1),'goaway',1,'RFC9113 6.7'),
      ('reset-on-stream-zero',frame(3,b'\x00'*4),'goaway',1,'RFC9113 6.4'),
      ('window-zero-connection',frame(8,b'\x00'*4),'goaway',1,'RFC9113 6.9'),
      ('initial-window-overflow',frame(4,struct.pack('!HI',4,2**31)),'goaway',3,'RFC9113 6.5.2'),
      ('max-frame-size-too-small',frame(4,struct.pack('!HI',5,16383)),'goaway',1,'RFC9113 6.5.2'),
    ]
def parse(raw):
    rows=[];offset=0
    while len(raw)-offset>=9:
        length=int.from_bytes(raw[offset:offset+3],'big')
        if len(raw)-offset<9+length:break
        kind,flags=raw[offset+3:offset+5];stream=int.from_bytes(raw[offset+5:offset+9],'big')&0x7fffffff
        payload=raw[offset+9:offset+9+length]
        rows.append(dict(type=kind,flags=flags,stream=stream,length=length,payload_hex=payload.hex(),error_code=int.from_bytes(payload[4:8],'big') if kind==7 and length>=8 else int.from_bytes(payload,'big') if kind==3 and length==4 else None))
        offset+=9+length
    return rows,raw[offset:].hex()
def measure(port,packet,window):
    began=time.perf_counter();raw=b'';ended='observation deadline'
    with socket.create_connection(('127.0.0.1',port),timeout=2) as client:
        sent=PREFACE+frame(4)+packet;client.sendall(sent)
        deadline=time.monotonic()+window
        while time.monotonic()<deadline:
            client.settimeout(max(.001,deadline-time.monotonic()))
            try:
                chunk=client.recv(65536)
                if not chunk:ended='peer closed';break
                raw+=chunk
            except socket.timeout:break
            except ConnectionResetError:ended='peer reset';break
    frames,trailing=parse(raw)
    goaway=next((f for f in frames if f['type']==7 and f['error_code']!=0),None)
    outcome='goaway' if goaway else 'response' if any(f['type']==0 and bytes.fromhex(f['payload_hex'])==b'loopback-control' for f in frames) else 'ping-ack' if any(f['type']==6 and f['flags']&1 for f in frames) else 'unknown'
    return dict(sent_hex=sent.hex(),received_hex=raw.hex(),sent_bytes=len(sent),received_bytes=len(raw),frames=frames,trailing_hex=trailing,outcome=outcome,error_code=goaway['error_code'] if goaway else None,ended=ended,elapsed_seconds=time.perf_counter()-began)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--pilot',action='store_true');args=ap.parse_args()
    version='pilot-v1' if args.pilot else 'main-v1'
    protocol=ROOT/f'protocol-loopback-{version}.json';p=json.loads(protocol.read_text())
    identity=dict(protocol_sha256=sha(protocol),runner_sha256=sha(ROOT/'loopback.py'),server_sha256=sha(ROOT/'loopback-server.mjs'),node_sha256=sha(Path(NODE)),frozen_at=now(),os=platform.platform(),machine=platform.machine())
    lock=ROOT/f'protocol-lock-loopback-{version}.json'
    if lock.exists():
        old=json.loads(lock.read_text());assert all(old[k]==identity[k] for k in ('protocol_sha256','runner_sha256','server_sha256','node_sha256')),'Frozen implementation changed'
    else:lock.write_text(json.dumps(identity,indent=2)+'\n')
    rows=[];processes=[]
    for replication in range(p['replications']):
        stderr=ROOT/'results/raw'/f'{version}-{replication}.stderr';stderr.parent.mkdir(parents=True,exist_ok=True)
        with stderr.open('w') as log:
            proc=subprocess.Popen([NODE,str(ROOT/'loopback-server.mjs')],stdout=subprocess.PIPE,stderr=log,text=True)
            try:
                selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ)
                if not selector.select(timeout=3):raise RuntimeError('Endpoint startup deadline')
                info=json.loads(proc.stdout.readline());processes.append(dict(replication=replication,versions=info['versions'],requested_processes=1,actual_processes=1))
                tasks=[(case,w) for case in cases() for w in p['observation_windows_seconds']];random.Random(p['seed']+replication).shuffle(tasks)
                for (name,packet,expected,code,section),window in tasks:
                    row=dict(id=f'{version}-{replication}-{name}-{window}',replication=replication,case=name,window_seconds=window,expected=expected,expected_error_code=code,rfc_section=section,started_at=now())
                    try:
                        row.update(measure(info['port'],packet,window));row['status']='ambiguous' if row['outcome']=='unknown' else 'recorded';row['agrees']=None if row['outcome']=='unknown' else row['outcome']==expected and row['error_code']==code
                    except Exception as error:row.update(status='failed',reason=str(error),agrees=None)
                    row['ended_at']=now();row['raw_sha256']=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest();rows.append(row)
            finally:proc.terminate();proc.wait(timeout=3)
    report=dict(id=p['id'],stage=p['stage'],protocol=p,provenance=identity,processes=processes,measurements=rows,planned=len(cases())*p['replications']*len(p['observation_windows_seconds']),recorded=len(rows),costs=dict(incremental_purchased_compute_usd=0,workstation_allocation_cost_usd=None),summary=dict(agreement=sum(r.get('agrees') is True for r in rows),disagreement=sum(r.get('agrees') is False for r in rows),unknown_or_failed=sum(r.get('agrees') is None for r in rows)))
    (ROOT/'evidence'/f'loopback-{version}.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(id=report['id'],planned=report['planned'],summary=report['summary'])))
if __name__=='__main__':main()
