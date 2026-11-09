"""Deterministic child-process boundary shared by benchmark unit suites."""
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


class Process:
    def __init__(self, command, clock, *, startup=True, failure=None):
        self.command, self.clock = command, clock
        self.mode = command[command.index('--mode') + 1]
        self.name = command[command.index('--name') + 1] if self.mode == 'receiver' else command[command.index('--targets') + 1].split(':')[0]
        self.returncode = 7 if failure == self.mode else 0
        self.killed = False
        if self.mode == 'receiver' and startup:
            Path(command[command.index('--ready-file') + 1]).touch()
    def poll(self): return self.returncode if self.clock[0] >= 0.15 else None
    def kill(self): self.killed = True
    def communicate(self, timeout):
        if timeout != 10: raise AssertionError('Completion wait must remain bounded')
        value = {'mode': 'sender', 'targets': {self.name: {'bytes_sent': 1000, 'seconds': 0.15}}, 'total_seconds': 0.15} if self.mode == 'sender' else {'name': self.name, 'bytes_received': 1000}
        return json.dumps(value), 'synthetic process failure' if self.returncode else ''


def simulate(bench, source, *, startup=True, failure=None):
    clock = [0.0]; processes = []
    def launch(command, **kwargs):
        if kwargs['text'] is not True: raise AssertionError('Expected text-mode child output')
        process = Process(command, clock, startup=startup, failure=failure)
        processes.append(process); return process
    def sleep(seconds): clock[0] = round(clock[0] + seconds, 6)
    def counter(path):
        if path.endswith('.rx_bytes'): return int(clock[0] * (400000 if 'client1.' in path else 600000))
        return 12 if path.endswith('.in_flight_packets') else 18000
    def command_output(command, **kwargs):
        if '/statistics/rx_bytes' in command:
            return str(int(clock[0] * (400000 if 'veth_out1/' in command else 600000)))
        return f'qdisc netem 1: root\n Sent {int(clock[0]*1000000)} bytes 4 pkt\n backlog 1250b 1p'
    ss = MagicMock(); ss.stop.return_value = [{'snapshot_index': 1, 'fixture': 'retained synthetic SS snapshot'}]
    with patch.object(source.subprocess, 'Popen', side_effect=launch), patch.object(source.time, 'time', side_effect=lambda: clock[0]), \
            patch.object(source.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(source.time, 'sleep', side_effect=sleep), \
            patch.object(source, 'read_counter_file', side_effect=counter), patch.object(source, 'read_float_file', return_value=21.5), \
            patch.object(source, 'run_and_grab_shell', side_effect=command_output), \
            patch.object(source, 'SSSampler', return_value=ss), patch.object(source, 'resolve_ss_log_path', return_value='/synthetic/ss.jsonl'), \
            contextlib.redirect_stdout(io.StringIO()):
        try: return bench.run_benchmark(), processes, ss
        except RuntimeError as error: return error, processes, ss
