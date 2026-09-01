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
/**
 * FrogNet Local Template API
 * Handles Phase I writes (request + response templates)
 * and template lookup for Phase II (request + response).
 *
 * ALWAYS JSON output.
 */

header('Content-Type: application/json');

// ------------------------------------------------------------
// DB connection routed through config.php's FrogNetDb wrapper so
// the request ends with a clean COM_QUIT (eliminates "Aborted
// connection" warnings from this caller's tear-down).  Caller-facing
// code below is unchanged — $mysqli is a real mysqli instance.
// ------------------------------------------------------------
require_once __DIR__ . '/config.php';

$exists = false;

$mysqli = db()->raw();
if ($mysqli === null || $mysqli->connect_errno) {
    http_response_code(500);
    echo json_encode([
        'status' => 'error',
        'error'  => 'db_connect_failed',
        'message'  => '',
        'detail' => $mysqli ? $mysqli->connect_error : 'wrapper returned null',
        'time'    => gmdate('c'),
        'templateId' => '',
        'opcode'     => '',
        'exists'=>$exists,
        'request'=>'',
        'response'=>'',
        'action'=>''
    ]);
    exit;
}
$mysqli->set_charset('utf8mb4');

// ------------------------------------------------------------
// READ JSON BODY (IF ANY)
// ------------------------------------------------------------
$input = file_get_contents('php://input');
$data  = [];
if ($input !== '' &&
    isset($_SERVER['CONTENT_TYPE']) &&
    stripos($_SERVER['CONTENT_TYPE'], 'application/json') !== false) {

    $tmp = json_decode($input, true);
    if (is_array($tmp)) {
        $data = $tmp;
    }
}

// Action from body or GET
$action = $data['action'] ?? ($_GET['action'] ?? null);
if (!$action) {
    echo json_encode([
        'status' => 'error',
        'error'  => 'missing_action',
        'message'  => '',
        'detail' => '',
        'time'    => gmdate('c'),
        'templateId' => '',
        'opcode'     => '',
        'exists'=>$exists,
        'request'=>'',
        'response'=>'',
        'action'=>''
    ]);
    exit;
}

// ------------------------------------------------------------
// HELPER: compute opcode from templateId
// ------------------------------------------------------------
function frognet_compute_opcode($templateId) {
    $hash = hash('sha256', $templateId, true);
    $b0 = ord($hash[0]);
    $b1 = ord($hash[1]);
    return ($b0 << 8) | $b1;
}

// ------------------------------------------------------------
// HELPER: compute HMAC for request-template integrity
// ------------------------------------------------------------
function frognet_compute_hmac($templateId, $pageUrl, $method, $actionUrl, $paramsJson) {
    $secret = 'FrogNetLocalDevKey'; // <<< UPDATE IF YOU WANT
    $msg = $templateId.'|'.$pageUrl.'|'.$method.'|'.$actionUrl.'|'.$paramsJson;
    return hash_hmac('sha256', $msg, $secret);
}

// ------------------------------------------------------------
// ACTION: ping
// ------------------------------------------------------------
if ($action === 'ping') {
    echo json_encode([
        'status'  => 'ok',
        'error'  => '',
        'message' => 'pong',
        'detail' => '',
        'time'    => gmdate('c'),
        'templateId' => '',
        'opcode'     => '',
        'exists'=>$exists,
        'request'=>'',
        'response'=>'',
        'action'=>''
    ]);
    exit;
}

