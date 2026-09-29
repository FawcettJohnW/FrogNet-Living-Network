#!/usr/bin/env python3
# No-fallbacks oracle for John's Python (the files the semantic-engine port touches).
#
# Each probe feeds one site the input it used to swallow. PASS means the site now raises (or, for detection, decides
# from the bytes). On the original tree every probe FAILS and prints what the Python did instead; on the fixed tree
# every probe PASSES. Run: test_python_no_fallbacks_oracle.py <root of opt/frognet_semantic>
import argparse, ast, contextlib, io, json, os, struct, sys, tempfile, types

sys.dont_write_bytecode = True
ap = argparse.ArgumentParser(); ap.add_argument('root'); a = ap.parse_args()
os.environ['FROGNET_BLOB_ROOT'] = tempfile.mkdtemp(prefix='nofb-blobs-')
sys.path.insert(0, a.root)
with contextlib.redirect_stdout(io.StringIO()):
    import core.json_handler as JH
    import core.template as TP
    import core.format_registry as FR
    import core.codec as CD
    import core.store as ST
    import proxy.templates as PT

results = []


def probe(name, f):
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            r = f()
    except Exception as e:  # the oracle's own boundary: a raise is the PASS answer
        results.append((name, True, 'raised %s: %s' % (type(e).__name__, str(e)[:110])))
        return
    results.append((name, False, 'returned %r%s' % (r if not isinstance(r, str) else r[:80],
                                                     ' (printed: %s)' % out.getvalue().strip()[:90] if out.getvalue() else '')))


def expect(name, f, want):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        r = f()
    results.append((name, r == want, 'returned %r, want %r' % (r, want)))


JS = JH.JsonFormatHandler()
DICT_T = JS.learn_reply_template('{"a":1,"rows":[{"x":1}],"b":{"c":"s"}}')
assert DICT_T['field_order'] == ['a', 'b.c', 'rows'], DICT_T['field_order']
ARR_T = JS.learn_reply_template('[1,2]')

# ---- core/json_handler.py
probe('json_handler learn: body is not JSON', lambda: JS.learn_reply_template('{bad'))
probe('json_handler extract: body is not JSON', lambda: JS.extract_reply_dynamic('{bad', DICT_T))
probe('json_handler extract: scalar body on an object template', lambda: JS.extract_reply_dynamic('7', DICT_T))
probe('json_handler extract: array body on an object template', lambda: JS.extract_reply_dynamic('[1]', DICT_T))
probe('json_handler rebuild: array field is non-JSON text', lambda: JS.rebuild_reply(DICT_T, [1, 's', 'nope']))
probe('json_handler rebuild: array field is Python-literal text', lambda: JS.rebuild_reply(DICT_T, [1, 's', "['x']"]))
probe('json_handler rebuild: array field is ""', lambda: JS.rebuild_reply(DICT_T, [1, 's', '']))
probe('json_handler rebuild: array field is an int', lambda: JS.rebuild_reply(DICT_T, [1, 's', 7]))
probe('json_handler rebuild: array field JSON is an object', lambda: JS.rebuild_reply(DICT_T, [1, 's', '{"a":1}']))
probe('json_handler rebuild: top-level array is non-JSON text', lambda: JS.rebuild_reply(ARR_T, ['nope']))
probe('json_handler rebuild: top-level array is an int', lambda: JS.rebuild_reply(ARR_T, [7]))
probe('json_handler rebuild: top-level array, no baseline',
      lambda: JS.rebuild_reply({'field_order': ['value'], 'type_map': {'value': 'array'}}, [None]))
BLOB_T = dict(DICT_T, baseline_blobs={'rows': 'sha256-' + '0' * 64})
probe('json_handler rebuild: baseline blob missing', lambda: JS.rebuild_reply(BLOB_T, [1, 's', None]))
os.environ['FROGNET_JSON_MAX_ITEMS'] = 'many'
probe('json_handler _max_items: malformed env', lambda: JS._max_items())
del os.environ['FROGNET_JSON_MAX_ITEMS']

