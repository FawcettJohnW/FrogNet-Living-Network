<?php
// ~500KB stable JSON with 5 dynamic top-level fields.
$fields = ['ts', 'rnd_a', 'rnd_b', 'rnd_c', 'rnd_d'];
$num_changing = rand(1, count($fields));
$changing = $fields; shuffle($changing);
$changing = array_slice($changing, 0, $num_changing);
$dynamic = [];
foreach ($fields as $f) {
    if ($f === 'ts')                      $dynamic[$f] = date('Y-m-d H:i:s');
    elseif (in_array($f, $changing))      $dynamic[$f] = sprintf('%08x', rand(0, 0x7fffffff));
    else                                   $dynamic[$f] = 'baseline_value_' . $f;
}
$entries = [];
for ($i = 0; $i < 3300; $i++) {
    $entries[] = [
        'index'  => $i,
        'key'    => sprintf('node_%05d', $i),
        'value'  => sprintf('FrogNet semantic compression test payload entry %05d', $i),
        'static' => 'This field is always the same and compresses to near-zero after bootstrap.',
    ];
}
header('Content-Type: application/json');
echo json_encode(['status'=>'ok','dynamic'=>$dynamic,'entries'=>$entries]);
