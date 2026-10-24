#!/usr/bin/env python3
"""Explicit online setup command for pinned public artifacts; inference never downloads."""
import argparse,json,hashlib,os,tempfile
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();config=json.loads((ROOT/'config/semantic-model.json').read_text());m=config['manifest'];a.output.mkdir(parents=True,exist_ok=True);a.output.chmod(0o700)
 base=config['repository']+'/resolve/'+m['revision']+'/'
 downloads={name:{'url':base+remote,'sha256':m['files'][name]} for name,remote in config['download_files'].items()}
 downloads.update(config.get('additional_files',{}))
 for name,item in downloads.items():
  if Path(name).name!=name:raise ValueError('Unsafe model filename')
  dest=a.output/name
  if dest.exists():
   if hashlib.sha256(dest.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Existing model artifact differs: '+name)
   continue
  fd,temporary=tempfile.mkstemp(prefix=name+'.',dir=a.output)
  try:
   h=hashlib.sha256();size=0
   with os.fdopen(fd,'wb') as f,urlopen(Request(item['url'],headers={'User-Agent':'STS semantic setup'}),timeout=90) as response:
    os.fchmod(f.fileno(),0o600)
    while block:=response.read(1024*1024):
     size+=len(block)
     if size>512*1024*1024:raise ValueError('Embedding artifact too large')
     h.update(block);f.write(block)
   if h.hexdigest()!=item['sha256']:raise ValueError('Downloaded embedding checksum failed')
   os.replace(temporary,dest)
  finally:
   if os.path.exists(temporary):os.unlink(temporary)
  print('Verified',name,flush=True)
 (a.output/'manifest.json').write_text(json.dumps(m,indent=2)+'\n');(a.output/'manifest.json').chmod(0o600)
 print('Pinned offline model ready; license: Apache-2.0; source:',config['repository'])
if __name__=='__main__':main()
