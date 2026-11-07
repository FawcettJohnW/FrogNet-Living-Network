#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""Reproducible local Maven invocation, using this invocation's proxy and CA."""
from pathlib import Path
import argparse,os,subprocess,urllib.parse,shutil,json,datetime,hashlib
p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--state',type=Path,required=True);p.add_argument('--install-util',action='store_true');a=p.parse_args()
w=a.workspace.resolve();src=a.source.resolve();state=a.state.resolve();state.mkdir(parents=True,exist_ok=True)
jdk=w/'baseline-tools/zulu8.98.0.107-ca-jdk8.0.512-linux_x64';env=os.environ.copy();env['JAVA_HOME']=str(jdk)
trust=state/'cacerts';shutil.copyfile(jdk/'jre/lib/security/cacerts',trust)
with (state/'trust.log').open('w') as log:subprocess.run([str(jdk/'bin/keytool'),'-importcert','-noprompt','-alias','workspace-proxy','-file','/usr/local/share/ca-certificates/nebula-dns.crt','-keystore',str(trust),'-storepass','changeit'],stdout=log,stderr=subprocess.STDOUT,check=True)
proxy=urllib.parse.urlparse(os.environ['HTTPS_PROXY']);settings=state/'settings.xml'
settings.write_text(f'<settings><proxies><proxy><id>workspace</id><active>true</active><protocol>http</protocol><host>{proxy.hostname}</host><port>{proxy.port}</port><nonProxyHosts>localhost|127.0.0.1</nonProxyHosts></proxy></proxies></settings>')
args=[str(w/'baseline-tools/apache-maven-3.9.9/bin/mvn'),'-s',str(settings),'-B','-Dmaven.repo.local='+str(w/'recovery-state/m2'),'-Djavax.net.ssl.trustStore='+str(trust),'-Djavax.net.ssl.trustStorePassword=changeit']
with (state/'build.log').open('w') as log:
 if a.install_util:subprocess.run(args+['install:install-file','-Dfile='+str(src/'lib/util-service-1.0.0.5.jar'),'-DgroupId=com.magma.util','-DartifactId=util-service','-Dversion=1.0.0.5','-Dpackaging=jar','-DgeneratePom=true'],cwd=state,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
 r=subprocess.run(args+['package'],cwd=src,env=env,stdout=log,stderr=subprocess.STDOUT)
result={'source':str(src),'commit':subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip(),'exit':r.returncode}
jar=src/'core-service/target/core-service-1.0.0.0.jar'
if r.returncode==0:result['jar_sha256']=hashlib.sha256(jar.read_bytes()).hexdigest()
(state/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));raise SystemExit(r.returncode)
