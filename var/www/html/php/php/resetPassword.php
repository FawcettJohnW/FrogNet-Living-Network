<?php
	require_once("config.php");

	$auth_host = $GLOBALS['auth_host'];
	$auth_user = $GLOBALS['auth_user'];
	$auth_pass = $GLOBALS['auth_pass'];
	$auth_dbase = $GLOBALS['auth_dbase'];

	$db = mysqli_connect($auth_host, $auth_user, $auth_pass,$auth_dbase) or die("Error " . mysqli_error($db));
	
	
	$username= mysqli_real_escape_string($db,$_POST['name']);
	$password = mysqli_real_escape_string($db,$_POST['password']);

    	$result = mysqli_query($db,"UPDATE account SET password = MD5('".$password."') WHERE(user ='$username')");
	echo "true";	
	mysqli_close($db);
?> 