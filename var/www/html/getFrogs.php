
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

$query = "Select * from KnownFrogNets";

$result = mysqli_query( $mysqli, $query);
        $num_row = mysqli_num_rows($result);
        $frogs = '';
        if( $num_row > 0 )
        {
            $firstPass = 1;
            while($row = mysqli_fetch_array($result))
            {
                if ($firstPass > 1)
                {
                    $frogs .= ",";
                }
                $firstPass++;

                $frogs .=    '{ "networkName":"'.$row['NetoworkName'].
                             '", "networkIP":"'.$row['IPAddress'].
                             '", "frognetID":"'.$row['FrogID'].
                             '", "lastHeartbeat":"'.$row['LastHeartbeat'].
            }
            echo '{"Status":[{"Code":1}], "NumEntries":[{"NumEntries":'.$num_row.'}], "Frogs":['.$frogs.']}';
        }
        else
        {
            echo '{"Status":[{"Code":1894}], "NumEntries":[{"NumEntries":0}], "frogs":[]}';
        }
    
    exit();
?>
