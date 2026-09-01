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
function GUID()
{
    if (function_exists('com_create_guid') === true)
    {
        return trim(com_create_guid(), '{}');
    }

    return sprintf('%04X%04X-%04X-%04X-%04X-%04X%04X%04X', mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(16384, 20479), mt_rand(32768, 49151), mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(0, 65535));
}

$newHost = strip_tags(html_entity_decode($_GET["networkHostName"]));
$deviceIP = strip_tags(html_entity_decode($_GET["networkIP"]));
$gatewayIP = strip_tags(html_entity_decode($_GET["gatewayIP"]));

$guid = GUID();
$myfile = fopen(__DIR__."/Commands/netCommand.".$guid, "w") or die("Unable to open file!");
fwrite($myfile, "/usr/loca/bin/add_lan_route.bash  $newHost $deviceIP $gatewayIP");
fclose($myfile);

$guid = GUID();
$myfile = fopen(__DIR__."/Commands/netCommand.".$guid, "w") or die("Unable to open file!");
fwrite($myfile, "/usr/local/bin/PropogateHost $newHost $deviceIP $gatewayIP no");
fclose($myfile);

?>

