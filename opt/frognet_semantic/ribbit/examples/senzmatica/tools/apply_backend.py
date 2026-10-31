#!/usr/bin/env python3
from pathlib import Path
import argparse,subprocess,shutil
p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('--repair',action='store_true');a=p.parse_args();source=a.source.resolve();here=Path(__file__).resolve().parents[1]
pin='1bdf0dcea6d59d5b6651d14e076cff6dfdb512e4'
assert subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()==pin
assert not subprocess.check_output(['git','-C',str(source),'diff','HEAD','--'],text=True)
base=source/'core-lib/src/main/java'
for tree in ['backend-overlay','java']:
 for file in (here/tree).rglob('*.java'):
  dest=base/file.relative_to(here/tree);assert not dest.exists(),str(dest);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,dest)
p=base/'com/magma/core/service/CoreService.java';original=p.read_text();s=original
s=s.replace('    @Autowired\n    VerneMqService verneMqService;','    @Autowired\n    RibbitConfigurationService ribbitConfiguration;\n\n    @Autowired\n    VerneMqService verneMqService;',1)
s=s.replace('        LOGGER.debug("Update Device\'s Remote Configurations for Device Id: {}", deviceId);','        final boolean useRibbit = ribbitConfiguration.isEnabled();\n        LOGGER.info("REMOTE_CONFIG_PATH device={} path={}", deviceId, useRibbit ? "RAM" : "MQTT");\n        LOGGER.debug("Update Device\'s Remote Configurations for Device Id: {}", deviceId);',1)
s=s.replace('            Collections.sort(modifiedIds);','            Collections.sort(modifiedIds);\n            if (useRibbit) return ribbitConfiguration.submit(requestedDevice, newRemoteConfigurations, modifiedIds, userId);',1)
assert s!=original
if a.repair:
 s=s.replace('                            mqttGateway.send(topic, retained, chunk);\n','',1)
 s=s.replace('                            deviceRepository.save(requestedDevice);\n','                            deviceRepository.save(requestedDevice);\n                            mqttGateway.send(topic, retained, chunk);\n',1)
 s=s.replace('                        mqttGateway.send(topic, retained, completeMessage);\n','',1)
 s=s.replace('                        deviceRepository.save(requestedDevice);\n                    }','                        deviceRepository.save(requestedDevice);\n                        mqttGateway.send(topic, retained, completeMessage);\n                    }',1)
p.write_text(s)
p=base/'com/magma/core/gateway/MQTTMessageHandler.java';original=p.read_text();s=original.replace('import com.magma.core.service.CoreService;','import com.magma.core.service.CoreService;\nimport com.magma.core.service.RibbitConfigurationService;',1)
s=s.replace('    @Autowired\n    CoreService coreService;','    @Autowired\n    CoreService coreService;\n\n    @Autowired\n    RibbitConfigurationService ribbitConfiguration;',1)
s=s.replace('        if (topic.contains("V1")) {','        if (topic.startsWith(RibbitConfigurationService.COMPLETION_PREFIX)) {\n            return "RIBBIT_CONFIGURATION";\n        } else if (topic.contains("V1")) {',1)
s=s.replace('        switch (messageType) {','        switch (messageType) {\n            case "RIBBIT_CONFIGURATION":\n                ribbitConfiguration.handleCompletion(topic, messageContent);\n                break;',1)
assert s!=original;p.write_text(s)
print('Applied primary conversion'+(' plus explicit MQTT ordering repair' if a.repair else '; original MQTT send order retained'))
