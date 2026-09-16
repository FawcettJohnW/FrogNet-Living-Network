<?php
function frognet_entities(): array {
  return [
    'users' => [
      'table'   => 'User',
      'columns' => ['CallSign','RealName','Password'],
      'pk'      => ['CallSign'],
      'auto'    => [],
      'required_on_create' => ['CallSign','RealName','Password'],
    ],
    'teams' => [
      'table'   => 'Team',
      'columns' => ['TeamName','CreatedDate','TeamOwner'],
      'pk'      => ['TeamName'],
      'auto'    => [],
      'required_on_create' => ['TeamName','TeamOwner'],
    ],
    'team_members' => [
      'table'   => 'TeamMember',
      'columns' => ['TeamName','TeamUser'],
      'pk'      => ['TeamName','TeamUser'],
      'auto'    => [],
      'required_on_create' => ['TeamName','TeamUser'],
    ],
    'messages' => [
      'table'   => 'Message',
      'columns' => ['messageID','FromUser','ToUser','Message','sentDate','readDate'],
      'pk'      => ['messageID'],
      'auto'    => ['messageID'],
      'required_on_create' => ['FromUser','ToUser','Message'],
    ],
    'known_frognets' => [
      'table'   => 'KnownFrogNet',
      'columns' => ['NetworkName','IPAddress','FrogID','Tags','LastHeartbeat'],
      'pk'      => ['NetworkName'],
      'auto'    => [],
      'required_on_create' => ['NetworkName','IPAddress','FrogID'],
    ],
    'sensors' => [
      'table'   => 'Sensor',
      'columns' => ['SensorID','FrogID','SensorAddress','SensorNetwork','SensorName','SensorType','Tags'],
      'pk'      => ['SensorID'],
      'auto'    => ['SensorID'],
      'required_on_create' => ['FrogID','SensorAddress','SensorNetwork','SensorName','SensorType'],
    ],
    'iahosts' => [
      'table'   => 'IAHost',
      'columns' => ['IAHostID','FrogID','IAAddress','IANetwork','IAName','IAType','Tags'],
      'pk'      => ['IAHostID'],
      'auto'    => ['IAHostID'],
      'required_on_create' => ['FrogID','IAAddress','IANetwork','IAName','IAType'],
    ],
    'actuators' => [
      'table'   => 'Actuator',
      'columns' => ['ActuatorID','FrogID','ActuatorAddress','ActuatorNetwork','ActuatorName','ActuatorType','Tags'],
      'pk'      => ['ActuatorID'],
      'auto'    => ['ActuatorID'],
      'required_on_create' => ['FrogID','ActuatorAddress','ActuatorNetwork','ActuatorName','ActuatorType'],
    ],
    'well_known_sites' => [
      'table'   => 'WellKnownSite',
      'columns' => ['SiteID','FrogID','SiteAddress','SiteName','Tags'],
      'pk'      => ['SiteID'],
      'auto'    => ['SiteID'],
      'required_on_create' => ['FrogID','SiteAddress','SiteName'],
    ],
    'sensor_data' => [
      'table'   => 'SensorData',
      'columns' => ['SensorID','FrogID','jsonData'],
      'pk'      => ['SensorID'],
      'auto'    => [],
      'required_on_create' => ['SensorID','FrogID','jsonData'],
    ],
  ];
}
