<?php
// chat_send.php (POST from, text) -- [FDF_CHAT_V1] page input goes in as <App>.<QueueName>.<submitter>: chat.inbox.<from>.
// The RAM send happens HERE, on the page's own server, over its FNWP connection (ram write). Nothing else: the chat
// service takes it from the queue.
header('Content-Type: application/json');
$bin = getenv('FDF_BIN') ?: '/opt/fdf/bin';
$from = isset($_POST['from']) ? trim($_POST['from']) : ''; $text = isset($_POST['text']) ? (string)$_POST['text'] : '';
if (!preg_match('/^[A-Za-z0-9_.-]{1,40}$/', $from) || $text === '') { http_response_code(400); echo json_encode(['error' => 'from (letters, digits, _ . -) and text are required']); exit; }
$bag = json_encode(['text' => $text, 'at' => microtime(true)]);
$out = []; $rc = 0;
exec(escapeshellcmd($bin . '/ram') . ' write chat inbox ' . escapeshellarg($from) . ' ' . escapeshellarg($bag) , $out, $rc);   // stderr (diagnostics) goes to the server's terminal, never into the JSON
if ($rc !== 0) { http_response_code(502); echo json_encode(['error' => 'ram write failed', 'output' => implode("\n", $out)]); exit; }
echo implode("\n", $out);
