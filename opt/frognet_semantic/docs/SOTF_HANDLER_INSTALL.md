# SotF Codex — Install Instructions

A new codex installable alongside the existing `json` / `xml` / `html` /
`text` / `raw` handlers in `core/`. Routes media frames through the
codex compression machinery using a JSON envelope with a `"_sotf": 1`
marker field that the body sniffer recognizes.

This package does NOT modify `core/format_registry.py`. You apply the
small registration patch yourself so it composes with whatever local
changes you've already made.

## What this tarball lays down

```
opt/frognet_semantic/
  core/
    sotf_handler.py             # the new codex — drop into your core/
  tests/
    test_sotf_handler.py        # smoke test — run after install
  docs/
    SOTF_HANDLER_INSTALL.md     # this file
```

After extracting (`sudo tar xzf … -C /`), nothing is active yet — the
handler file is in place but `format_registry.py` doesn't know about it.
Apply the patch below to register it.

## Registration patch for `core/format_registry.py`

Three small edits. Line numbers are from the reference v3 registry; in
your modified tree they'll be different, but the surrounding context
should be unique enough to locate cleanly.

### Edit 1 — add the import

Find the existing handler imports near the top:

```python
from .json_handler import JsonFormatHandler
from .xml_handler import XmlFormatHandler
from .html_handler import HtmlFormatHandler
from .text_handler import RawFormatHandler, TextFormatHandler
```

Add:

```python
from .sotf_handler import SotFMediaHandler, looks_like_sotf
```

### Edit 2 — register the handler instance

Find the existing instances + `FORMAT_HANDLERS` dict:

```python
JSON_HANDLER = JsonFormatHandler()
XML_HANDLER  = XmlFormatHandler()
HTML_HANDLER = HtmlFormatHandler()
TEXT_HANDLER = TextFormatHandler()
RAW_HANDLER  = RawFormatHandler()

FORMAT_HANDLERS: Dict[str, Any] = {
    "json": JSON_HANDLER,
    "xml":  XML_HANDLER,
    "html": HTML_HANDLER,
    "text": TEXT_HANDLER,
    "raw":  RAW_HANDLER,
}
```

Add the SotF instance and a `"sotf_media"` entry:

```python
JSON_HANDLER = JsonFormatHandler()
XML_HANDLER  = XmlFormatHandler()
HTML_HANDLER = HtmlFormatHandler()
TEXT_HANDLER = TextFormatHandler()
RAW_HANDLER  = RawFormatHandler()
SOTF_HANDLER = SotFMediaHandler()

FORMAT_HANDLERS: Dict[str, Any] = {
    "json":       JSON_HANDLER,
    "xml":        XML_HANDLER,
    "html":       HTML_HANDLER,
    "text":       TEXT_HANDLER,
    "raw":        RAW_HANDLER,
    "sotf_media": SOTF_HANDLER,
}
```

### Edit 3 — teach the sniffer to recognize SotF bodies

Find `sniff_body_mode`:

```python
def sniff_body_mode(body_bytes: bytes) -> str:
    if not body_bytes:
        return "raw"
    try:
        text = body_bytes.decode("utf-8", "replace")
    except Exception:
        return "raw"

    if _looks_like_json(text):
        return "json"
    if _looks_like_xml(text):
        return "xml"
    if _looks_like_html(text):
        return "html"
    if _looks_like_text(text):
        return "text"
    return "raw"
```

Add the SotF check BEFORE the JSON check (SotF bodies are JSON, so a
plain JSON check would claim them first):

```python
def sniff_body_mode(body_bytes: bytes) -> str:
    if not body_bytes:
        return "raw"
    try:
        text = body_bytes.decode("utf-8", "replace")
    except Exception:
        return "raw"

    if looks_like_sotf(text):
        return "sotf_media"
    if _looks_like_json(text):
        return "json"
    if _looks_like_xml(text):
        return "xml"
    if _looks_like_html(text):
        return "html"
    if _looks_like_text(text):
        return "text"
    return "raw"
```

