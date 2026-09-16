<?php
	require_once("config.php");
	require("PHPMailer/class.phpmailer.php");

	$auth_host = $GLOBALS['auth_host'];
	$auth_user = $GLOBALS['auth_user'];
	$auth_pass = $GLOBALS['auth_pass'];
	$auth_dbase = $GLOBALS['auth_dbase'];

	$db = mysqli_connect($auth_host, $auth_user, $auth_pass,$auth_dbase) or die("Error " . mysqli_error($db));
	
	
	$email = mysqli_real_escape_string($db,$_POST['email']);

	$sql = mysqli_query($db,"SELECT * FROM account WHERE email = '$email'");
	$rows= mysqli_num_rows($sql);

	if($rows < 1){
		echo "false";
	}else{
		$row = mysqli_fetch_row($sql);
		$message = "Your password is:\n\n";
		 //Initialize the random password
  		$password = '';

  		//Initialize a random desired length
  		$desired_length = rand(8, 12);

  		for($length = 0; $length < $desired_length; $length++) {
    			//Append a random ASCII character (including symbols)
    			$password .= chr(rand(32, 126));
  		}

                $message .= $password;

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
		$mail->Subject  = 'Recover Password';
		$mail->Body     = $message;

		$mail->Send();

		$q = "UPDATE account SET password = MD5('".$password."') WHERE(email ='$email')";
    		$result_activate_account = mysqli_query($db,$q);

		echo "true";	
	}
	mysqli_close($db);
?> 
 