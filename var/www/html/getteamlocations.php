
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

$fnhQuery = 'select * from AuthorizedUsers where Team="'.$teamname.'"';
$fnh_result = mysqli_query( $mysqli, $fnhQuery);
$num_fnh_row = mysqli_num_rows($fnh_result);
$locations = '';
if( $num_fnh_row > 0 )
{
    $firstPass = 1;
    while($fnh_row = mysqli_fetch_array($fnh_result))
    {
        $fnID = $fnh_row['UserID'];
        $fnhName = $fnh_row['CallSIgn'];

        $gps_query = 'Select * from GPSLocation where UserID="'.$fnID.'"';
        $gps_result = mysqli_query( $mysqli, $gps_query);

        $gps_row = mysqli_fetch_array($gps_result);

        if ($firstPass > 1)
        {
            $locations .= ",";
        }
        $firstPass++;
    
        $locations .= '{ "UserID":"'.$fnID.
                         '", "CallSign":"'.$fnh_row['CallSign'].
                         '", "Latitude":"'.$gps_row['Latitude'].
                         '", "Longitude":"'.$gps_row['Longitude'].
                         '", "Altitude":"'.$gps_row['Altitude'].
                         '", "Speed":"'.$gps_row['Speed'].
                         '", "Course":"'.$gps_row['Course'].
                         '", "IsUnknown":"'.$gps_row['IsUnknown'].'"}';
   }
   
   echo '{"Status":[{"Code":1}], "NumEntries":[{"NumEntries":'.$num_fnh_row.'}], "Locations":['.$locations.']}';

   exit();
}
else
{
   echo '{"Status":[{"Code":1}], "NumEntries":[{"NumEntries":0], "Locations":[]}';
}
?>
