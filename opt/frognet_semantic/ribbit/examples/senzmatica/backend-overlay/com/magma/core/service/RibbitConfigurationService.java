package com.magma.core.service;
import com.fawcett.ribbit.Ribbit;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.magma.core.configuration.MQTTConfiguration;
import com.magma.core.data.entity.Device;
import com.magma.core.data.repository.DeviceRepository;
import com.magma.core.data.support.*;
import com.magma.core.util.UpdateStatus;
import org.joda.time.DateTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.data.mongodb.core.query.Update;
import org.springframework.stereotype.Service;
import javax.annotation.PostConstruct;
import java.util.*;
import java.util.concurrent.atomic.AtomicLong;

@Service
public class RibbitConfigurationService {
    public static final String COMPLETION_PREFIX="D2S/SA/RIBBIT_CONFIG/";
    private static final Logger LOG=LoggerFactory.getLogger(RibbitConfigurationService.class);
    private final ObjectMapper json=new ObjectMapper();
    private final String incarnation=UUID.randomUUID().toString();
    private final AtomicLong sequence=new AtomicLong();
    private volatile boolean enabled;
    private volatile Ribbit ram;
    // This operator's own truths: the latest desired bag per (device, parameter). Reasserted on every fresh session,
    // because a session's writes go with it ([GONE_WITH_ITS_WRITER_V1]). Not a copy of anyone else's tuples.
    private final java.util.concurrent.ConcurrentHashMap<String,String[]> owned=new java.util.concurrent.ConcurrentHashMap<>();
    private final java.util.concurrent.atomic.AtomicInteger callbackFailures=new java.util.concurrent.atomic.AtomicInteger();
    private static final long FRESH_SESSION_MS=2000; // same cadence as fdf-client's RestartSec=2
    @Value("${ribbit.address:}")private String address;
    @Value("${ribbit.space:senz-baseline}")private String space;
    @Autowired private MQTTConfiguration.MqttGateway mqtt;
    @Autowired private DeviceRepository devices;
    @Autowired private MongoTemplate mongo;
    @PostConstruct public void start(){
        LOG.info("REMOTE_CONFIG_SWITCH ram=false");
        if(address.isEmpty())return;
        Thread session=new Thread(this::sessions,"ribbit-session");session.setDaemon(true);session.start();
    }
    // [FRESH_SESSION_ON_END_V1] One session at a time. Its held read on this space is how its end is seen: a read that
    // fails means the session is gone. Then: no RAM path (submits fail visibly, never fall back to MQTT), a fresh
    // session after FRESH_SESSION_MS, this operator's own desired truths written again, and the space read from the
    // start so every applied result is seen again (completion is idempotent by its history marker).
    private void sessions(){
        for(;;){
            Ribbit session=null;
            try{
                session=new Ribbit(address,"senz-binding");
                for(String[] truth:owned.values())session.Set(space,truth[0],truth[1],truth[2]);
                ram=session;
                LOG.info("RAM_SESSION up address={} reasserted={}",address,owned.size());
                long after=0;
                for(;;){
                    String batch=session.HeldRead(space,"","",after,25);
                    after=json.readTree(batch).path("after").asLong(after);
                    try{applied(batch);}catch(RuntimeException error){callbackFailures.incrementAndGet();LOG.error("RAM applied-result handling failed",error);}
                }
            }catch(Exception error){
                ram=null;
                LOG.error("RAM_SESSION ended: {} -- no RAM path until a fresh session; next attempt in {} ms",error.getMessage(),FRESH_SESSION_MS);
                if(session!=null)try{session.DisconnectRibbit();}catch(RuntimeException ignored){}
            }
            try{Thread.sleep(FRESH_SESSION_MS);}catch(InterruptedException stop){Thread.currentThread().interrupt();return;}
        }
    }
    public boolean isEnabled(){return enabled;}
    public void setEnabled(boolean value){enabled=value;LOG.info("REMOTE_CONFIG_SWITCH ram={} time={}",value,DateTime.now());}
    public String submit(Device device,List<RemoteConfigField> fields,List<String> modified,String user){
        final Ribbit ram=this.ram;
        if(ram==null)throw new IllegalStateException("RAM is unavailable; no MQTT fallback");
        if(callbackFailures.get()!=0)throw new IllegalStateException("A RAM applied-result handler failed; see the log");
        for(String id:modified){
            RemoteConfigField field=fields.stream().filter(f->id.equals(f.getParameterId())).findFirst().get();
            String generation=incarnation+":"+sequence.incrementAndGet();
            ObjectNode wanted=json.createObjectNode();wanted.put("generation",generation);wanted.put("value",field.getDefaultValue());wanted.put("actionBy",user);wanted.put("format",field.getFormat());
            LOG.info("REMOTE_CONFIG_PATH device={} parameter={} generation={} path=RAM",device.getId(),id,generation);
            ram.Set(space,"desired-config|"+device.getId(),id,wanted.toString());
            owned.put(device.getId()+"\u001f"+id,new String[]{"desired-config|"+device.getId(),id,wanted.toString()});
        }
        return "Success"; // accepted/sent; physical application is a separate truth
    }
    private JsonNode desired(String device,String parameter)throws Exception{
        final Ribbit ram=this.ram;if(ram==null)throw new IllegalStateException("RAM is unavailable");
        JsonNode rows=json.readTree(ram.Get(space,"desired-config|"+device,parameter)).path("rows");return rows.size()==1?rows.get(0).path("bag"):null;
    }
    private boolean matches(JsonNode wanted,JsonNode actual){
        return wanted != null && wanted.path("generation").isTextual() &&
            wanted.path("generation").asText().equals(actual.path("generation").asText());
    }
    private void applied(String snapshot){
        try{
            for(JsonNode row:json.readTree(snapshot).path("rows")){
                String family=row.path("name").path("indexB").asText();
                if(!family.startsWith("applied-config|")||row.path("gone").asBoolean())continue;
                String device=family.substring("applied-config|".length()),parameter=row.path("name").path("indexC").asText();
                JsonNode actual=row.path("bag"),wanted=desired(device,parameter);
                if(!matches(wanted,actual)){LOG.info("RAM_RESULT_IGNORED device={} parameter={} reason=generation",device,parameter);continue;}
                String status=actual.path("status").asText();if(!status.equals("applied")&&!status.equals("failed"))continue;
                if(!actual.path("value").isTextual())continue;
                if(status.equals("applied")&&!wanted.path("value").equals(actual.path("value"))){LOG.error("RAM result value mismatch device={} parameter={}",device,parameter);continue;}
                ObjectNode completion=json.createObjectNode();completion.put("device",device);completion.put("parameter",parameter);completion.put("generation",wanted.path("generation").asText());completion.put("value",actual.path("value").asText());completion.put("status",status);completion.put("actionBy",wanted.path("actionBy").asText());
                LOG.info("RAM_APPLIED device={} parameter={} generation={} status={}",device,parameter,wanted.path("generation").asText(),status);
                mqtt.send(COMPLETION_PREFIX+device+"/"+parameter,false,completion.toString());
            }
        }catch(Exception error){LOG.error("RAM completion observation/publication failed; physical action is not retried",error);throw new IllegalStateException("RAM completion failed",error);}
    }
    public void handleCompletion(String topic,String payload){
        try{
            JsonNode result=json.readTree(payload);String device=result.path("device").asText(),parameter=result.path("parameter").asText();
            if(!topic.equals(COMPLETION_PREFIX+device+"/"+parameter))throw new IllegalArgumentException("completion topic mismatch");
            JsonNode wanted=desired(device,parameter);
            if(!matches(wanted,result)){LOG.info("RAM_COMPLETION_IGNORED device={} parameter={} reason=generation",device,parameter);return;}
            String status=result.path("status").asText();if(!status.equals("applied")&&!status.equals("failed"))throw new IllegalArgumentException("completion status");
            if(!result.path("value").isTextual())throw new IllegalArgumentException("completion value");
            if(status.equals("applied")&&!wanted.path("value").equals(result.path("value")))return;
            Device current=devices.findOne(device);if(current==null)throw new IllegalArgumentException("unknown device");
            List<RemoteConfigField> fields=current.getDeviceParameterConfiguration().getRemoteConfigurations();int index=-1;
            for(int i=0;i<fields.size();++i)if(parameter.equals(fields.get(i).getParameterId()))index=i;
            if(index<0)throw new IllegalArgumentException("unknown parameter");RemoteConfigField field=fields.get(index);
            String marker="ribbit:"+result.path("generation").asText()+":"+parameter,value=result.path("value").asText();
            DeviceParameterConfigurationHistory history=new DeviceParameterConfigurationHistory();history.setMessageId(marker);history.setActionBy(wanted.path("actionBy").asText());history.setUpdatedDate(DateTime.now());history.setUpdateStatus(status.equals("applied")?UpdateStatus.UPDATED:UpdateStatus.FAILED);
            Map<String,String> network=new HashMap<>(),topics=new HashMap<>(),messages=new HashMap<>();
            if("Network & Communication".equals(field.getParameterCategory()))network.put(parameter,value);
            if("Topic Format & Interval".equals(field.getParameterCategory()))topics.put(parameter,value);
            if("Message Format".equals(field.getParameterCategory()))messages.put(parameter,value);
            history.setNetworkAndCommunication(network);history.setTopicFormat(topics);history.setMessageFormat(messages);
            DeviceParameterUpdateStatus updated=new DeviceParameterUpdateStatus();updated.setParameterId(parameter);updated.setParameter(field.getParameter());updated.setParameterCategory(field.getParameterCategory());updated.setDefaultValue(value);updated.setIsinValid(status.equals("failed"));
            Map<String,DeviceParameterUpdateStatus> changes=new HashMap<>();changes.put(parameter,updated);history.setUpdateParamList(changes);
            Query query=new Query(Criteria.where("_id").is(device).and("deviceParameterConfiguration.updateHistory.messageId").ne(marker));
            Update update=new Update().push("deviceParameterConfiguration.updateHistory",history);
            if(status.equals("applied"))update.set("deviceParameterConfiguration.remoteConfigurations."+index+".defaultValue",value);
            mongo.updateFirst(query,update,Device.class);
            LOG.info("RAM_COMPATIBILITY_CONSUMED device={} parameter={} generation={} status={}",device,parameter,result.path("generation").asText(),status);
        }catch(Exception error){LOG.error("RAM MQTT completion rejected/failed",error);throw new IllegalStateException("RAM MQTT completion failed",error);}
    }
}
