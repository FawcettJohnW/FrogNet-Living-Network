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
$networkip = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["NetworkIP"])));
$networkName = $mysqli->real_escape_string(strip_tags(html_entity_decode($_GET["NetworkName"])));

function create_guid()
{
    if (function_exists('com_create_guid') === true)
    {
        return trim(com_create_guid(), '{}');
    }

    return sprintf('%04X%04X-%04X-%04X-%04X-%04X%04X%04X', mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(16384, 20479), mt_rand(32768, 49151), mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(0, 65535));
}

$query='select * from KnownFrogNet where NetworkName="'.$networkName.'"';

$result = $mysqli->query($query)or die('{"Status":[{"Code":6799}], "BadQuery":[{"BadQuery":"'.$mysqli->error.'"}]}');

$numRows = mysqli_num_rows($result);  
if ($numRows > 0)
{
    $row=mysqli_fetch_array($result);

    $networkName=$row['NetworkName'];
    $networkIP=$row['IPAddress'];
    $frogNetID=$row['FrogID'];
    $lastHeartbeat=$row['LastHeartbeat'];

    echo '{"Code":"1", "NetworkName": "'.$networkName.'", "FrogNetID":"'.$frogNetID.'", "IPAddress": "'.$networkIP.'", "LastHeartbeat":"'.$lastHeartbeat.'"}';

    exit();
}

$networkID = create_guid();

$createTime = date('Y-m-d H:i:s');

$regFN='replace into KnownFrogNet (NetworkName, FrogID, IPAddress, LastHeartbeat) VALUES ("'.$networkName.'", "'.$networkID.'", "'.$networkip.'", "'.$createTime.'")';

$mysqli->query($regFN) or die('{"Status":[{"Code":6797}], "BadQuery":[{"BadQuery":"'.$mysqli->error.'"}]}');

$query='select * from KnownFrogNet where NetworkName="'.$networkName.'"';

$result = $mysqli->query($query)or die('{"Status":[{"Code":6799}], "BadQuery":[{"BadQuery":"'.$mysqli->error.'"}]}');

$numRows = mysqli_num_rows($result);  
if ($numRows > 0)
{
    $row=mysqli_fetch_array($result);

    $networkName=$row['NetworkName'];
    $networkIP=$row['IPAddress'];
    $frogNetID=$row['FrogID'];
    $lastHeartbeat=$row['LastHeartbeat'];

    echo '{"Code":"1", "NetworkName": "'.$networkName.'", "FrogNetID":"'.$frogNetID.'", "IPAddress": "'.$networkIP.'", "LastHeartbeat":"'.$lastHeartbeat.'"}';

    exit();
}
echo '{"Code":"6500", "NetworkName": "", "FrogNetID":"", "IPAddress": "", "LastHeartbeat":""}';
?>
}
echo '{"Code":"0", "NetworkName": "", "FrogNetID":"", "IPAddress": "", "LastHeartbeat":""}';
?>
