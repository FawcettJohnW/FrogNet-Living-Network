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

function create_guid()
{
    if (function_exists('com_create_guid') === true)
    {
        return trim(com_create_guid(), '{}');
    }

    return sprintf('%04X%04X-%04X-%04X-%04X-%04X%04X%04X', mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(16384, 20479), mt_rand(32768, 49151), mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(0, 65535));
}

$loginHost = gethostname();

$eu_query = "Select * from AuthorizedUsers where CallSign='".$callsign."'";

$eu_result = mysqli_query( $mysqli, $eu_query);

$numRows = mysqli_num_rows($eu_result);
if ($numRows > 0)
{
	$row=mysqli_fetch_array($eu_result);

	$userID=$row['UserID'];
	$lastMessageID=$row['lastMessageID'];
        $pw=$row['Password'];
        if ($pw == $password) {
            $updateQuery = 'UPDATE AuthorizedUsers SET loginHost="'.$loginHost.'" WHERE UserID="'.$userID.'"';
            $updateResult = mysqli_query( $mysqli, $updateQuery);

            echo '{"Status":[{"Code":1}], "UserID":[{"UserID":"'.$userID.'"}], "LastMessageID":[{"LastMessageID":'.$lastMessageID.'}]}';
        }
        else {
            echo '{"Status":[{"Code":3}], "UserID":[{"UserID":0}], "LastMessageID":[{"LastMessageID":0}]}';
        }

	exit();
}

$newGuid = create_guid();

$fnHostQuery = 'SELECT FrogID from KnownFrogNet where NetworkName="'.$loginHost.'"';
$hostResult = mysqli_query( $mysqli, $fnHostQuery);
$hostrow=mysqli_fetch_array($hostResult);
$fnHostID=$hostrow['FrogID'];

$insert_query = 'INSERT INTO AuthorizedUsers (RealName, loginHost, registeredHost, CallSign, Password, Role, UserID, Team, lastMessageID) VALUES ("'.$realname.'", "'.$loginHost.'", "'.$fnHostID.'", "' .$callsign.'", "'.$password.'", "User", "'.$newGuid.'", "'.$teamname.'",0 )';

$iu_result = $mysqli->query($insert_query) or die('{"Status":[{"Code":2797}], "BadQuery":[{"BadQuery":"'.$mysqli->error.'"}]}');

$au_query = "Select * from AuthorizedUsers where CallSign='".$callsign."'";

$au_result = mysqli_query( $mysqli, $au_query);

$numRows = mysqli_num_rows($au_result);
if ($numRows > 0)
{
    $row=mysqli_fetch_array($au_result);

    $userID=$row['UserID'];
    $lastMessageID=$row['lastMessageID'];

    echo '{"Status":[{"Code":1}], "UserID":[{"UserID":"'.$userID.'"}], "LastMessageID":[{"LastMessageID":'.$lastMessageID.'}]}';

    exit();
}
echo '{"Status":[{"Code":0}], "UserID":[{"UserID":0}], "LastMessageID":[{"LastMessageID":0}]}';
?>
