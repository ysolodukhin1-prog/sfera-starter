"""Operational counters only. Never records identity, headers or request bodies."""
import json
import os
import threading
import time
from pathlib import Path
import fcntl

_lock=threading.Lock()
_epoch=f'{time.time_ns()}-{os.getpid()}'
_state={'epoch':_epoch,'started_at':time.time(),'requests':0,'errors':0,'duration_ms':0,
        'model_calls':0,'input_tokens':0,'output_tokens':0,'usage_missing':0}

def record(duration_ms=0,error=False,model_usage=None,model_call=False):
    try:
        with _lock:
            path=Path('/telemetry/counter.json')
            if not path.parent.exists():path=Path('/tmp/galactica-telemetry.json')
            with path.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                try:
                    saved=json.loads(path.read_text())
                    _state.update({key:saved[key] for key in _state if key in saved})
                except (OSError,ValueError):pass
                _record(path,duration_ms,error,model_usage,model_call)
    except (OSError,ValueError,TypeError):pass

def _record(path,duration_ms,error,model_usage,model_call):
            if model_call:
                _state['model_calls']+=1
                usage=model_usage or {}
                if 'prompt_tokens' in usage and 'completion_tokens' in usage:
                    _state['input_tokens']+=max(0,int(usage['prompt_tokens']))
                    _state['output_tokens']+=max(0,int(usage['completion_tokens']))
                else:_state['usage_missing']+=1
            else:
                _state['requests']+=1
                _state['errors']+=int(bool(error))
                _state['duration_ms']+=max(0,float(duration_ms))
            temp=path.with_suffix('.next');temp.write_text(json.dumps(_state));temp.replace(path)
