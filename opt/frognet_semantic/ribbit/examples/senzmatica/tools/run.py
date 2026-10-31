#!/usr/bin/env python3
"""Actual Java/Mongo/MQTT/native-RAM workflow; synthetic device, no mock backend."""
from pathlib import Path
import argparse,json,os,socket,subprocess,time,urllib.request,urllib.error
p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);p.add_argument('--state',type=Path,required=True);p.add_argument('--mode',choices=['original','ram','mixed'],required=True);a=p.parse_args()
w=a.workspace.resolve();state=a.state.resolve();state.mkdir(parents=True,exist_ok=True)
assert not (state/'result.json').exists(),'Use a fresh run directory'
here=Path(__file__).resolve().parents[1];repo=here
for _ in range(5):repo=repo.parent
stock=here.parent/'mqtt/stock';build=here/'build';fixtures=here/'fixtures'
source=w/('senzmatica-core' if a.mode=='original' else 'senzmatica-converted' if a.mode=='ram' else 'senzmatica-converted-repaired')
pin='1bdf0dcea6d59d5b6651d14e076cff6dfdb512e4'
subprocess.run(['git','-C',str(source),'merge-base','--is-ancestor',pin,'HEAD'],check=True)
if a.mode=='original':
 assert subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()==pin
 assert not subprocess.check_output(['git','-C',str(source),'diff','HEAD','--'],text=True)
java=w/'baseline-tools/zulu8.98.0.107-ca-jdk8.0.512-linux_x64/bin/java';mongo=w/'baseline-tools/mongodb-linux-x86_64-ubuntu2004-5.0.31/bin'
env=os.environ.copy();env['LD_LIBRARY_PATH']=str(w/'baseline-tools/openssl11/usr/lib/x86_64-linux-gnu')+':'+str(stock/'lib')
endpoint='http://127.0.0.1:18080/core/device/deviceParameterConfiguration';urlopen=urllib.request.build_opener(urllib.request.ProxyHandler({})).open
props={'server.address':'127.0.0.1','server.port':'18080','spring.application.name':'senz-baseline','spring.data.mongodb.host':'127.0.0.1','spring.data.mongodb.port':'27027','spring.data.mongodb.database':'senz-baseline','project.version':'1.0.0.0','server.resource.id':'test','mqtt.broker.address':'tcp://127.0.0.1:18883','mqtt.client.id':'baseline-server','mqtt.client.username':'test','mqtt.client.password':'test','mqtt.sub.topic':'D2S/SA/#','mqtt.res.topic':'S2D/SA/#','mqtt.old.topic':'','mqtt.pub.topic':'S2D/SA/V1/','magma.front.pub.topic':'S2F/M/V1/','mqtt.sub.qos':'1','mqtt.pool.size':'2','mqtt.max.pool.size':'4','mqtt.que.size':'2500','mail.transport.protocol':'smtp','mail.smtp.auth':'false','mail.smtp.starttls.enable':'false','mail.debug':'false','spring.mail.host':'127.0.0.1','spring.mail.port':'2525','device.data.interval':'30','device.data.battery.property.number':'-10','device.data.countAnalyseDeviceSummary':'10','device.offline.interval':'600000','magma.deviceSummary.fixedRate':'600000','security.basic.enabled':'false','management.security.enabled':'false','grpc.server.port':'19092','senzagro.grpc.port':'19093','senzagro.grpc.host':'127.0.0.1','ums.service.host':'127.0.0.1','ums.service.port':'19090','bin.url':'http://127.0.0.1:18080/','bin.directory':str(state/'firmware'),'eureka.client.enabled':'false','spring.cloud.discovery.enabled':'false','azure.oauth.client.id':'test','azure.oauth.client.secret':'test','azure.ouath.tenet.id':'test','azure.oauth.token.uri':'http://127.0.0.1:19090/','azure.oauth.graph.uri':'http://127.0.0.1:19090/'}
if a.mode!='original':props['ribbit.address']='127.0.0.1:18884'
(state/'local.properties').write_text(''.join(k+'='+v+'\n' for k,v in props.items()));(state/'mongo').mkdir(exist_ok=True)
(state/'mosquitto.conf').write_text(('user root\n' if os.geteuid()==0 else '')+'listener 18883 127.0.0.1\nallow_anonymous true\npersistence false\nlog_type all\n')
processes=[];logs=[];result={'mode':a.mode,'broker':'Mosquitto 2.1.2 (authorized substitution)','checks':[],'upstream_unchanged':a.mode=='original'}
def save(name,value):
 (state/name).write_text(json.dumps(value,indent=2)+'\n')
