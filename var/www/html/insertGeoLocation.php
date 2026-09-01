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
// Connection routed through config.php's FrogNetDb wrapper so the
// request ends with a clean COM_QUIT (no "Aborted connection" spam).
require_once __DIR__ . '/config.php';
$mysqli = db()->raw();
if ($mysqli === null) {
    http_response_code(500);
    echo '{"Status":[{"Code":8888}], "Location":[]}';
    exit;
}

$query = "Select * from GPSLocation where LocationID=(select LocationID from KnownFrogNet)";

# $result = $mysqli->query($query) or die('{"Status":[{"Code":1000}], "BadQuery":[{"BadQuery":"'.$mysqli->error.'"}, {"Query":"'.$query.'"}]}');
$result = mysqli_query( $mysqli, $query);

$numRows = mysqli_num_rows($result);
if ($numRows > 0)
{
	$row=mysqli_fetch_array($result);

	$reply  =    '{ "Latitude":"'.$row['Latitude'].'",
		"Longitude":"'.$row['Longitude'].'" }';
	echo '{"Status":[{"Code":1}], "Location":['.$reply.']}';

	exit();
}
?>