// ------------------------------------------------------------
// ACTION: upsert_request
// ------------------------------------------------------------
if ($action === 'upsert_request') {

    $templateId = $data['templateId'] ?? null;
    $pageUrl    = $data['pageUrl']    ?? null;
    $method     = $data['method']     ?? null;
    $actionUrl  = $data['actionUrl']  ?? null;  // PATH ONLY
    $params     = $data['params']     ?? [];

    if (!$templateId || !$pageUrl || !$method || !$actionUrl) {
        echo json_encode([
            'status' => 'error',
            'error'  => 'missing_fields',
            'message'  => '',
            'detail' => '',
            'time'    => gmdate('c'),
            'templateId' => '',
            'opcode'     => '',
            'exists'=>$exists,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    if (!is_array($params)) {
        $params = [];
    }

    $paramsJson    = json_encode($params);
    $paramTypes    = null;
    $returnFields  = null;
    $returnTypes   = null;
    $jsonSchemaId  = null;

    $opcode        = frognet_compute_opcode($templateId);
    $hmacKeyId     = 1;
    $hmacSignature = frognet_compute_hmac($templateId, $pageUrl, $method, $actionUrl, $paramsJson);

    $sql = "
        INSERT INTO frognet_request_templates
          (template_id, page_url, method, action_url,
           params_json, param_types, return_fields, return_types,
           json_schema_id, opcode, hmac_key_id, hmac_signature,
           response_html, created_at)
        VALUES
          (?, ?, ?, ?,
           ?, ?, ?, ?,
           ?, ?, ?, ?, NULL, NOW())
        ON DUPLICATE KEY UPDATE
          page_url       = VALUES(page_url),
          method         = VALUES(method),
          action_url     = VALUES(action_url),
          params_json    = VALUES(params_json),
          param_types    = VALUES(param_types),
          return_fields  = VALUES(return_fields),
          return_types   = VALUES(return_types),
          json_schema_id = VALUES(json_schema_id),
          opcode         = VALUES(opcode),
          hmac_key_id    = VALUES(hmac_key_id),
          hmac_signature = VALUES(hmac_signature)
    ";

    $stmt = $mysqli->prepare($sql);
    if (!$stmt) {
        echo json_encode([
            'status'=>'error',
            'error'=>'db_prepare_failed',
            'message'  => '',
            'detail'=>$mysqli->error,
            'time'    => gmdate('c'),
            'templateId' => '',
            'opcode'     => '',
            'exists'=>$exists,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    // 12 parameters: s s s s s s s s i i i s
    $stmt->bind_param(
        "ssssssssiiis",
        $templateId,
        $pageUrl,
        $method,
        $actionUrl,
        $paramsJson,
        $paramTypes,
        $returnFields,
        $returnTypes,
        $jsonSchemaId,
        $opcode,
        $hmacKeyId,
        $hmacSignature
    );

    if (!$stmt->execute()) {
        echo json_encode([
            'status'=>'error',
            'error'=>'db_execute_failed',
            'message'  => '',
            'detail'=>$stmt->error,
            'time'    => gmdate('c'),
            'templateId' => '',
            'opcode'     => '',
            'exists'=>$exists,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        $stmt->close();
        exit;
    }

    $stmt->close();

    echo json_encode([
        'status'     => 'ok',
        'error'  => 'missing_action',
        'message'  => '',
        'detail' => '',
        'time'    => gmdate('c'),
        'templateId' => $templateId,
        'opcode'     => $opcode,
        'exists'=>$exists,
        'request'=>'',
        'response'=>'',
        'action'=>''
    ]);
    exit;
}

// ------------------------------------------------------------
// ACTION: upsert_full (store request+response template blobs)
// ------------------------------------------------------------
if ($action === 'upsert_full') {

    $templateId = $data['templateId'] ?? null;
    $request    = $data['request']    ?? null;
    $response   = $data['response']   ?? null;

    if (!$templateId || !is_array($request) || !is_array($response)) {
        echo json_encode([
            'status'=>'error',
            'error'=>'missing_fields',
            'message'  => '',
            'detail' => '',
            'time'    => gmdate('c'),
            'templateId' => '',
            'opcode'     => '',
            'exists'=>$exists,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    $requestJson  = json_encode($request);
    $responseJson = json_encode($response);

    $sql = "
        INSERT INTO frognet_full_templates
          (template_id, request_json, response_json, created_at)
        VALUES
          (?, ?, ?, NOW())
        ON DUPLICATE KEY UPDATE
          request_json  = VALUES(request_json),
          response_json = VALUES(response_json)
    ";

    $stmt = $mysqli->prepare($sql);
    if (!$stmt) {
        echo json_encode([
            'status'=>'error',
            'error'=>'db_prepare_failed',
            'message'  => '',
            'detail'=>$mysqli->error,
            'time'    => gmdate('c'),
            'templateId' => '',
            'opcode'     => '',
            'exists'=>$exists,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    $stmt->bind_param("sss", $templateId, $requestJson, $responseJson);

    if (!$stmt->execute()) {
        echo json_encode([
            'status'=>'error',
            'error'=>'db_execute_failed',
            'message'  => '',
            'detail'=>$stmt->error,
            'time'    => gmdate('c'),
            'templateId' => '',
            'opcode'     => '',
            'exists'=>$exists,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        $stmt->close();
        exit;
    }

    $stmt->close();

    echo json_encode([
        'status'=>'ok',
        'error'=>'',
        'message'  => '',
        'detail' => '',
        'time'    => gmdate('c'),
        'templateId'=>$templateId,
        'opcode'     => '',
        'exists'=>$exists,
        'request'=>'',
        'response'=>'',
        'action'=>''
    ]);
    exit;
}

// ------------------------------------------------------------
// ACTION: exists (Phase II check)
// ------------------------------------------------------------
if ($action === 'exists') {
    $templateId = $data['templateId'] ?? ($_GET['templateId'] ?? null);
    if (!$templateId) {
        echo json_encode([
            'status'=>'error',
            'error'=>'missing_templateId',
            'message'  => '',
            'detail' => '',
            'time'    => gmdate('c'),
            'templateId' => '',
            'opcode'     => '',
            'exists'=>$exists,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    $sql = "SELECT 1 FROM frognet_request_templates WHERE template_id=? LIMIT 1";
    $stmt = $mysqli->prepare($sql);
    if (!$stmt) {
        echo json_encode([
            'status'=>'error',
            'error'=>'db_prepare_failed',
            'message'  => '',
            'detail'=>$mysqli->error,
            'time'    => gmdate('c'),
            'templateId' => '',
            'opcode'     => '',
            'exists'=>$exists,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    $stmt->bind_param("s", $templateId);
    $stmt->execute();
    $stmt->store_result();
    $exists = $stmt->num_rows > 0;
    $stmt->close();

    echo json_encode([
        'status'=>'ok',
        'error'=>'',
        'message'  => '',
        'detail' => '',
        'time'    => gmdate('c'),
        'templateId'=>$templateId,
        'opcode'     => '',
        'exists'=>$exists,
        'request'=>'',
        'response'=>'',
        'action'=>''
    ]);
    exit;
}

// ------------------------------------------------------------
// ACTION: get_request (daemon-debug)
// ------------------------------------------------------------
if ($action === 'get_request') {
    $templateId = $data['templateId'] ?? ($_GET['templateId'] ?? null);

    if (!$templateId) {
        echo json_encode([
            'status'=>'error',
            'error'=>'missing_templateId',
            'message'  => '',
            'detail' => '',
            'time'    => gmdate('c'),
            'templateId'=>$templateId,
            'opcode'     => '',
            'exists'=>$exists,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    $sql = "
      SELECT template_id, page_url, method, action_url,
             params_json, param_types, return_fields, return_types,
             json_schema_id, opcode, hmac_key_id, hmac_signature
      FROM frognet_request_templates
      WHERE template_id=? LIMIT 1
    ";

    $stmt = $mysqli->prepare($sql);
    if (!$stmt) {
        echo json_encode([
            'status'=>'error',
            'error'=>'db_prepare_failed',
            'message'  => '',
            'detail'=>$mysqli->error,
            'time'    => gmdate('c'),
            'templateId'=>$templateId,
            'opcode'     => '',
            'exists'=>$exists,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    $stmt->bind_param("s", $templateId);
    $stmt->execute();
    $res = $stmt->get_result();
    $row = $res->fetch_assoc();
    $stmt->close();

    if (!$row) {
        echo json_encode([
            'status'=>'ok',
            'error'=>'db_prepare_failed',
            'message'  => '',
            'detail'=>$mysqli->error,
            'time'    => gmdate('c'),
            'templateId'=>$templateId,
            'opcode'     => '',
            'exists'=>false,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    echo json_encode([
        'status'=>'ok',
        'error'=>'',
        'message'  => '',
        'detail'=>'',
        'time'    => gmdate('c'),
        'templateId'=>'',
        'opcode'     => '',
        'exists'=>true,
        'request'=>'',
        'response'=>'',
        'action'=>''
    ]);
    exit;
}

// ------------------------------------------------------------
// ACTION: get_response (wrapper inflator)
// ------------------------------------------------------------
if ($action === 'get_response') {
    $templateId = $data['templateId'] ?? ($_GET['templateId'] ?? null);

    if (!$templateId) {
        echo json_encode([
            'status'=>'error',
            'error'=>'missing_templateId',
            'message'  => '',
            'detail'=>'',
            'time'    => gmdate('c'),
            'templateId'=>'',
            'opcode'     => '',
            'exists'=>true,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    $sql = "SELECT response_json FROM frognet_full_templates WHERE template_id=? LIMIT 1";
    $stmt = $mysqli->prepare($sql);
    if (!$stmt) {
        echo json_encode([
            'status'=>'error',
            'error'=>'db_prepare_failed',
            'message'  => '',
            'detail'=>$mysqli->error,
            'time'    => gmdate('c'),
            'templateId'=>'',
            'opcode'     => '',
            'exists'=>true,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    $stmt->bind_param("s", $templateId);
    $stmt->execute();
    $res = $stmt->get_result();
    $row = $res->fetch_assoc();
    $stmt->close();

    if (!$row || !$row['response_json']) {
        echo json_encode([
            'status'=>'ok',
            'error'=>'missing_templateId',
            'message'  => '',
            'detail'=>'',
            'time'    => gmdate('c'),
            'templateId'=>$templateId,
            'opcode'     => '',
            'exists'=>false,
            'request'=>'',
            'response'=>'',
            'action'=>''
        ]);
        exit;
    }

    echo json_encode([
        'status'=>'ok',
        'error'=>'',
        'message'  => '',
        'detail'=>'',
        'time'    => gmdate('c'),
        'templateId'=>$templateId,
        'templateId'=>'',
        'opcode'     => '',
        'exists'=>true,
        'request'=>'',
        'response'=>json_decode($row['response_json'], true),
        'action'=>''
    ]);
    exit;
}

// ------------------------------------------------------------
// UNKNOWN ACTION
// ------------------------------------------------------------
echo json_encode([
    'status'=>'error',
    'error'=>'unknown_action',
    'message'  => '',
    'detail'=>'',
    'time'    => gmdate('c'),
    'templateId'=>$templateId,
    'opcode'     => '',
    'exists'=>true,
    'request'=>'',
    'response'=>'',
    'action'=>$action
]);
exit;