def check(ok,label):
 if not ok:raise AssertionError(label)
 result['checks'].append(label);print('PASS '+label,flush=True)
def start(cmd,name):
 log=(state/(name+'.log')).open('w');logs.append(log);child=subprocess.Popen([str(x) for x in cmd],env=env,cwd=state,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT);processes.append(child);return child
def wait(predicate,label,timeout=10):
 deadline=time.monotonic()+timeout
 while True:
  value=predicate()
  if value:return value
  if time.monotonic()>deadline:raise AssertionError(label)
  time.sleep(.02) # bootstrap/oracle observation, never a reported latency sample

def port(number,child):
 def ready():
  if child.poll() is not None:raise RuntimeError('Process exited before port '+str(number)+'; see logs')
  try:
   with socket.create_connection(('127.0.0.1',number),.1):return True
  except OSError:return False
 wait(ready,'startup port '+str(number),90)
def shell(js):
 r=subprocess.run([str(mongo/'mongo'),'--quiet','--host','127.0.0.1','--port','27027','senz-baseline','--eval',js],env=env,capture_output=True,text=True,timeout=15)
 if r.returncode:raise RuntimeError(r.stdout+r.stderr)
 return r.stdout.strip()
def snapshot():return json.loads(shell('print(JSON.stringify({device:db.device.findOne({_id:"test-device-001"}),messages:db.message.find({device:"test-device-001"}).toArray()}))'))
def config(s):return s['device']['deviceParameterConfiguration']
def http(url,body=None):
 req=urllib.request.Request(url,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='GET' if body is None else 'PUT')
 with urlopen(req,timeout=10) as response:return json.loads(response.read())
def change(value):
 body=json.loads((fixtures/'change.json').read_text());body['remoteConfigurations'][0]['defaultValue']=str(value)
 return http(endpoint+'/test-device-001/user/test-operator',body)
def route(value):assert http('http://127.0.0.1:18080/core/device/remoteConfiguration/path',{'ram':value})['ram']==value
def applied(value):
 def probe():
  s=snapshot();return s if config(s)['remoteConfigurations'][0]['defaultValue']==str(value) and not s['messages'] else None
 return wait(probe,'applied legacy value not observed: '+str(value))