That's the complete registry patch. Three edits, no other changes.

## Optional: Content-Type hint

If you want `Content-Type: application/x-sotf-frame` to be a weak hint
before the empirical sniff (parallel to the existing `application/json`
hint in `detect_request_handler`), add this branch near the existing
`"json" in ct`, `"xml" in ct` checks:

```python
if "x-sotf-frame" in ct:
    return SOTF_HANDLER
```

The sniffer will still confirm via `looks_like_sotf`, so this is just
a speed hint. Skip it if you'd rather keep the registry honest about
not trusting Content-Type.

## Verify after install

From `/opt/frognet_semantic/`:

```bash
python3 tests/test_sotf_handler.py
```

Expected output: 22 PASS lines, then "ALL SMOKE-TEST CHECKS PASS".

The test runs against your real `core/codec.py` and confirms:
- handler exposes the production FormatHandler interface
- template + dynamic round-trip through `SemanticCodec` preserves all
  session-scoped fields and the binary payload (byte-identical, via
  the base64 FINDING-1 workaround)
- `encode_request_diff` shrinks subsequent frames
- identical-content frames set `is_identical=True` (REQ_REPEAT path)
- the `looks_like_sotf` sniffer hook accepts SotF envelopes and rejects
  plain JSON / non-JSON / empty / wrong-marker bodies

If smoke test fails on the SemanticCodec round-trip, your local codec
or handler changes likely renamed something the codex expects. The
test prints the exact field that failed; fix and re-run.

## Wire body format

The handler claims any HTTP body whose top-level JSON dict carries
`"_sotf": 1`. Example:

```json
{
  "_sotf": 1,
  "session_id": "sotf-abc123",
  "codec": "opus",
  "sr": 48000,
  "layout": "stereo",
  "level_idx": 4,
  "seq": 42,
  "payload": "<base64-encoded media bytes>"
}
```

Five fields are session-scoped (`session_id`, `codec`, `sr`, `layout`,
`level_idx`) and become the per-session template after the first
REQ_FULL. Two fields (`seq`, `payload`) are per-frame and travel as
REQ_DIFF. Identical-content frames collapse to REQ_REPEAT (25 wire
bytes, the codex floor).

## FINDING-1 dependency

The `payload` field is currently mapped as `"string"` (TYPE_STR) with
base64 encoding rather than `"raw"` (TYPE_RAW). This is because
`core/codec.py` `_decode_fieldblock` currently shares a decode branch
between TYPE_RAW and TYPE_STR — both pass through
`.decode("utf-8", "replace")`, which mangles binary on the daemon side.
Cost of the workaround: ~33% overhead on the payload portion only
(session-scoped fields are unaffected).

Once `_decode_fieldblock` is fixed to keep TYPE_RAW as `bytes` on
decode:

1. In `core/sotf_handler.py`, change `type_map["payload"]` from
   `"string"` to `"raw"`.
2. Remove the `_coerce_payload_for_wire` call in `_extract` (or change
   it to a pass-through for bytes).
3. Remove the base64 decode in `decode_payload` (it'll be bytes
   already).

The smoke test will need its base64 expectations adjusted at that
point too.

## What this does NOT change

- `core/codec.py` — untouched
- `core/semcache_wire.py` — untouched
- `core/json_handler.py` / `xml_handler.py` / `html_handler.py` /
  `text_handler.py` — untouched
- `proxy/transport_semantic.py` — untouched
- `daemon/engine/session.py` — untouched

The codex is self-contained. Once registered in `format_registry.py`,
the proxy's existing `detect_request_handler` / `detect_reply_handler`
machinery routes SotF bodies to it automatically.

## Removal

If you need to back this out:

1. Revert the three edits in `format_registry.py`.
2. `rm /opt/frognet_semantic/core/sotf_handler.py`
3. `rm /opt/frognet_semantic/tests/test_sotf_handler.py`

No other files reference the SotF codex.
