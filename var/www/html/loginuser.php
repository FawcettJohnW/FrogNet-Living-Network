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
	
$query = "Select * from AuthorizedUsers where CallSign='".$callsign."' AND Password='".$password."'";

$result = mysqli_query( $mysqli, $query);

$numRows = mysqli_num_rows($result);
if ($numRows > 0)
{
	$row=mysqli_fetch_array($result);

        $loginHost = gethostname();

        // $fnHostQuery = 'SELECT FrogID from FrogNet where NetworkName="'.$loginHost.'"';
        // $hostResult = mysqli_query( $mysqli, $fnHostQuery);
        // $hostrow=mysqli_fetch_array($hostResult);
        // $fnHostID=$hostRow['FrogID'];

	$userID=$row['UserID'];
	$team=$row['Team'];
	$registeredHost=$row['registeredHost'];

        $updateQuery = 'UPDATE AuthorizedUsers SET loginHost="'.$loginHost.'" WHERE UserID="'.$userid.'"';
        $updateResult = mysqli_query( $mysqli, $updateQuery);

	$lastMessageID=$row['lastMessageID'];
        echo '{"Status":[{"Code":1}], "UserID":[{"UserID":"'.$userID.'"}], "Team":[{"Team":"'.$team.'"}], "RegisteredHost":[{"RegisteredHost":"'.$registeredHost.'"}], "LastMessageID":[{"LastMessageID":'.$lastMessageID.'}]}';

	exit();
}
else
{
        echo '{"Status":[{"Code":0}], "UserID":[{"UserID":0}], "Team":[{"Team":""}], "RegisteredHost":[{"RegisteredHost":""}], "LastMessageID":[{"LastMessageID":''}]}';
}
?>
