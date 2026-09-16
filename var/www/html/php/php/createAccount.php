<?php
	require_once("config.php");
	require("PHPMailer/class.phpmailer.php");

	$auth_host = $GLOBALS['auth_host'];
	$auth_user = $GLOBALS['auth_user'];
	$auth_pass = $GLOBALS['auth_pass'];
	$auth_dbase = $GLOBALS['auth_dbase'];

	$db = mysqli_connect($auth_host, $auth_user, $auth_pass,$auth_dbase) or die("Error " . mysqli_error($db));
	
	
	$username = mysqli_real_escape_string($db,$_POST['name']);
	$password = mysqli_real_escape_string($db,$_POST['password']);
	$email = mysqli_real_escape_string($db,$_POST['email']);

	$sql = mysqli_query($db,"SELECT * FROM account WHERE user = '$username'");
	$rows= mysqli_num_rows($sql);

	if($rows > 0){
		echo "false";
	}else{
		
		$url  = isset($_SERVER['HTTPS']) ? 'https://' : 'http://';
		$url .= $_SERVER['SERVER_NAME'];
		$url .= $_SERVER['REQUEST_URI'];

		$activation = md5(uniqid(rand(), true));
		mysqli_query($db,"INSERT INTO account(user,password,email,active) VALUES ('$username',MD5('".$password."'),'$email','$activation')");
		$message = " To activate your account, please click on this link:\n\n";
                $message .= dirname($url). '/activate.php?email=' . urlencode($email) . "&key=$activation";

		$mail = new PHPMailer;
		$smtp_auth = $GLOBALS['smtp_auth'];

		if($smtp_auth){
			$mail->isSMTP();                                   
			$mail->Host = $GLOBALS['smtp_host'];
			$mail->SMTPAuth = true;                              
			$mail->Username = $GLOBALS['smtp_user'];                 
			$mail->Password = $GLOBALS['smtp_password'];                           
			$mail->SMTPSecure = "tls";                           
			$mail->Port = $GLOBALS['smtp_port'];     
		}

		$mail->setFrom($GLOBALS['smtp_user'], 'Support');
		$mail->addAddress($email);
		$mail->Subject  = 'Registration Confirmation';
		$mail->Body     = $message;

		$mail->Send();

		echo "true";
	}
	mysqli_close($db);
?> 
 