# ---- core/template.py
TOK = {'ip': {}, 'host': {}, 'str': {}, 'enum': {}}
probe('template: fragment text is not JSON', lambda: TP.RequestTemplate('t', 1, 'GET', '/p', [], '{bad', TOK))
probe('template: fragment is None', lambda: TP.ReplyTemplate('t', 1, None, TOK))
probe('template: fragment JSON is not an object', lambda: TP.ReplyTemplate('t', 1, '[1]', TOK))
probe('template: fragment has no mode', lambda: TP.RequestTemplate('t', 1, 'GET', '/p', [], {'field_order': []}, TOK))
probe('template: unknown mode', lambda: TP.ReplyTemplate('t', 1, {'mode': 'bogus'}, TOK))
probe('template: tokens are not a dict', lambda: TP.ReplyTemplate('t', 1, {'mode': 'raw'}, 'x'))
probe('template: request template has no URL', lambda: TP.RequestTemplate('t', 1, 'GET', '', [], {'mode': 'raw'}, TOK))
probe('template: url_query_keys is not a list', lambda: TP.RequestTemplate('t', 1, 'GET', '/p', 'a', {'mode': 'raw'}, TOK))

# ---- core/format_registry.py: decides from the bytes; the handler then raises on a malformed body
expect('format_registry: request "{bad" is JSON by its bytes', lambda: FR.sniff_body_mode(b'{bad'), 'json')
src = open(FR.__file__).read()
results.append(('format_registry: no parse probe (json.loads / ElementTree) in detection',
                'ET.fromstring' not in src and 'json.loads' not in src.split('def detect_request')[0].split('def _looks')[1],
                'source scan of the sniffers'))

# ---- core/codec.py
codec = CD.SemanticCodec()
bad_err = struct.pack('<BBIH', 5, 0, 0xFFFFFFFF, 1) + struct.pack('<HB', 0, 2) + struct.pack('<H', 5) + b'xx:no'
probe('codec decode_error_reply: error frame with a malformed status', lambda: codec.decode_error_reply(bad_err))
expect('codec decode_error_reply: a non-error frame is still None',
       lambda: codec.decode_error_reply(struct.pack('<BBIH', 5, 0, 42, 0) + b'\0' * 8), None)

# ---- core/store.py (a store with a fake pool: no MySQL needed)


class Cur:
    def __init__(self, fail_on=()): self.fail_on = fail_on; self.sql = []
    def execute(self, q, args=None):
        self.sql.append(q)
        for f in self.fail_on:
            if f in q: raise RuntimeError('fake mysql: ' + f)
    def fetchone(self): return None
    def close(self): pass


class Conn:
    def __init__(self, cur): self.cur = cur; self.closed = False
    def cursor(self, **k): return self.cur
    def commit(self): pass
    def close(self): self.closed = True


class Pool:
    def __init__(self, conns): self.conns = list(conns); self.released = []
    def acquire(self): return self.conns.pop(0)
    def release(self, c): self.released.append(c)


def store_with(*conns):
    s = object.__new__(ST.TemplateStore)
    s._pool = Pool(conns)
    return s


probe('store _loads_json_maybe: column text is not JSON', lambda: ST._loads_json_maybe('{bad', {}))
probe('store build_request_template: no row', lambda: store_with().build_request_template(None))
probe('store build_reply_template: no row', lambda: store_with().build_reply_template(None))
ROW = {'templateId': 'tpl_1', 'opcode': 1, 'method': 'GET', 'actionUrl': '/p', 'url_query_keys': None}
probe('store build_request_template: params is not an object',
      lambda: store_with().build_request_template(dict(ROW, params='[1]')))
probe('store build_request_template: url_query_keys is not a list',
      lambda: store_with().build_request_template(dict(ROW, params='{"mode":"raw","tokens":{}}', url_query_keys='"a"')))
probe('store store_templates: empty path', lambda: store_with().store_templates('GET', '', {'mode': 'raw'}, None))
probe('store store_templates: request template is not a dict', lambda: store_with(Conn(Cur())).store_templates(
      'GET', '/p', 'x', None))
probe('store store_templates: INSERT fails (no fallback INSERT)', lambda: store_with(Conn(Cur(('url_query_keys',))),
      Conn(Cur())).store_templates('GET', '/p', {'mode': 'raw', 'tokens': {}}, {'mode': 'raw', 'tokens': {}}))
_calls = []


def _fails_first_time(conn):
    _calls.append(conn)
    if len(_calls) == 1:
        raise RuntimeError('boom')
    return 'succeeded on retry'


probe('store _with_conn: a failing call is not retried', lambda: store_with(Conn(Cur()), Conn(Cur()))._with_conn(
      _fails_first_time))
ST.TemplateStore._tables_ensured = False
probe('store ensure_tables: a failing migration step raises',
      lambda: store_with(Conn(Cur(('ALTER TABLE',)))).ensure_tables())

