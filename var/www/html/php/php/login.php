<?php
	require_once("config.php");

	$auth_host = $GLOBALS['auth_host'];
	$auth_user = $GLOBALS['auth_user'];
	$auth_pass = $GLOBALS['auth_pass'];
	$auth_dbase = $GLOBALS['auth_dbase'];
	
	
	$db = mysqli_connect($auth_host, $auth_user, $auth_pass,$auth_dbase) or die("Error " . mysqli_error($db));
	

	$user_name = mysqli_real_escape_string($db,$_POST['name']);
	$user_password = mysqli_real_escape_string($db,$_POST['password']);

	$sql = mysqli_query($db,"SELECT * FROM account WHERE (user = '$user_name' AND password = MD5('".$user_password."')) AND active IS NULL");
	$rows= mysqli_num_rows($sql);
	if($rows > 0){
		echo "true";
	}else{
		echo "false";
	}
	mysqli_close($db);
	
?> 