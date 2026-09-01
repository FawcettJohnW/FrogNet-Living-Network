<?php
/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// Returns:
// 0 = no change
// 1 = error
// bitmask >=2:
//   2 = frognet_echo changed
//   4 = getHosts changed
//   8 = getDBHost changed

header("Content-Type: text/plain");
header("Cache-Control: no-store");

$STATE="/tmp/frognet_dashboard_state.json";

function sh($c){ $o=@shell_exec($c." 2>/dev/null"); return $o===null?"":trim($o); }

function canon_hosts(){
  $L=@file("/etc/hosts",FILE_IGNORE_NEW_LINES);
  if(!$L) return "";
  $o=[];
  foreach($L as $l){
    $l=preg_replace('/#.*/','',$l);
    $l=trim($l);
    if($l!=="") $o[]=$l;
  }
  return implode("\n",$o);
}

function ipint($ip){
  $p=array_map('intval',explode(".",$ip));
  return ($p[0]<<24)+($p[1]<<16)+($p[2]<<8)+$p[3];
}

try{
  // frognet_echo fingerprint
  $echo = sh("hostname -s").",".
          sh("/usr/local/bin/getEth0Address").",".
          sh("/usr/local/bin/getWlan0IP").",".
          sh("/usr/local/bin/getWlan1IP");

  // hosts fingerprint
  $hosts_fp = hash("sha256", canon_hosts());

  // dbhost fingerprint
  $mx=""; $mxv=-1; $db="";
  foreach(explode("\n",canon_hosts()) as $l){
    $a=preg_split('/\s+/',$l);
    if(count($a)<2) continue;
    $ip=$a[0];
    if(strncmp($ip,"10.",3)===0){
      $v=ipint($ip);
      if($v>$mxv){$mxv=$v;$mx=$ip;}
    }
    for($i=1;$i<count($a);$i++)
      if($a[$i]==="databasehost.frognet") $db=$ip;
  }
  $db_fp="$mx|$db";

  $prev=["e"=>"","h"=>"","d"=>""];
  if(is_file($STATE)){
    $j=@json_decode(file_get_contents($STATE),true);
    if(is_array($j)) $prev=$j;
  }

  $m=0;
  if($echo!==$prev["e"]) $m|=2;
  if($hosts_fp!==$prev["h"]) $m|=4;
  if($db_fp!==$prev["d"]) $m|=8;

  if($m){
    file_put_contents($STATE,json_encode(["e"=>$echo,"h"=>$hosts_fp,"d"=>$db_fp]));
  }

  echo $m;
}catch(Throwable $t){
  http_response_code(500);
  echo "1";
}
