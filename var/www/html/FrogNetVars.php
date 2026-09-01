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
$latitude = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["Latitude"])));
$longitude = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["Longitude"])));
$altitude = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["Altitude"])));
$speed = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["Speed"])));
$course = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["Course"])));
$userid = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["UserID"])));
$password = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["PWH"])));
$callsign = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["CallSign"])));
$realname = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["RealName"])));
$teamname = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["Team"])));

$propogateUp = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["propogateUp"])));
$routedip = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["routedIP"])));
$networkip = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["networkIP"])));
$gatewayip = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["gatewayIP"])));
$deviceip = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["deviceIP"])));
$host = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["host"])));
$registeredhost = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["registeredHost"])));
$route = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["route"])));
?>