# ---- proxy/templates.py
probe('templates _parse_json_body: body is not JSON', lambda: PT._parse_json_body(b'{bad'))
probe('templates canonical_semantic_key: upsert body is not JSON',
      lambda: PT.canonical_semantic_key('POST', '/api.php?entity=sensor_data&action=upsert_by_name', b'{bad'))
probe('templates extract_dynamic_query_vals: declared key missing', lambda: PT.extract_dynamic_query_vals('/p', ['a']))
probe('templates extract_dynamic_query_vals: path has no query', lambda: PT.extract_dynamic_query_vals('/p?b=1', ['a']))
expect('templates extract_dynamic_query_vals: blank value is ""', lambda: PT.extract_dynamic_query_vals('/p?a=', ['a']),
       [('a', '')])


class RaisingStore:
    def store_templates(self, *a): raise RuntimeError('store down')


probe('templates learn_templates_from_real: a store failure propagates', lambda: PT.learn_templates_from_real(
      method='GET', semantic_path='/p', req_headers={}, req_body=b'', upstream={'status': 200, 'body': b'{"a":1}'},
      store=RaisingStore(), raw_path='/p'))
probe('templates learn_templates_from_real: a malformed JSON reply raises', lambda: PT.learn_templates_from_real(
      method='GET', semantic_path='/p', req_headers={}, req_body=b'', upstream={'status': 200, 'body': b'{bad'},
      store=types.SimpleNamespace(store_templates=lambda *a: None), raw_path='/p'))


class W:
    def write(self, b): raise BrokenPipeError(32, 'Broken pipe')


h = types.SimpleNamespace(send_response=lambda c: None, send_header=lambda k, v: None, end_headers=lambda: None, wfile=W(),
                          close_connection=False)
probe('templates fail_closed_template_missing: client hung up', lambda: PT.fail_closed_template_missing(
      h, corrid='c', method='GET', semantic_key='/p', target_ip='1.2.3.4', reason='r'))

# ---- proxy/transport_semantic.py: the learning call in _handle_resp_raw_and_learn is not inside a try with handlers
tree = ast.parse(open(os.path.join(a.root, 'proxy/transport_semantic.py')).read())
fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_handle_resp_raw_and_learn')
guarded = [t for t in ast.walk(fn) if isinstance(t, ast.Try) and t.handlers and any(
    isinstance(c, ast.Call) and getattr(c.func, 'id', '') == 'learn_templates_from_real' for c in ast.walk(t))]
calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, 'id', '') == 'learn_templates_from_real']
results.append(('transport _handle_resp_raw_and_learn: learn_templates_from_real not under except', bool(calls) and not guarded,
                '%d try/except block(s) wrap it' % len(guarded)))

# ---- discovery/hosts.py: the 2026-09-24 box log -- the control read timed out and the floor elected that host
with contextlib.redirect_stdout(io.StringIO()):
    import discovery.hosts as DH
_real_ci = DH._capability_index


def _timed_out(*a, **k):
    raise DH.CapabilityReadFailed("StoreSlow on 10.251.251.1: TimeoutError: timed out")


DH._capability_index = _timed_out
BOX = ["10.123.123.1 FrogNetHost.a", "10.155.155.1 FrogNetHost.b", "10.199.199.1 FrogNetHost.c",
       "10.250.250.1 FrogNetHost.d", "10.251.251.1 FrogNetHost.e"]
probe('discovery select_database_host: control read timed out',
      lambda: [l for l in DH.select_database_host(list(BOX), dbhost="10.251.251.1", logger=lambda m: None)
               if "databasehost.frognet" in l])
probe('discovery role_barrier_ready: control read timed out',
      lambda: DH.role_barrier_ready(list(BOX), dbhost="10.251.251.1"))
DH._capability_index = lambda *a, **k: {}
expect('discovery select_database_host: read answered, nothing published -> highest .1 baseline',
       lambda: [l for l in DH.select_database_host(list(BOX), logger=lambda m: None) if "databasehost.frognet" in l],
       ["10.251.251.1 databasehost.frognet"])
DH._capability_index = _real_ci
probe('discovery _num: a malformed capability field', lambda: DH._num({"value": 1}, 0.0))

bad = 0
for name, ok, what in results:
    print('%s %-72s %s' % ('PASS' if ok else 'FAIL', name, what))
    bad += not ok
print('RESULT %s no-fallbacks: %d probes, %d fail (%s)' % ('PASS' if not bad else 'FAIL', len(results), bad, a.root))
sys.exit(1 if bad else 0)
