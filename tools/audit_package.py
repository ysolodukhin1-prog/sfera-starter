"""Audit tracked publication files, not generated client secrets or live data."""
import ast,hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 names=subprocess.check_output(['git','ls-files'],cwd=ROOT,text=True).splitlines()
 forbidden=[n for n in names if n.startswith(('upstream/','base/','instance/')) or n in {'.env','schema/current.sql','SOURCE_PROVENANCE.json'} or n.endswith(('.dump','.log','.zip'))]
 if forbidden:raise ValueError('Excluded material is tracked: '+','.join(forbidden))
 token=re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|sk-proj-[A-Za-z0-9_-]{30,}')
 dsn=re.compile(r'(?:postgres(?:ql)?|mysql)://[^\s:/]+:[^\s@]+@')
 for name in names:
  data=(ROOT/name).read_bytes()
  if token.search(data):raise ValueError('Secret-like token in '+name)
  if name.endswith('.py'):
   tree=ast.parse(data,filename=name)
   values=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
  else:values=[re.sub(r'\$\{[^}]+\}','',data.decode('utf-8'))]
  if any(dsn.search(v) for v in values):raise ValueError('Literal database credential in '+name)
 snapshot=(ROOT/'schema/001_snapshot.sql.in').read_text(encoding='utf-8')
 if re.search(r'^COPY .* FROM stdin|^SELECT pg_catalog\.setval',snapshot,re.M):raise ValueError('Schema snapshot contains exported rows or sequence state')
 manifest={n:hashlib.sha256((ROOT/n).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for n in names if n not in {'SOURCE_MANIFEST.json','TEST_RESULTS.json'}}
 (ROOT/'SOURCE_MANIFEST.json').write_text(json.dumps({'product':'Sfera standalone','version':'0.1.0-rc.1','snapshot_date':'2026-10-05','schema_only':True,'source_hashes':manifest},indent=2)+'\n',encoding='utf-8',newline='\n')
 print('Publication audit passed: '+str(len(names))+' tracked files; no credentials, raw exports or database rows.')
if __name__=='__main__':main()
