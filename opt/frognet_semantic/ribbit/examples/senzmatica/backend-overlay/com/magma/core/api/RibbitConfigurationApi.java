package com.magma.core.api;
import com.magma.core.service.RibbitConfigurationService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import java.util.Collections;
import java.util.Map;
@RestController
public class RibbitConfigurationApi {
    @Autowired private RibbitConfigurationService configuration;
    @RequestMapping(value="/core/device/remoteConfiguration/path",method=RequestMethod.GET)
    public Map<String,Boolean> get(){return Collections.singletonMap("ram",configuration.isEnabled());}
    @RequestMapping(value="/core/device/remoteConfiguration/path",method=RequestMethod.PUT)
    public Map<String,Boolean> set(@RequestBody Map<String,Boolean> value){
        if(value.size()!=1||!value.containsKey("ram")||value.get("ram")==null)throw new IllegalArgumentException("Expected {ram:boolean}");
        configuration.setEnabled(value.get("ram"));return get();
    }
}
