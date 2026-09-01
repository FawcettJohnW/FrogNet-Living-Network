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
$frogIDQuery='select FrogID from KnownFrogNet where NetworkName="'.$host.'"';
$frogIDResult=$mysqli->query($frogIDQuery) or die('{"Status":[{"Code":2798}], "BadQuery":[{"BadQuery":"'.$mysqli->error.'"}]}');

$row=mysqli_fetch_array($frogIDResult);
$frogID=$row['FrogID'];

$query='REPLACE INTO GPSLocation (timeRecorded, Latitude, Longitude, Altitude, Speed, Course, UserID) VALUES ("'.$TimeStamp.'", '.$latitude.', ' .$longitude.', '.$altitude.', '.$speed.', '.$course.', "'.$frogID.'")';

$result=$mysqli->query($query) or die('{"Status":[{"Code":2797}], "BadQuery":[{"BadQuery":"'.$mysqli->error.'"}]}');

echo '{"Status":[{"Code":1}]}';
?>
