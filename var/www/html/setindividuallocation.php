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

require("opendb.php");
require("FrogNetVars.php");

$TimeStamp=date('Y-m-d H:i:s', time());

if ( $speed == "") {
    $speed = 0.0;
}

if ( $altitude == "") {
    $altitude = 0.0;
}

if ( $course == "") {
    $course = 0.0;
}

$query='REPLACE INTO GPSLocation (timeRecorded, Latitude, Longitude, Altitude, Speed, Course, UserID) VALUES ("'.$TimeStamp.'", '.$latitude.', ' .$longitude.', '.$altitude.', '.$speed.', '.$course.', "'.$userid.'")';

$result = $mysqli->query($query) or die('{"Status":[{"Code":2797}], "BadQuery":[{"BadQuery":"'.$mysqli->error.'"}]}');

echo '{"Status":[{"Code":0}]}';
?>
