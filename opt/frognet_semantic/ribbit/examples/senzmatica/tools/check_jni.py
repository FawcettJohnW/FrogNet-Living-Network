from pathlib import Path
import argparse,os,subprocess,json
p=argparse.ArgumentParser();p.add_argument('--state',type=Path,required=True);a=p.parse_args();a.state.mkdir(parents=True,exist_ok=True)
here=Path(__file__).resolve().parents[1];build=here/'build'
with (a.state/'host.log').open('w') as log:
 host=subprocess.Popen([str(build/'test-host'),'18884'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True)
 try:
  assert host.stdout.readline().strip()=='READY'
  r=subprocess.run([str(Path(os.environ['JAVA_HOME'])/'bin/java'),'-Xcheck:jni','-Djava.library.path='+str(build),'-cp',str(build/'classes'),'BindingTest','127.0.0.1:18884'],capture_output=True,text=True,timeout=30)
  (a.state/'java.log').write_text(r.stdout+r.stderr);print(r.stdout+r.stderr)
  assert r.returncode==0 and 'RESULT PASS JNI binding (23 checks)' in r.stdout
 finally:
  if host.poll() is None:host.communicate('\n',timeout=10)