def publish(topic,payload):subprocess.run([str(stock/'client/mosquitto_pub'),'-h','127.0.0.1','-p','18883','-q','1','-t',topic,'-m',payload],env=env,check=True,timeout=10)
def events():return (state/'backend.log').read_text()
def log_event(text,count=1):wait(lambda:events().count(text)>=count,'missing backend event '+text)
def commands():return [line for line in (state/'broker.log').read_text().splitlines() if 'Received PUBLISH from baseline-server_' in line and "'S2D/SA/test-device-001/RM/" in line]
try:
 m=start([mongo/'mongod','--nounixsocket','--bind_ip','127.0.0.1','--port','27027','--dbpath',state/'mongo','--setParameter','diagnosticDataCollectionEnabled=false'],'mongo');port(27027,m)
 broker=start([stock/'src/mosquitto','-c',state/'mosquitto.conf'],'broker');port(18883,broker)
 if a.mode!='original':host=start([build/'test-host','18884'],'ram-host');port(18884,host)
 app=start([java,'-Djava.library.path='+str(build),'-Dloader.path='+str(source/'lib'),'-cp',source/'core-service/target/core-service-1.0.0.0.jar','org.springframework.boot.loader.PropertiesLauncher','--spring.config.location='+str(state/'local.properties')],'backend');port(18080,app)
 def api_ready():
  try:return http(endpoint)
  except OSError:
   if app.poll() is not None:raise
   return None
 ready=wait(api_ready,'configuration GET not ready',30);save('ready.json',ready);check(ready['statusCode']=='S1000','configuration API responds')
 initial=json.loads((fixtures/'initial.json').read_text());device=initial['device'];device['_id']=device.pop('id');product=initial['productType'];product['_id']='test-product-001'
 shell('db.dropDatabase();db.device.insert('+json.dumps(device)+');db.productType.insert('+json.dumps(product)+');')
 (state/'device-setting.txt').write_text('30')
 if a.mode!='original':participant=start([build/'test-device','127.0.0.1:18884','18883',state/'device-setting.txt'],'device')
 if a.mode=='ram':route(True)
 sent=change(60);save('change-response.json',sent);assert sent['statusCode']=='S1000'
 if a.mode!='ram':
  received=subprocess.run([str(stock/'client/mosquitto_sub'),'-h','127.0.0.1','-p','18883','-t','S2D/SA/test-device-001/RM/#','-C','1','-W','10','-v'],env=env,capture_output=True,text=True,timeout=15)
  assert received.returncode==0,received.stderr
  (state/'outbound-mqtt.txt').write_text(received.stdout);topic,payload=received.stdout.strip().split(' ',1)
  check(topic.startswith('S2D/SA/test-device-001/RM/'),'original MQTT command observed');check(payload=='RM|1-60;|END','original encoder bytes match')
  if a.mode=='original':
   (state/'device-setting.txt').write_text('60');publish('D2S/SA/test-device-001/RM/'+topic.rsplit('/',1)[1],'rm-conf-successful')
 current=applied(60);save('applied-record.json',current)
 view=http(endpoint);entries=[x for x in view['content'] if x['id']=='test-device-001'];save('configuration-view.json',view)
 check(len(entries)==1 and entries[0]['deviceParameterConfiguration']['remoteConfigurations'][0]['defaultValue']=='60' and entries[0]['lastUpdateStatus']=='UPDATED','frontend API exposes applied value and UPDATED status')
 check(config(current)['updateHistory'][-1]['updateStatus']=='UPDATED','completion updates value/history and removes pending Message')
 if a.mode=='ram':check(len(commands())==0 and 'D2S/SA/RIBBIT_CONFIG/test-device-001/1' in (state/'broker.log').read_text() and (state/'device-setting.txt').read_text()=='60','RAM applies setting and publishes final MQTT completion without old command')
 try:change('not-an-integer');raise AssertionError('invalid integer accepted')
 except urllib.error.HTTPError as error:
  rejected=json.loads(error.read());save('invalid-response.json',rejected);assert error.code==422 and rejected['errorCode']=='E496'
 unchanged=snapshot();check(config(unchanged)['remoteConfigurations'][0]['defaultValue']=='60' and not unchanged['messages'],'invalid integer rejected without state change')
 change(60);unchanged=snapshot();check(len(config(unchanged)['updateHistory'])==1 and not unchanged['messages'],'unchanged setting adds no history or pending Message')
 if a.mode=='ram':
  errorlog=(state/'probe.log').open('w');logs.append(errorlog)
  probe=subprocess.Popen([str(build/'probe'),'127.0.0.1:18884'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errorlog,text=True,env=env,cwd=state);processes.append(probe);assert probe.stdout.readline().strip()=='READY'
  def read_ram(family,op='get'):
   probe.stdin.write(json.dumps({'op':op,'a':'senz-baseline','b':family,'c':'1'})+'\n');probe.stdin.flush();return json.loads(probe.stdout.readline())
  wanted=read_ram('desired-config|test-device-001')['rows'][0]['bag'];completion={'device':'test-device-001','parameter':'1','generation':wanted['generation'],'value':'60','status':'applied'}
  publish('D2S/SA/RIBBIT_CONFIG/test-device-001/1',json.dumps(completion));log_event('RAM_COMPATIBILITY_CONSUMED',2)
  check(len(config(snapshot())['updateHistory'])==1,'duplicate completion added history' if len(config(snapshot())['updateHistory'])!=1 else 'duplicate completion has no repeated history')
  participant.stdin.write(b'reobserve\n');participant.stdin.flush();log_event('RAM_COMPATIBILITY_CONSUMED',3)
  observed=[line for line in (state/'device.log').read_text().splitlines() if line.startswith('APPLIED ')]
  check(len(observed)==2 and observed[-1].endswith('applications=1'),'duplicate observation repeated physical application' if not(len(observed)==2 and observed[-1].endswith('applications=1')) else 'reobserving desired state causes no repeated physical application')
  before=events();ignored=before.count('RAM_COMPLETION_IGNORED');consumed=before.count('RAM_COMPATIBILITY_CONSUMED');completion['generation']='old-generation'
  publish('D2S/SA/RIBBIT_CONFIG/test-device-001/1',json.dumps(completion))
  wait(lambda:events().count('RAM_COMPLETION_IGNORED')>ignored or events().count('RAM_COMPATIBILITY_CONSUMED')>consumed,'no completion consumption event')
  check(len(config(snapshot())['updateHistory'])==1,'stale completion added history' if len(config(snapshot())['updateHistory'])!=1 else 'stale completion cannot alter legacy history')
  shell('db.sensorCode.insert({_id:"test-temp",codeValue:"T"});db.device.update({_id:"test-device-001"},{$set:{sensorCodes:["test-temp"]}})');publish('D2S/SA/V1/test-device-001','T:21.5')
  wait(lambda:int(shell('print(db.sensor.count({deviceId:"test-device-001",code:"test-temp",value:"21.5"}))'))==1,'telemetry not persisted')
  check(True,'unconverted MQTT telemetry persists the actual sensor value')
  participant.terminate();participant.wait(timeout=10);gone=read_ram('capability|test-device-001','absent');check(gone['matched'] and not gone['rows'],'session loss removes capability')
  change(120);change(150);waiting=snapshot();check(config(waiting)['remoteConfigurations'][0]['defaultValue']=='60' and len(config(waiting)['updateHistory'])==1,'changes while absent do not create false applied history')
  participant=start([build/'test-device','127.0.0.1:18884','18883',state/'device-setting.txt'],'device-return');returned=applied(150);save('returned-record.json',returned)
  trace=(state/'device-return.log').read_text()
  check(len(config(returned)['updateHistory'])==2 and (state/'device-setting.txt').read_text()=='150' and 'value=120' not in trace and 'value=150 applications=1' in trace and len(read_ram('capability|test-device-001')['rows'])==1,'returning device applies only latest pending value once')
 if a.mode=='mixed':
  route(True);change(90);ram_state=applied(90);save('ram-record.json',ram_state)
  check(len(config(ram_state)['updateHistory'])==2 and (state/'device-setting.txt').read_text()=='90','switch ON applies through RAM and updates existing view')
  check(len(commands())==1 and 'D2S/SA/RIBBIT_CONFIG/test-device-001/1' in (state/'broker.log').read_text(),'RAM path has final MQTT completion and no old command')
  route(False);change(120);off=applied(120)
  check(len(config(off)['updateHistory'])==3 and len(commands())==2 and (state/'device-setting.txt').read_text()=='120','switch OFF returns to original MQTT path')
  actions=[line for line in (state/'device.log').read_text().splitlines() if line.startswith('APPLIED ')];check(len(actions)==3 and actions[-1].endswith('applications=3'),'three changes cause three test-setting applications')
  participant.terminate();participant.wait(timeout=10);participant=start([build/'test-device','127.0.0.1:18884','18883',state/'device-setting.txt'],'device-after-off')
  wait(lambda:'APPLIED path=MQTT value=120' in (state/'device-after-off.log').read_text(),'retained MQTT state not observed')
  actions=[line for line in (state/'device-after-off.log').read_text().splitlines() if line.startswith('APPLIED ')]
  check(all(line.endswith('applications=0') for line in actions) and (state/'device-setting.txt').read_text()=='120','restart after OFF does not replay completed RAM intent')
 result['status']='PASS'
except BaseException as error:
 result['status']='FAIL';result['error']=str(error);raise
finally:
 for child in reversed(processes):
  if child.poll() is None:child.terminate()
 for child in reversed(processes):
  try:child.wait(timeout=10)
  except subprocess.TimeoutExpired:child.kill();child.wait()
 for log in logs:log.close()
 save('result.json',result);print(json.dumps(result),flush=True)
