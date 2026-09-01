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
exec("/usr/local/bin/get_available_wifi.bash", $options);
exec("/usr/local/bin/get_available_interfaces.bash", $interfaces);
?>
<br>

<!DOCTYPE html><html><head>    <title>LOGIN</title>    <link rel="stylesheet" type="text/css" href="style.css"></head><body>     
<form action="connect_network.php" method="post">        
    <h2>LOGIN</h2>        
    <br>
    <label>Which interface?:</label>        
        <select>
            <?php foreach ($interfaces as $interface): ?>
                <option value="<?php echo $interface; ?>">
                    <?php echo $interface; ?>
                </option>
            <?php endforeach; ?>
        </select>
    </label>
    <br>
    <br>
    <label>Available Networks:</label>        
        <select>
            <?php foreach ($options as $option): ?>
                <option value="<?php echo $option; ?>">
                    <?php echo $option; ?>
                </option>
            <?php endforeach; ?>
        </select>
    </label>
        <br>
        <br>
        <label>Password</label>
        <input type="password" name="password" placeholder="Password">
        <br>         
        <br>
        <button type="submit">Login</button>     
</form>
</body>
</html>
