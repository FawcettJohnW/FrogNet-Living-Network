/******/ (() => { // webpackBootstrap
/******/ 	"use strict";
/******/ 	var __webpack_modules__ = ({

/***/ "./node_modules/sdp/sdp.js":
/*!*********************************!*\
  !*** ./node_modules/sdp/sdp.js ***!
  \*********************************/
/***/ ((module) => {

/* eslint-env node */


// SDP helpers.
const SDPUtils = {};

// Generate an alphanumeric identifier for cname or mids.
// TODO: use UUIDs instead? https://gist.github.com/jed/982883
SDPUtils.generateIdentifier = function() {
  return Math.random().toString(36).substring(2, 12);
};

// The RTCP CNAME used by all peerconnections from the same JS.
SDPUtils.localCName = SDPUtils.generateIdentifier();

// Splits SDP into lines, dealing with both CRLF and LF.
SDPUtils.splitLines = function(blob) {
  return blob.trim().split('\n').map(line => line.trim());
};
// Splits SDP into sessionpart and mediasections. Ensures CRLF.
SDPUtils.splitSections = function(blob) {
  const parts = blob.split('\nm=');
  return parts.map((part, index) => (index > 0 ?
    'm=' + part : part).trim() + '\r\n');
};

// Returns the session description.
SDPUtils.getDescription = function(blob) {
  const sections = SDPUtils.splitSections(blob);
  return sections && sections[0];
};

// Returns the individual media sections.
SDPUtils.getMediaSections = function(blob) {
  const sections = SDPUtils.splitSections(blob);
  sections.shift();
  return sections;
};

// Returns lines that start with a certain prefix.
SDPUtils.matchPrefix = function(blob, prefix) {
  return SDPUtils.splitLines(blob).filter(line => line.indexOf(prefix) === 0);
};

// Parses an ICE candidate line. Sample input:
// candidate:702786350 2 udp 41819902 8.8.8.8 60769 typ relay raddr 8.8.8.8
// rport 55996"
// Input can be prefixed with a=.
SDPUtils.parseCandidate = function(line) {
  let parts;
  // Parse both variants.
  if (line.indexOf('a=candidate:') === 0) {
    parts = line.substring(12).split(' ');
  } else {
    parts = line.substring(10).split(' ');
  }

  const candidate = {
    foundation: parts[0],
    component: {1: 'rtp', 2: 'rtcp'}[parts[1]] || parts[1],
    protocol: parts[2].toLowerCase(),
    priority: parseInt(parts[3], 10),
    ip: parts[4],
    address: parts[4], // address is an alias for ip.
    port: parseInt(parts[5], 10),
    // skip parts[6] == 'typ'
    type: parts[7],
  };

  for (let i = 8; i < parts.length; i += 2) {
    switch (parts[i]) {
      case 'raddr':
        candidate.relatedAddress = parts[i + 1];
        break;
      case 'rport':
        candidate.relatedPort = parseInt(parts[i + 1], 10);
        break;
      case 'tcptype':
        candidate.tcpType = parts[i + 1];
        break;
      case 'ufrag':
        candidate.ufrag = parts[i + 1]; // for backward compatibility.
        candidate.usernameFragment = parts[i + 1];
        break;
      default: // extension handling, in particular ufrag. Don't overwrite.
        if (candidate[parts[i]] === undefined) {
          candidate[parts[i]] = parts[i + 1];
        }
        break;
    }
  }
  return candidate;
};

// Translates a candidate object into SDP candidate attribute.
// This does not include the a= prefix!
SDPUtils.writeCandidate = function(candidate) {
  const sdp = [];
  sdp.push(candidate.foundation);

  const component = candidate.component;
  if (component === 'rtp') {
    sdp.push(1);
  } else if (component === 'rtcp') {
    sdp.push(2);
  } else {
    sdp.push(component);
  }
  sdp.push(candidate.protocol.toUpperCase());
  sdp.push(candidate.priority);
  sdp.push(candidate.address || candidate.ip);
  sdp.push(candidate.port);

  const type = candidate.type;
  sdp.push('typ');
  sdp.push(type);
  if (type !== 'host' && candidate.relatedAddress &&
      candidate.relatedPort) {
    sdp.push('raddr');
    sdp.push(candidate.relatedAddress);
    sdp.push('rport');
    sdp.push(candidate.relatedPort);
  }
  if (candidate.tcpType && candidate.protocol.toLowerCase() === 'tcp') {
    sdp.push('tcptype');
    sdp.push(candidate.tcpType);
  }
  if (candidate.usernameFragment || candidate.ufrag) {
    sdp.push('ufrag');
    sdp.push(candidate.usernameFragment || candidate.ufrag);
  }
  return 'candidate:' + sdp.join(' ');
};

// Parses an ice-options line, returns an array of option tags.
// Sample input:
// a=ice-options:foo bar
SDPUtils.parseIceOptions = function(line) {
  return line.substring(14).split(' ');
};

// Parses a rtpmap line, returns RTCRtpCoddecParameters. Sample input:
// a=rtpmap:111 opus/48000/2
SDPUtils.parseRtpMap = function(line) {
  let parts = line.substring(9).split(' ');
  const parsed = {
    payloadType: parseInt(parts.shift(), 10), // was: id
  };

  parts = parts[0].split('/');

  parsed.name = parts[0];
  parsed.clockRate = parseInt(parts[1], 10); // was: clockrate
  parsed.channels = parts.length === 3 ? parseInt(parts[2], 10) : 1;
  // legacy alias, got renamed back to channels in ORTC.
  parsed.numChannels = parsed.channels;
  return parsed;
};

// Generates a rtpmap line from RTCRtpCodecCapability or
// RTCRtpCodecParameters.
SDPUtils.writeRtpMap = function(codec) {
  let pt = codec.payloadType;
  if (codec.preferredPayloadType !== undefined) {
    pt = codec.preferredPayloadType;
  }
  const channels = codec.channels || codec.numChannels || 1;
  return 'a=rtpmap:' + pt + ' ' + codec.name + '/' + codec.clockRate +
      (channels !== 1 ? '/' + channels : '') + '\r\n';
};

// Parses a extmap line (headerextension from RFC 5285). Sample input:
// a=extmap:2 urn:ietf:params:rtp-hdrext:toffset
// a=extmap:2/sendonly urn:ietf:params:rtp-hdrext:toffset
SDPUtils.parseExtmap = function(line) {
  const parts = line.substring(9).split(' ');
  return {
    id: parseInt(parts[0], 10),
    direction: parts[0].indexOf('/') > 0 ? parts[0].split('/')[1] : 'sendrecv',
    uri: parts[1],
    attributes: parts.slice(2).join(' '),
  };
};

// Generates an extmap line from RTCRtpHeaderExtensionParameters or
// RTCRtpHeaderExtension.
SDPUtils.writeExtmap = function(headerExtension) {
  return 'a=extmap:' + (headerExtension.id || headerExtension.preferredId) +
      (headerExtension.direction && headerExtension.direction !== 'sendrecv'
        ? '/' + headerExtension.direction
        : '') +
      ' ' + headerExtension.uri +
      (headerExtension.attributes ? ' ' + headerExtension.attributes : '') +
      '\r\n';
};

// Parses a fmtp line, returns dictionary. Sample input:
// a=fmtp:96 vbr=on;cng=on
// Also deals with vbr=on; cng=on
SDPUtils.parseFmtp = function(line) {
  const parsed = {};
  let kv;
  const parts = line.substring(line.indexOf(' ') + 1).split(';');
  for (let j = 0; j < parts.length; j++) {
    kv = parts[j].trim().split('=');
    parsed[kv[0].trim()] = kv[1];
  }
  return parsed;
};

// Generates a fmtp line from RTCRtpCodecCapability or RTCRtpCodecParameters.
SDPUtils.writeFmtp = function(codec) {
  let line = '';
  let pt = codec.payloadType;
  if (codec.preferredPayloadType !== undefined) {
    pt = codec.preferredPayloadType;
  }
  if (codec.parameters && Object.keys(codec.parameters).length) {
    const params = [];
    Object.keys(codec.parameters).forEach(param => {
      if (codec.parameters[param] !== undefined) {
        params.push(param + '=' + codec.parameters[param]);
      } else {
        params.push(param);
      }
    });
    line += 'a=fmtp:' + pt + ' ' + params.join(';') + '\r\n';
  }
  return line;
};

// Parses a rtcp-fb line, returns RTCPRtcpFeedback object. Sample input:
// a=rtcp-fb:98 nack rpsi
SDPUtils.parseRtcpFb = function(line) {
  const parts = line.substring(line.indexOf(' ') + 1).split(' ');
  return {
    type: parts.shift(),
    parameter: parts.join(' '),
  };
};

// Generate a=rtcp-fb lines from RTCRtpCodecCapability or RTCRtpCodecParameters.
SDPUtils.writeRtcpFb = function(codec) {
  let lines = '';
  let pt = codec.payloadType;
  if (codec.preferredPayloadType !== undefined) {
    pt = codec.preferredPayloadType;
  }
  if (codec.rtcpFeedback && codec.rtcpFeedback.length) {
    // FIXME: special handling for trr-int?
    codec.rtcpFeedback.forEach(fb => {
      lines += 'a=rtcp-fb:' + pt + ' ' + fb.type +
      (fb.parameter && fb.parameter.length ? ' ' + fb.parameter : '') +
          '\r\n';
    });
  }
  return lines;
};

// Parses a RFC 5576 ssrc media attribute. Sample input:
// a=ssrc:3735928559 cname:something
SDPUtils.parseSsrcMedia = function(line) {
  const sp = line.indexOf(' ');
  const parts = {
    ssrc: parseInt(line.substring(7, sp), 10),
  };
  const colon = line.indexOf(':', sp);
  if (colon > -1) {
    parts.attribute = line.substring(sp + 1, colon);
    parts.value = line.substring(colon + 1);
  } else {
    parts.attribute = line.substring(sp + 1);
  }
  return parts;
};

// Parse a ssrc-group line (see RFC 5576). Sample input:
// a=ssrc-group:semantics 12 34
SDPUtils.parseSsrcGroup = function(line) {
  const parts = line.substring(13).split(' ');
  return {
    semantics: parts.shift(),
    ssrcs: parts.map(ssrc => parseInt(ssrc, 10)),
  };
};

// Extracts the MID (RFC 5888) from a media section.
// Returns the MID or undefined if no mid line was found.
SDPUtils.getMid = function(mediaSection) {
  const mid = SDPUtils.matchPrefix(mediaSection, 'a=mid:')[0];
  if (mid) {
    return mid.substring(6);
  }
};

// Parses a fingerprint line for DTLS-SRTP.
SDPUtils.parseFingerprint = function(line) {
  const parts = line.substring(14).split(' ');
  return {
    algorithm: parts[0].toLowerCase(), // algorithm is case-sensitive in Edge.
    value: parts[1].toUpperCase(), // the definition is upper-case in RFC 4572.
  };
};

// Extracts DTLS parameters from SDP media section or sessionpart.
// FIXME: for consistency with other functions this should only
//   get the fingerprint line as input. See also getIceParameters.
SDPUtils.getDtlsParameters = function(mediaSection, sessionpart) {
  const lines = SDPUtils.matchPrefix(mediaSection + sessionpart,
    'a=fingerprint:');
  // Note: a=setup line is ignored since we use the 'auto' role in Edge.
  return {
    role: 'auto',
    fingerprints: lines.map(SDPUtils.parseFingerprint),
  };
};

// Serializes DTLS parameters to SDP.
SDPUtils.writeDtlsParameters = function(params, setupType) {
  let sdp = 'a=setup:' + setupType + '\r\n';
  params.fingerprints.forEach(fp => {
    sdp += 'a=fingerprint:' + fp.algorithm + ' ' + fp.value + '\r\n';
  });
  return sdp;
};

// Parses a=crypto lines into
//   https://rawgit.com/aboba/edgertc/master/msortc-rs4.html#dictionary-rtcsrtpsdesparameters-members
SDPUtils.parseCryptoLine = function(line) {
  const parts = line.substring(9).split(' ');
  return {
    tag: parseInt(parts[0], 10),
    cryptoSuite: parts[1],
    keyParams: parts[2],
    sessionParams: parts.slice(3),
  };
};

SDPUtils.writeCryptoLine = function(parameters) {
  return 'a=crypto:' + parameters.tag + ' ' +
    parameters.cryptoSuite + ' ' +
    (typeof parameters.keyParams === 'object'
      ? SDPUtils.writeCryptoKeyParams(parameters.keyParams)
      : parameters.keyParams) +
    (parameters.sessionParams ? ' ' + parameters.sessionParams.join(' ') : '') +
    '\r\n';
};

// Parses the crypto key parameters into
//   https://rawgit.com/aboba/edgertc/master/msortc-rs4.html#rtcsrtpkeyparam*
SDPUtils.parseCryptoKeyParams = function(keyParams) {
  if (keyParams.indexOf('inline:') !== 0) {
    return null;
  }
  const parts = keyParams.substring(7).split('|');
  return {
    keyMethod: 'inline',
    keySalt: parts[0],
    lifeTime: parts[1],
    mkiValue: parts[2] ? parts[2].split(':')[0] : undefined,
    mkiLength: parts[2] ? parts[2].split(':')[1] : undefined,
  };
};

SDPUtils.writeCryptoKeyParams = function(keyParams) {
  return keyParams.keyMethod + ':'
    + keyParams.keySalt +
    (keyParams.lifeTime ? '|' + keyParams.lifeTime : '') +
    (keyParams.mkiValue && keyParams.mkiLength
      ? '|' + keyParams.mkiValue + ':' + keyParams.mkiLength
      : '');
};

// Extracts all SDES parameters.
SDPUtils.getCryptoParameters = function(mediaSection, sessionpart) {
  const lines = SDPUtils.matchPrefix(mediaSection + sessionpart,
    'a=crypto:');
  return lines.map(SDPUtils.parseCryptoLine);
};

// Parses ICE information from SDP media section or sessionpart.
// FIXME: for consistency with other functions this should only
//   get the ice-ufrag and ice-pwd lines as input.
SDPUtils.getIceParameters = function(mediaSection, sessionpart) {
  const ufrag = SDPUtils.matchPrefix(mediaSection + sessionpart,
    'a=ice-ufrag:')[0];
  const pwd = SDPUtils.matchPrefix(mediaSection + sessionpart,
    'a=ice-pwd:')[0];
  if (!(ufrag && pwd)) {
    return null;
  }
  return {
    usernameFragment: ufrag.substring(12),
    password: pwd.substring(10),
  };
};

// Serializes ICE parameters to SDP.
SDPUtils.writeIceParameters = function(params) {
  let sdp = 'a=ice-ufrag:' + params.usernameFragment + '\r\n' +
      'a=ice-pwd:' + params.password + '\r\n';
  if (params.iceLite) {
    sdp += 'a=ice-lite\r\n';
  }
  return sdp;
};

// Parses the SDP media section and returns RTCRtpParameters.
SDPUtils.parseRtpParameters = function(mediaSection) {
  const description = {
    codecs: [],
    headerExtensions: [],
    fecMechanisms: [],
    rtcp: [],
  };
  const lines = SDPUtils.splitLines(mediaSection);
  const mline = lines[0].split(' ');
  description.profile = mline[2];
  for (let i = 3; i < mline.length; i++) { // find all codecs from mline[3..]
    const pt = mline[i];
    const rtpmapline = SDPUtils.matchPrefix(
      mediaSection, 'a=rtpmap:' + pt + ' ')[0];
    if (rtpmapline) {
      const codec = SDPUtils.parseRtpMap(rtpmapline);
      const fmtps = SDPUtils.matchPrefix(
        mediaSection, 'a=fmtp:' + pt + ' ');
      // Only the first a=fmtp:<pt> is considered.
      codec.parameters = fmtps.length ? SDPUtils.parseFmtp(fmtps[0]) : {};
      codec.rtcpFeedback = SDPUtils.matchPrefix(
        mediaSection, 'a=rtcp-fb:' + pt + ' ')
        .map(SDPUtils.parseRtcpFb);
      description.codecs.push(codec);
      // parse FEC mechanisms from rtpmap lines.
      switch (codec.name.toUpperCase()) {
        case 'RED':
        case 'ULPFEC':
          description.fecMechanisms.push(codec.name.toUpperCase());
          break;
        default: // only RED and ULPFEC are recognized as FEC mechanisms.
          break;
      }
    }
  }
  SDPUtils.matchPrefix(mediaSection, 'a=extmap:').forEach(line => {
    description.headerExtensions.push(SDPUtils.parseExtmap(line));
  });
  const wildcardRtcpFb = SDPUtils.matchPrefix(mediaSection, 'a=rtcp-fb:* ')
    .map(SDPUtils.parseRtcpFb);
  description.codecs.forEach(codec => {
    wildcardRtcpFb.forEach(fb=> {
      const duplicate = codec.rtcpFeedback.find(existingFeedback => {
        return existingFeedback.type === fb.type &&
          existingFeedback.parameter === fb.parameter;
      });
      if (!duplicate) {
        codec.rtcpFeedback.push(fb);
      }
    });
  });
  // FIXME: parse rtcp.
  return description;
};

// Generates parts of the SDP media section describing the capabilities /
// parameters.
SDPUtils.writeRtpDescription = function(kind, caps) {
  let sdp = '';

  // Build the mline.
  sdp += 'm=' + kind + ' ';
  sdp += caps.codecs.length > 0 ? '9' : '0'; // reject if no codecs.
  sdp += ' ' + (caps.profile || 'UDP/TLS/RTP/SAVPF') + ' ';
  sdp += caps.codecs.map(codec => {
    if (codec.preferredPayloadType !== undefined) {
      return codec.preferredPayloadType;
    }
    return codec.payloadType;
  }).join(' ') + '\r\n';

  sdp += 'c=IN IP4 0.0.0.0\r\n';
  sdp += 'a=rtcp:9 IN IP4 0.0.0.0\r\n';

  // Add a=rtpmap lines for each codec. Also fmtp and rtcp-fb.
  caps.codecs.forEach(codec => {
    sdp += SDPUtils.writeRtpMap(codec);
    sdp += SDPUtils.writeFmtp(codec);
    sdp += SDPUtils.writeRtcpFb(codec);
  });
  let maxptime = 0;
  caps.codecs.forEach(codec => {
    if (codec.maxptime > maxptime) {
      maxptime = codec.maxptime;
    }
  });
  if (maxptime > 0) {
    sdp += 'a=maxptime:' + maxptime + '\r\n';
  }

  if (caps.headerExtensions) {
    caps.headerExtensions.forEach(extension => {
      sdp += SDPUtils.writeExtmap(extension);
    });
  }
  // FIXME: write fecMechanisms.
  return sdp;
};

// Parses the SDP media section and returns an array of
// RTCRtpEncodingParameters.
SDPUtils.parseRtpEncodingParameters = function(mediaSection) {
  const encodingParameters = [];
  const description = SDPUtils.parseRtpParameters(mediaSection);
  const hasRed = description.fecMechanisms.indexOf('RED') !== -1;
  const hasUlpfec = description.fecMechanisms.indexOf('ULPFEC') !== -1;

  // filter a=ssrc:... cname:, ignore PlanB-msid
  const ssrcs = SDPUtils.matchPrefix(mediaSection, 'a=ssrc:')
    .map(line => SDPUtils.parseSsrcMedia(line))
    .filter(parts => parts.attribute === 'cname');
  const primarySsrc = ssrcs.length > 0 && ssrcs[0].ssrc;
  let secondarySsrc;

  const flows = SDPUtils.matchPrefix(mediaSection, 'a=ssrc-group:FID')
    .map(line => {
      const parts = line.substring(17).split(' ');
      return parts.map(part => parseInt(part, 10));
    });
  if (flows.length > 0 && flows[0].length > 1 && flows[0][0] === primarySsrc) {
    secondarySsrc = flows[0][1];
  }

  description.codecs.forEach(codec => {
    if (codec.name.toUpperCase() === 'RTX' && codec.parameters.apt) {
      let encParam = {
        ssrc: primarySsrc,
        codecPayloadType: parseInt(codec.parameters.apt, 10),
      };
      if (primarySsrc && secondarySsrc) {
        encParam.rtx = {ssrc: secondarySsrc};
      }
      encodingParameters.push(encParam);
      if (hasRed) {
        encParam = JSON.parse(JSON.stringify(encParam));
        encParam.fec = {
          ssrc: primarySsrc,
          mechanism: hasUlpfec ? 'red+ulpfec' : 'red',
        };
        encodingParameters.push(encParam);
      }
    }
  });
  if (encodingParameters.length === 0 && primarySsrc) {
    encodingParameters.push({
      ssrc: primarySsrc,
    });
  }

  // we support both b=AS and b=TIAS but interpret AS as TIAS.
  let bandwidth = SDPUtils.matchPrefix(mediaSection, 'b=');
  if (bandwidth.length) {
    if (bandwidth[0].indexOf('b=TIAS:') === 0) {
      bandwidth = parseInt(bandwidth[0].substring(7), 10);
    } else if (bandwidth[0].indexOf('b=AS:') === 0) {
      // use formula from JSEP to convert b=AS to TIAS value.
      bandwidth = parseInt(bandwidth[0].substring(5), 10) * 1000 * 0.95
          - (50 * 40 * 8);
    } else {
      bandwidth = undefined;
    }
    encodingParameters.forEach(params => {
      params.maxBitrate = bandwidth;
    });
  }
  return encodingParameters;
};

// parses http://draft.ortc.org/#rtcrtcpparameters*
SDPUtils.parseRtcpParameters = function(mediaSection) {
  const rtcpParameters = {};

  // Gets the first SSRC. Note that with RTX there might be multiple
  // SSRCs.
  const remoteSsrc = SDPUtils.matchPrefix(mediaSection, 'a=ssrc:')
    .map(line => SDPUtils.parseSsrcMedia(line))
    .filter(obj => obj.attribute === 'cname')[0];
  if (remoteSsrc) {
    rtcpParameters.cname = remoteSsrc.value;
    rtcpParameters.ssrc = remoteSsrc.ssrc;
  }

  // Edge uses the compound attribute instead of reducedSize
  // compound is !reducedSize
  const rsize = SDPUtils.matchPrefix(mediaSection, 'a=rtcp-rsize');
  rtcpParameters.reducedSize = rsize.length > 0;
  rtcpParameters.compound = rsize.length === 0;

  // parses the rtcp-mux attrіbute.
  // Note that Edge does not support unmuxed RTCP.
  const mux = SDPUtils.matchPrefix(mediaSection, 'a=rtcp-mux');
  rtcpParameters.mux = mux.length > 0;

  return rtcpParameters;
};

SDPUtils.writeRtcpParameters = function(rtcpParameters) {
  let sdp = '';
  if (rtcpParameters.reducedSize) {
    sdp += 'a=rtcp-rsize\r\n';
  }
  if (rtcpParameters.mux) {
    sdp += 'a=rtcp-mux\r\n';
  }
  if (rtcpParameters.ssrc !== undefined && rtcpParameters.cname) {
    sdp += 'a=ssrc:' + rtcpParameters.ssrc +
      ' cname:' + rtcpParameters.cname + '\r\n';
  }
  return sdp;
};


// parses either a=msid: or a=ssrc:... msid lines and returns
// the id of the MediaStream and MediaStreamTrack.
SDPUtils.parseMsid = function(mediaSection) {
  let parts;
  const spec = SDPUtils.matchPrefix(mediaSection, 'a=msid:');
  if (spec.length === 1) {
    parts = spec[0].substring(7).split(' ');
    return {stream: parts[0], track: parts[1]};
  }
  const planB = SDPUtils.matchPrefix(mediaSection, 'a=ssrc:')
    .map(line => SDPUtils.parseSsrcMedia(line))
    .filter(msidParts => msidParts.attribute === 'msid');
  if (planB.length > 0) {
    parts = planB[0].value.split(' ');
    return {stream: parts[0], track: parts[1]};
  }
};

// SCTP
// parses draft-ietf-mmusic-sctp-sdp-26 first and falls back
// to draft-ietf-mmusic-sctp-sdp-05
SDPUtils.parseSctpDescription = function(mediaSection) {
  const mline = SDPUtils.parseMLine(mediaSection);
  const maxSizeLine = SDPUtils.matchPrefix(mediaSection, 'a=max-message-size:');
  let maxMessageSize;
  if (maxSizeLine.length > 0) {
    maxMessageSize = parseInt(maxSizeLine[0].substring(19), 10);
  }
  if (isNaN(maxMessageSize)) {
    maxMessageSize = 65536;
  }
  const sctpPort = SDPUtils.matchPrefix(mediaSection, 'a=sctp-port:');
  if (sctpPort.length > 0) {
    return {
      port: parseInt(sctpPort[0].substring(12), 10),
      protocol: mline.fmt,
      maxMessageSize,
    };
  }
  const sctpMapLines = SDPUtils.matchPrefix(mediaSection, 'a=sctpmap:');
  if (sctpMapLines.length > 0) {
    const parts = sctpMapLines[0]
      .substring(10)
      .split(' ');
    return {
      port: parseInt(parts[0], 10),
      protocol: parts[1],
      maxMessageSize,
    };
  }
};

// SCTP
// outputs the draft-ietf-mmusic-sctp-sdp-26 version that all browsers
// support by now receiving in this format, unless we originally parsed
// as the draft-ietf-mmusic-sctp-sdp-05 format (indicated by the m-line
// protocol of DTLS/SCTP -- without UDP/ or TCP/)
SDPUtils.writeSctpDescription = function(media, sctp) {
  let output = [];
  if (media.protocol !== 'DTLS/SCTP') {
    output = [
      'm=' + media.kind + ' 9 ' + media.protocol + ' ' + sctp.protocol + '\r\n',
      'c=IN IP4 0.0.0.0\r\n',
      'a=sctp-port:' + sctp.port + '\r\n',
    ];
  } else {
    output = [
      'm=' + media.kind + ' 9 ' + media.protocol + ' ' + sctp.port + '\r\n',
      'c=IN IP4 0.0.0.0\r\n',
      'a=sctpmap:' + sctp.port + ' ' + sctp.protocol + ' 65535\r\n',
    ];
  }
  if (sctp.maxMessageSize !== undefined) {
    output.push('a=max-message-size:' + sctp.maxMessageSize + '\r\n');
  }
  return output.join('');
};

// Generate a session ID for SDP.
// https://tools.ietf.org/html/draft-ietf-rtcweb-jsep-20#section-5.2.1
// recommends using a cryptographically random +ve 64-bit value
// but right now this should be acceptable and within the right range
SDPUtils.generateSessionId = function() {
  return Math.random().toString().substr(2, 22);
};

// Write boiler plate for start of SDP
// sessId argument is optional - if not supplied it will
// be generated randomly
// sessVersion is optional and defaults to 2
// sessUser is optional and defaults to 'thisisadapterortc'
SDPUtils.writeSessionBoilerplate = function(sessId, sessVer, sessUser) {
  let sessionId;
  const version = sessVer !== undefined ? sessVer : 2;
  if (sessId) {
    sessionId = sessId;
  } else {
    sessionId = SDPUtils.generateSessionId();
  }
  const user = sessUser || 'thisisadapterortc';
  // FIXME: sess-id should be an NTP timestamp.
  return 'v=0\r\n' +
      'o=' + user + ' ' + sessionId + ' ' + version +
        ' IN IP4 127.0.0.1\r\n' +
      's=-\r\n' +
      't=0 0\r\n';
};

// Gets the direction from the mediaSection or the sessionpart.
SDPUtils.getDirection = function(mediaSection, sessionpart) {
  // Look for sendrecv, sendonly, recvonly, inactive, default to sendrecv.
  const lines = SDPUtils.splitLines(mediaSection);
  for (let i = 0; i < lines.length; i++) {
    switch (lines[i]) {
      case 'a=sendrecv':
      case 'a=sendonly':
      case 'a=recvonly':
      case 'a=inactive':
        return lines[i].substring(2);
      default:
        // FIXME: What should happen here?
    }
  }
  if (sessionpart) {
    return SDPUtils.getDirection(sessionpart);
  }
  return 'sendrecv';
};

SDPUtils.getKind = function(mediaSection) {
  const lines = SDPUtils.splitLines(mediaSection);
  const mline = lines[0].split(' ');
  return mline[0].substring(2);
};

SDPUtils.isRejected = function(mediaSection) {
  return mediaSection.split(' ', 2)[1] === '0';
};

SDPUtils.parseMLine = function(mediaSection) {
  const lines = SDPUtils.splitLines(mediaSection);
  const parts = lines[0].substring(2).split(' ');
  return {
    kind: parts[0],
    port: parseInt(parts[1], 10),
    protocol: parts[2],
    fmt: parts.slice(3).join(' '),
  };
};

SDPUtils.parseOLine = function(mediaSection) {
  const line = SDPUtils.matchPrefix(mediaSection, 'o=')[0];
  const parts = line.substring(2).split(' ');
  return {
    username: parts[0],
    sessionId: parts[1],
    sessionVersion: parseInt(parts[2], 10),
    netType: parts[3],
    addressType: parts[4],
    address: parts[5],
  };
};

// a very naive interpretation of a valid SDP.
SDPUtils.isValidSDP = function(blob) {
  if (typeof blob !== 'string' || blob.length === 0) {
    return false;
  }
  const lines = SDPUtils.splitLines(blob);
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].length < 2 || lines[i].charAt(1) !== '=') {
      return false;
    }
    // TODO: check the modifier a bit more.
  }
  return true;
};

// Expose public methods.
if (true) {
  module.exports = SDPUtils;
}


/***/ }),

/***/ "./node_modules/webrtc-adapter/src/js/adapter_core.js":
/*!************************************************************!*\
  !*** ./node_modules/webrtc-adapter/src/js/adapter_core.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": () => (__WEBPACK_DEFAULT_EXPORT__)
/* harmony export */ });
/* harmony import */ var _adapter_factory_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./adapter_factory.js */ "./node_modules/webrtc-adapter/src/js/adapter_factory.js");
/*
 *  Copyright (c) 2016 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree.
 */
/* eslint-env node */





const adapter =
  (0,_adapter_factory_js__WEBPACK_IMPORTED_MODULE_0__.adapterFactory)({window: typeof window === 'undefined' ? undefined : window});
/* harmony default export */ const __WEBPACK_DEFAULT_EXPORT__ = (adapter);


/***/ }),

/***/ "./node_modules/webrtc-adapter/src/js/adapter_factory.js":
/*!***************************************************************!*\
  !*** ./node_modules/webrtc-adapter/src/js/adapter_factory.js ***!
  \***************************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   adapterFactory: () => (/* binding */ adapterFactory)
/* harmony export */ });
/* harmony import */ var _utils__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./utils */ "./node_modules/webrtc-adapter/src/js/utils.js");
/* harmony import */ var _chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./chrome/chrome_shim */ "./node_modules/webrtc-adapter/src/js/chrome/chrome_shim.js");
/* harmony import */ var _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./firefox/firefox_shim */ "./node_modules/webrtc-adapter/src/js/firefox/firefox_shim.js");
/* harmony import */ var _safari_safari_shim__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./safari/safari_shim */ "./node_modules/webrtc-adapter/src/js/safari/safari_shim.js");
/* harmony import */ var _common_shim__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! ./common_shim */ "./node_modules/webrtc-adapter/src/js/common_shim.js");
/* harmony import */ var sdp__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! sdp */ "./node_modules/sdp/sdp.js");
/* harmony import */ var sdp__WEBPACK_IMPORTED_MODULE_5___default = /*#__PURE__*/__webpack_require__.n(sdp__WEBPACK_IMPORTED_MODULE_5__);
/*
 *  Copyright (c) 2016 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree.
 */


// Browser shims.






// Shimming starts here.
function adapterFactory({window} = {}, options = {
  shimChrome: true,
  shimFirefox: true,
  shimSafari: true,
}) {
  // Utils.
  const logging = _utils__WEBPACK_IMPORTED_MODULE_0__.log;
  const browserDetails = _utils__WEBPACK_IMPORTED_MODULE_0__.detectBrowser(window);

  const adapter = {
    browserDetails,
    commonShim: _common_shim__WEBPACK_IMPORTED_MODULE_4__,
    extractVersion: _utils__WEBPACK_IMPORTED_MODULE_0__.extractVersion,
    disableLog: _utils__WEBPACK_IMPORTED_MODULE_0__.disableLog,
    disableWarnings: _utils__WEBPACK_IMPORTED_MODULE_0__.disableWarnings,
    // Expose sdp as a convenience. For production apps include directly.
    sdp: sdp__WEBPACK_IMPORTED_MODULE_5__,
  };

  // Shim browser if found.
  switch (browserDetails.browser) {
    case 'chrome':
      if (!_chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__ || !_chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__.shimPeerConnection ||
          !options.shimChrome) {
        logging('Chrome shim is not included in this adapter release.');
        return adapter;
      }
      if (browserDetails.version === null) {
        logging('Chrome shim can not determine version, not shimming.');
        return adapter;
      }
      logging('adapter.js shimming chrome.');
      // Export to the adapter global object visible in the browser.
      adapter.browserShim = _chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__;

      // Must be called before shimPeerConnection.
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimAddIceCandidateNullOrEmpty(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimParameterlessSetLocalDescription(window, browserDetails);

      _chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__.shimGetUserMedia(window, browserDetails);
      _chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__.shimMediaStream(window, browserDetails);
      _chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__.shimPeerConnection(window, browserDetails);
      _chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__.shimOnTrack(window, browserDetails);
      _chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__.shimAddTrackRemoveTrack(window, browserDetails);
      _chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__.shimGetSendersWithDtmf(window, browserDetails);
      _chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__.shimGetStats(window, browserDetails);
      _chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__.shimSenderReceiverGetStats(window, browserDetails);
      _chrome_chrome_shim__WEBPACK_IMPORTED_MODULE_1__.fixNegotiationNeeded(window, browserDetails);

      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimRTCIceCandidate(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimRTCIceCandidateRelayProtocol(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimConnectionState(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimMaxMessageSize(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimSendThrowTypeError(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.removeExtmapAllowMixed(window, browserDetails);
      break;
    case 'firefox':
      if (!_firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__ || !_firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimPeerConnection ||
          !options.shimFirefox) {
        logging('Firefox shim is not included in this adapter release.');
        return adapter;
      }
      logging('adapter.js shimming firefox.');
      // Export to the adapter global object visible in the browser.
      adapter.browserShim = _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__;

      // Must be called before shimPeerConnection.
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimAddIceCandidateNullOrEmpty(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimParameterlessSetLocalDescription(window, browserDetails);

      _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimGetUserMedia(window, browserDetails);
      _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimPeerConnection(window, browserDetails);
      _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimOnTrack(window, browserDetails);
      _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimRemoveStream(window, browserDetails);
      _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimSenderGetStats(window, browserDetails);
      _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimReceiverGetStats(window, browserDetails);
      _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimRTCDataChannel(window, browserDetails);
      _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimAddTransceiver(window, browserDetails);
      _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimGetParameters(window, browserDetails);
      _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimCreateOffer(window, browserDetails);
      _firefox_firefox_shim__WEBPACK_IMPORTED_MODULE_2__.shimCreateAnswer(window, browserDetails);

      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimRTCIceCandidate(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimConnectionState(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimMaxMessageSize(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimSendThrowTypeError(window, browserDetails);
      break;
    case 'safari':
      if (!_safari_safari_shim__WEBPACK_IMPORTED_MODULE_3__ || !options.shimSafari) {
        logging('Safari shim is not included in this adapter release.');
        return adapter;
      }
      logging('adapter.js shimming safari.');
      // Export to the adapter global object visible in the browser.
      adapter.browserShim = _safari_safari_shim__WEBPACK_IMPORTED_MODULE_3__;

      // Must be called before shimCallbackAPI.
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimAddIceCandidateNullOrEmpty(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimParameterlessSetLocalDescription(window, browserDetails);

      _safari_safari_shim__WEBPACK_IMPORTED_MODULE_3__.shimRTCIceServerUrls(window, browserDetails);
      _safari_safari_shim__WEBPACK_IMPORTED_MODULE_3__.shimCreateOfferLegacy(window, browserDetails);
      _safari_safari_shim__WEBPACK_IMPORTED_MODULE_3__.shimCallbacksAPI(window, browserDetails);
      _safari_safari_shim__WEBPACK_IMPORTED_MODULE_3__.shimLocalStreamsAPI(window, browserDetails);
      _safari_safari_shim__WEBPACK_IMPORTED_MODULE_3__.shimRemoteStreamsAPI(window, browserDetails);
      _safari_safari_shim__WEBPACK_IMPORTED_MODULE_3__.shimTrackEventTransceiver(window, browserDetails);
      _safari_safari_shim__WEBPACK_IMPORTED_MODULE_3__.shimGetUserMedia(window, browserDetails);
      _safari_safari_shim__WEBPACK_IMPORTED_MODULE_3__.shimAudioContext(window, browserDetails);

      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimRTCIceCandidate(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimRTCIceCandidateRelayProtocol(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimMaxMessageSize(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.shimSendThrowTypeError(window, browserDetails);
      _common_shim__WEBPACK_IMPORTED_MODULE_4__.removeExtmapAllowMixed(window, browserDetails);
      break;
    default:
      logging('Unsupported browser!');
      break;
  }

  return adapter;
}


/***/ }),

/***/ "./node_modules/webrtc-adapter/src/js/chrome/chrome_shim.js":
/*!******************************************************************!*\
  !*** ./node_modules/webrtc-adapter/src/js/chrome/chrome_shim.js ***!
  \******************************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   fixNegotiationNeeded: () => (/* binding */ fixNegotiationNeeded),
/* harmony export */   shimAddTrackRemoveTrack: () => (/* binding */ shimAddTrackRemoveTrack),
/* harmony export */   shimAddTrackRemoveTrackWithNative: () => (/* binding */ shimAddTrackRemoveTrackWithNative),
/* harmony export */   shimGetDisplayMedia: () => (/* reexport safe */ _getdisplaymedia__WEBPACK_IMPORTED_MODULE_2__.shimGetDisplayMedia),
/* harmony export */   shimGetSendersWithDtmf: () => (/* binding */ shimGetSendersWithDtmf),
/* harmony export */   shimGetStats: () => (/* binding */ shimGetStats),
/* harmony export */   shimGetUserMedia: () => (/* reexport safe */ _getusermedia__WEBPACK_IMPORTED_MODULE_1__.shimGetUserMedia),
/* harmony export */   shimMediaStream: () => (/* binding */ shimMediaStream),
/* harmony export */   shimOnTrack: () => (/* binding */ shimOnTrack),
/* harmony export */   shimPeerConnection: () => (/* binding */ shimPeerConnection),
/* harmony export */   shimSenderReceiverGetStats: () => (/* binding */ shimSenderReceiverGetStats)
/* harmony export */ });
/* harmony import */ var _utils_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../utils.js */ "./node_modules/webrtc-adapter/src/js/utils.js");
/* harmony import */ var _getusermedia__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./getusermedia */ "./node_modules/webrtc-adapter/src/js/chrome/getusermedia.js");
/* harmony import */ var _getdisplaymedia__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./getdisplaymedia */ "./node_modules/webrtc-adapter/src/js/chrome/getdisplaymedia.js");
/*
 *  Copyright (c) 2016 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree.
 */
/* eslint-env node */






function shimMediaStream(window) {
  window.MediaStream = window.MediaStream || window.webkitMediaStream;
}

function shimOnTrack(window) {
  if (typeof window === 'object' && window.RTCPeerConnection && !('ontrack' in
      window.RTCPeerConnection.prototype)) {
    Object.defineProperty(window.RTCPeerConnection.prototype, 'ontrack', {
      get() {
        return this._ontrack;
      },
      set(f) {
        if (this._ontrack) {
          this.removeEventListener('track', this._ontrack);
        }
        this.addEventListener('track', this._ontrack = f);
      },
      enumerable: true,
      configurable: true
    });
    const origSetRemoteDescription =
        window.RTCPeerConnection.prototype.setRemoteDescription;
    window.RTCPeerConnection.prototype.setRemoteDescription =
      function setRemoteDescription() {
        if (!this._ontrackpoly) {
          this._ontrackpoly = (e) => {
            // onaddstream does not fire when a track is added to an existing
            // stream. But stream.onaddtrack is implemented so we use that.
            e.stream.addEventListener('addtrack', te => {
              let receiver;
              if (window.RTCPeerConnection.prototype.getReceivers) {
                receiver = this.getReceivers()
                  .find(r => r.track && r.track.id === te.track.id);
              } else {
                receiver = {track: te.track};
              }

              const event = new Event('track');
              event.track = te.track;
              event.receiver = receiver;
              event.transceiver = {receiver};
              event.streams = [e.stream];
              this.dispatchEvent(event);
            });
            e.stream.getTracks().forEach(track => {
              let receiver;
              if (window.RTCPeerConnection.prototype.getReceivers) {
                receiver = this.getReceivers()
                  .find(r => r.track && r.track.id === track.id);
              } else {
                receiver = {track};
              }
              const event = new Event('track');
              event.track = track;
              event.receiver = receiver;
              event.transceiver = {receiver};
              event.streams = [e.stream];
              this.dispatchEvent(event);
            });
          };
          this.addEventListener('addstream', this._ontrackpoly);
        }
        return origSetRemoteDescription.apply(this, arguments);
      };
  } else {
    // even if RTCRtpTransceiver is in window, it is only used and
    // emitted in unified-plan. Unfortunately this means we need
    // to unconditionally wrap the event.
    _utils_js__WEBPACK_IMPORTED_MODULE_0__.wrapPeerConnectionEvent(window, 'track', e => {
      if (!e.transceiver) {
        Object.defineProperty(e, 'transceiver',
          {value: {receiver: e.receiver}});
      }
      return e;
    });
  }
}

function shimGetSendersWithDtmf(window) {
  // Overrides addTrack/removeTrack, depends on shimAddTrackRemoveTrack.
  if (typeof window === 'object' && window.RTCPeerConnection &&
      !('getSenders' in window.RTCPeerConnection.prototype) &&
      'createDTMFSender' in window.RTCPeerConnection.prototype) {
    const shimSenderWithDtmf = function(pc, track) {
      return {
        track,
        get dtmf() {
          if (this._dtmf === undefined) {
            if (track.kind === 'audio') {
              this._dtmf = pc.createDTMFSender(track);
            } else {
              this._dtmf = null;
            }
          }
          return this._dtmf;
        },
        _pc: pc
      };
    };

    // augment addTrack when getSenders is not available.
    if (!window.RTCPeerConnection.prototype.getSenders) {
      window.RTCPeerConnection.prototype.getSenders = function getSenders() {
        this._senders = this._senders || [];
        return this._senders.slice(); // return a copy of the internal state.
      };
      const origAddTrack = window.RTCPeerConnection.prototype.addTrack;
      window.RTCPeerConnection.prototype.addTrack =
        function addTrack(track, stream) {
          let sender = origAddTrack.apply(this, arguments);
          if (!sender) {
            sender = shimSenderWithDtmf(this, track);
            this._senders.push(sender);
          }
          return sender;
        };

      const origRemoveTrack = window.RTCPeerConnection.prototype.removeTrack;
      window.RTCPeerConnection.prototype.removeTrack =
        function removeTrack(sender) {
          origRemoveTrack.apply(this, arguments);
          const idx = this._senders.indexOf(sender);
          if (idx !== -1) {
            this._senders.splice(idx, 1);
          }
        };
    }
    const origAddStream = window.RTCPeerConnection.prototype.addStream;
    window.RTCPeerConnection.prototype.addStream = function addStream(stream) {
      this._senders = this._senders || [];
      origAddStream.apply(this, [stream]);
      stream.getTracks().forEach(track => {
        this._senders.push(shimSenderWithDtmf(this, track));
      });
    };

    const origRemoveStream = window.RTCPeerConnection.prototype.removeStream;
    window.RTCPeerConnection.prototype.removeStream =
      function removeStream(stream) {
        this._senders = this._senders || [];
        origRemoveStream.apply(this, [stream]);

        stream.getTracks().forEach(track => {
          const sender = this._senders.find(s => s.track === track);
          if (sender) { // remove sender
            this._senders.splice(this._senders.indexOf(sender), 1);
          }
        });
      };
  } else if (typeof window === 'object' && window.RTCPeerConnection &&
             'getSenders' in window.RTCPeerConnection.prototype &&
             'createDTMFSender' in window.RTCPeerConnection.prototype &&
             window.RTCRtpSender &&
             !('dtmf' in window.RTCRtpSender.prototype)) {
    const origGetSenders = window.RTCPeerConnection.prototype.getSenders;
    window.RTCPeerConnection.prototype.getSenders = function getSenders() {
      const senders = origGetSenders.apply(this, []);
      senders.forEach(sender => sender._pc = this);
      return senders;
    };

    Object.defineProperty(window.RTCRtpSender.prototype, 'dtmf', {
      get() {
        if (this._dtmf === undefined) {
          if (this.track.kind === 'audio') {
            this._dtmf = this._pc.createDTMFSender(this.track);
          } else {
            this._dtmf = null;
          }
        }
        return this._dtmf;
      }
    });
  }
}

function shimGetStats(window) {
  if (!window.RTCPeerConnection) {
    return;
  }

  const origGetStats = window.RTCPeerConnection.prototype.getStats;
  window.RTCPeerConnection.prototype.getStats = function getStats() {
    const [selector, onSucc, onErr] = arguments;

    // If selector is a function then we are in the old style stats so just
    // pass back the original getStats format to avoid breaking old users.
    if (arguments.length > 0 && typeof selector === 'function') {
      return origGetStats.apply(this, arguments);
    }

    // When spec-style getStats is supported, return those when called with
    // either no arguments or the selector argument is null.
    if (origGetStats.length === 0 && (arguments.length === 0 ||
        typeof selector !== 'function')) {
      return origGetStats.apply(this, []);
    }

    const fixChromeStats_ = function(response) {
      const standardReport = {};
      const reports = response.result();
      reports.forEach(report => {
        const standardStats = {
          id: report.id,
          timestamp: report.timestamp,
          type: {
            localcandidate: 'local-candidate',
            remotecandidate: 'remote-candidate'
          }[report.type] || report.type
        };
        report.names().forEach(name => {
          standardStats[name] = report.stat(name);
        });
        standardReport[standardStats.id] = standardStats;
      });

      return standardReport;
    };

    // shim getStats with maplike support
    const makeMapStats = function(stats) {
      return new Map(Object.keys(stats).map(key => [key, stats[key]]));
    };

    if (arguments.length >= 2) {
      const successCallbackWrapper_ = function(response) {
        onSucc(makeMapStats(fixChromeStats_(response)));
      };

      return origGetStats.apply(this, [successCallbackWrapper_,
        selector]);
    }

    // promise-support
    return new Promise((resolve, reject) => {
      origGetStats.apply(this, [
        function(response) {
          resolve(makeMapStats(fixChromeStats_(response)));
        }, reject]);
    }).then(onSucc, onErr);
  };
}

function shimSenderReceiverGetStats(window) {
  if (!(typeof window === 'object' && window.RTCPeerConnection &&
      window.RTCRtpSender && window.RTCRtpReceiver)) {
    return;
  }

  // shim sender stats.
  if (!('getStats' in window.RTCRtpSender.prototype)) {
    const origGetSenders = window.RTCPeerConnection.prototype.getSenders;
    if (origGetSenders) {
      window.RTCPeerConnection.prototype.getSenders = function getSenders() {
        const senders = origGetSenders.apply(this, []);
        senders.forEach(sender => sender._pc = this);
        return senders;
      };
    }

    const origAddTrack = window.RTCPeerConnection.prototype.addTrack;
    if (origAddTrack) {
      window.RTCPeerConnection.prototype.addTrack = function addTrack() {
        const sender = origAddTrack.apply(this, arguments);
        sender._pc = this;
        return sender;
      };
    }
    window.RTCRtpSender.prototype.getStats = function getStats() {
      const sender = this;
      return this._pc.getStats().then(result =>
        /* Note: this will include stats of all senders that
         *   send a track with the same id as sender.track as
         *   it is not possible to identify the RTCRtpSender.
         */
        _utils_js__WEBPACK_IMPORTED_MODULE_0__.filterStats(result, sender.track, true));
    };
  }

  // shim receiver stats.
  if (!('getStats' in window.RTCRtpReceiver.prototype)) {
    const origGetReceivers = window.RTCPeerConnection.prototype.getReceivers;
    if (origGetReceivers) {
      window.RTCPeerConnection.prototype.getReceivers =
        function getReceivers() {
          const receivers = origGetReceivers.apply(this, []);
          receivers.forEach(receiver => receiver._pc = this);
          return receivers;
        };
    }
    _utils_js__WEBPACK_IMPORTED_MODULE_0__.wrapPeerConnectionEvent(window, 'track', e => {
      e.receiver._pc = e.srcElement;
      return e;
    });
    window.RTCRtpReceiver.prototype.getStats = function getStats() {
      const receiver = this;
      return this._pc.getStats().then(result =>
        _utils_js__WEBPACK_IMPORTED_MODULE_0__.filterStats(result, receiver.track, false));
    };
  }

  if (!('getStats' in window.RTCRtpSender.prototype &&
      'getStats' in window.RTCRtpReceiver.prototype)) {
    return;
  }

  // shim RTCPeerConnection.getStats(track).
  const origGetStats = window.RTCPeerConnection.prototype.getStats;
  window.RTCPeerConnection.prototype.getStats = function getStats() {
    if (arguments.length > 0 &&
        arguments[0] instanceof window.MediaStreamTrack) {
      const track = arguments[0];
      let sender;
      let receiver;
      let err;
      this.getSenders().forEach(s => {
        if (s.track === track) {
          if (sender) {
            err = true;
          } else {
            sender = s;
          }
        }
      });
      this.getReceivers().forEach(r => {
        if (r.track === track) {
          if (receiver) {
            err = true;
          } else {
            receiver = r;
          }
        }
        return r.track === track;
      });
      if (err || (sender && receiver)) {
        return Promise.reject(new DOMException(
          'There are more than one sender or receiver for the track.',
          'InvalidAccessError'));
      } else if (sender) {
        return sender.getStats();
      } else if (receiver) {
        return receiver.getStats();
      }
      return Promise.reject(new DOMException(
        'There is no sender or receiver for the track.',
        'InvalidAccessError'));
    }
    return origGetStats.apply(this, arguments);
  };
}

function shimAddTrackRemoveTrackWithNative(window) {
  // shim addTrack/removeTrack with native variants in order to make
  // the interactions with legacy getLocalStreams behave as in other browsers.
  // Keeps a mapping stream.id => [stream, rtpsenders...]
  window.RTCPeerConnection.prototype.getLocalStreams =
    function getLocalStreams() {
      this._shimmedLocalStreams = this._shimmedLocalStreams || {};
      return Object.keys(this._shimmedLocalStreams)
        .map(streamId => this._shimmedLocalStreams[streamId][0]);
    };

  const origAddTrack = window.RTCPeerConnection.prototype.addTrack;
  window.RTCPeerConnection.prototype.addTrack =
    function addTrack(track, stream) {
      if (!stream) {
        return origAddTrack.apply(this, arguments);
      }
      this._shimmedLocalStreams = this._shimmedLocalStreams || {};

      const sender = origAddTrack.apply(this, arguments);
      if (!this._shimmedLocalStreams[stream.id]) {
        this._shimmedLocalStreams[stream.id] = [stream, sender];
      } else if (this._shimmedLocalStreams[stream.id].indexOf(sender) === -1) {
        this._shimmedLocalStreams[stream.id].push(sender);
      }
      return sender;
    };

  const origAddStream = window.RTCPeerConnection.prototype.addStream;
  window.RTCPeerConnection.prototype.addStream = function addStream(stream) {
    this._shimmedLocalStreams = this._shimmedLocalStreams || {};

    stream.getTracks().forEach(track => {
      const alreadyExists = this.getSenders().find(s => s.track === track);
      if (alreadyExists) {
        throw new DOMException('Track already exists.',
          'InvalidAccessError');
      }
    });
    const existingSenders = this.getSenders();
    origAddStream.apply(this, arguments);
    const newSenders = this.getSenders()
      .filter(newSender => existingSenders.indexOf(newSender) === -1);
    this._shimmedLocalStreams[stream.id] = [stream].concat(newSenders);
  };

  const origRemoveStream = window.RTCPeerConnection.prototype.removeStream;
  window.RTCPeerConnection.prototype.removeStream =
    function removeStream(stream) {
      this._shimmedLocalStreams = this._shimmedLocalStreams || {};
      delete this._shimmedLocalStreams[stream.id];
      return origRemoveStream.apply(this, arguments);
    };

  const origRemoveTrack = window.RTCPeerConnection.prototype.removeTrack;
  window.RTCPeerConnection.prototype.removeTrack =
    function removeTrack(sender) {
      this._shimmedLocalStreams = this._shimmedLocalStreams || {};
      if (sender) {
        Object.keys(this._shimmedLocalStreams).forEach(streamId => {
          const idx = this._shimmedLocalStreams[streamId].indexOf(sender);
          if (idx !== -1) {
            this._shimmedLocalStreams[streamId].splice(idx, 1);
          }
          if (this._shimmedLocalStreams[streamId].length === 1) {
            delete this._shimmedLocalStreams[streamId];
          }
        });
      }
      return origRemoveTrack.apply(this, arguments);
    };
}

function shimAddTrackRemoveTrack(window, browserDetails) {
  if (!window.RTCPeerConnection) {
    return;
  }
  // shim addTrack and removeTrack.
  if (window.RTCPeerConnection.prototype.addTrack &&
      browserDetails.version >= 65) {
    return shimAddTrackRemoveTrackWithNative(window);
  }

  // also shim pc.getLocalStreams when addTrack is shimmed
  // to return the original streams.
  const origGetLocalStreams = window.RTCPeerConnection.prototype
    .getLocalStreams;
  window.RTCPeerConnection.prototype.getLocalStreams =
    function getLocalStreams() {
      const nativeStreams = origGetLocalStreams.apply(this);
      this._reverseStreams = this._reverseStreams || {};
      return nativeStreams.map(stream => this._reverseStreams[stream.id]);
    };

  const origAddStream = window.RTCPeerConnection.prototype.addStream;
  window.RTCPeerConnection.prototype.addStream = function addStream(stream) {
    this._streams = this._streams || {};
    this._reverseStreams = this._reverseStreams || {};

    stream.getTracks().forEach(track => {
      const alreadyExists = this.getSenders().find(s => s.track === track);
      if (alreadyExists) {
        throw new DOMException('Track already exists.',
          'InvalidAccessError');
      }
    });
    // Add identity mapping for consistency with addTrack.
    // Unless this is being used with a stream from addTrack.
    if (!this._reverseStreams[stream.id]) {
      const newStream = new window.MediaStream(stream.getTracks());
      this._streams[stream.id] = newStream;
      this._reverseStreams[newStream.id] = stream;
      stream = newStream;
    }
    origAddStream.apply(this, [stream]);
  };

  const origRemoveStream = window.RTCPeerConnection.prototype.removeStream;
  window.RTCPeerConnection.prototype.removeStream =
    function removeStream(stream) {
      this._streams = this._streams || {};
      this._reverseStreams = this._reverseStreams || {};

      origRemoveStream.apply(this, [(this._streams[stream.id] || stream)]);
      delete this._reverseStreams[(this._streams[stream.id] ?
        this._streams[stream.id].id : stream.id)];
      delete this._streams[stream.id];
    };

  window.RTCPeerConnection.prototype.addTrack =
    function addTrack(track, stream) {
      if (this.signalingState === 'closed') {
        throw new DOMException(
          'The RTCPeerConnection\'s signalingState is \'closed\'.',
          'InvalidStateError');
      }
      const streams = [].slice.call(arguments, 1);
      if (streams.length !== 1 ||
          !streams[0].getTracks().find(t => t === track)) {
        // this is not fully correct but all we can manage without
        // [[associated MediaStreams]] internal slot.
        throw new DOMException(
          'The adapter.js addTrack polyfill only supports a single ' +
          ' stream which is associated with the specified track.',
          'NotSupportedError');
      }

      const alreadyExists = this.getSenders().find(s => s.track === track);
      if (alreadyExists) {
        throw new DOMException('Track already exists.',
          'InvalidAccessError');
      }

      this._streams = this._streams || {};
      this._reverseStreams = this._reverseStreams || {};
      const oldStream = this._streams[stream.id];
      if (oldStream) {
        // this is using odd Chrome behaviour, use with caution:
        // https://bugs.chromium.org/p/webrtc/issues/detail?id=7815
        // Note: we rely on the high-level addTrack/dtmf shim to
        // create the sender with a dtmf sender.
        oldStream.addTrack(track);

        // Trigger ONN async.
        Promise.resolve().then(() => {
          this.dispatchEvent(new Event('negotiationneeded'));
        });
      } else {
        const newStream = new window.MediaStream([track]);
        this._streams[stream.id] = newStream;
        this._reverseStreams[newStream.id] = stream;
        this.addStream(newStream);
      }
      return this.getSenders().find(s => s.track === track);
    };

  // replace the internal stream id with the external one and
  // vice versa.
  function replaceInternalStreamId(pc, description) {
    let sdp = description.sdp;
    Object.keys(pc._reverseStreams || []).forEach(internalId => {
      const externalStream = pc._reverseStreams[internalId];
      const internalStream = pc._streams[externalStream.id];
      sdp = sdp.replace(new RegExp(internalStream.id, 'g'),
        externalStream.id);
    });
    return new RTCSessionDescription({
      type: description.type,
      sdp
    });
  }
  function replaceExternalStreamId(pc, description) {
    let sdp = description.sdp;
    Object.keys(pc._reverseStreams || []).forEach(internalId => {
      const externalStream = pc._reverseStreams[internalId];
      const internalStream = pc._streams[externalStream.id];
      sdp = sdp.replace(new RegExp(externalStream.id, 'g'),
        internalStream.id);
    });
    return new RTCSessionDescription({
      type: description.type,
      sdp
    });
  }
  ['createOffer', 'createAnswer'].forEach(function(method) {
    const nativeMethod = window.RTCPeerConnection.prototype[method];
    const methodObj = {[method]() {
      const args = arguments;
      const isLegacyCall = arguments.length &&
          typeof arguments[0] === 'function';
      if (isLegacyCall) {
        return nativeMethod.apply(this, [
          (description) => {
            const desc = replaceInternalStreamId(this, description);
            args[0].apply(null, [desc]);
          },
          (err) => {
            if (args[1]) {
              args[1].apply(null, err);
            }
          }, arguments[2]
        ]);
      }
      return nativeMethod.apply(this, arguments)
        .then(description => replaceInternalStreamId(this, description));
    }};
    window.RTCPeerConnection.prototype[method] = methodObj[method];
  });

  const origSetLocalDescription =
      window.RTCPeerConnection.prototype.setLocalDescription;
  window.RTCPeerConnection.prototype.setLocalDescription =
    function setLocalDescription() {
      if (!arguments.length || !arguments[0].type) {
        return origSetLocalDescription.apply(this, arguments);
      }
      arguments[0] = replaceExternalStreamId(this, arguments[0]);
      return origSetLocalDescription.apply(this, arguments);
    };

  // TODO: mangle getStats: https://w3c.github.io/webrtc-stats/#dom-rtcmediastreamstats-streamidentifier

  const origLocalDescription = Object.getOwnPropertyDescriptor(
    window.RTCPeerConnection.prototype, 'localDescription');
  Object.defineProperty(window.RTCPeerConnection.prototype,
    'localDescription', {
      get() {
        const description = origLocalDescription.get.apply(this);
        if (description.type === '') {
          return description;
        }
        return replaceInternalStreamId(this, description);
      }
    });

  window.RTCPeerConnection.prototype.removeTrack =
    function removeTrack(sender) {
      if (this.signalingState === 'closed') {
        throw new DOMException(
          'The RTCPeerConnection\'s signalingState is \'closed\'.',
          'InvalidStateError');
      }
      // We can not yet check for sender instanceof RTCRtpSender
      // since we shim RTPSender. So we check if sender._pc is set.
      if (!sender._pc) {
        throw new DOMException('Argument 1 of RTCPeerConnection.removeTrack ' +
            'does not implement interface RTCRtpSender.', 'TypeError');
      }
      const isLocal = sender._pc === this;
      if (!isLocal) {
        throw new DOMException('Sender was not created by this connection.',
          'InvalidAccessError');
      }

      // Search for the native stream the senders track belongs to.
      this._streams = this._streams || {};
      let stream;
      Object.keys(this._streams).forEach(streamid => {
        const hasTrack = this._streams[streamid].getTracks()
          .find(track => sender.track === track);
        if (hasTrack) {
          stream = this._streams[streamid];
        }
      });

      if (stream) {
        if (stream.getTracks().length === 1) {
          // if this is the last track of the stream, remove the stream. This
          // takes care of any shimmed _senders.
          this.removeStream(this._reverseStreams[stream.id]);
        } else {
          // relying on the same odd chrome behaviour as above.
          stream.removeTrack(sender.track);
        }
        this.dispatchEvent(new Event('negotiationneeded'));
      }
    };
}

function shimPeerConnection(window, browserDetails) {
  if (!window.RTCPeerConnection && window.webkitRTCPeerConnection) {
    // very basic support for old versions.
    window.RTCPeerConnection = window.webkitRTCPeerConnection;
  }
  if (!window.RTCPeerConnection) {
    return;
  }

  // shim implicit creation of RTCSessionDescription/RTCIceCandidate
  if (browserDetails.version < 53) {
    ['setLocalDescription', 'setRemoteDescription', 'addIceCandidate']
      .forEach(function(method) {
        const nativeMethod = window.RTCPeerConnection.prototype[method];
        const methodObj = {[method]() {
          arguments[0] = new ((method === 'addIceCandidate') ?
            window.RTCIceCandidate :
            window.RTCSessionDescription)(arguments[0]);
          return nativeMethod.apply(this, arguments);
        }};
        window.RTCPeerConnection.prototype[method] = methodObj[method];
      });
  }
}

// Attempt to fix ONN in plan-b mode.
function fixNegotiationNeeded(window, browserDetails) {
  _utils_js__WEBPACK_IMPORTED_MODULE_0__.wrapPeerConnectionEvent(window, 'negotiationneeded', e => {
    const pc = e.target;
    if (browserDetails.version < 72 || (pc.getConfiguration &&
        pc.getConfiguration().sdpSemantics === 'plan-b')) {
      if (pc.signalingState !== 'stable') {
        return;
      }
    }
    return e;
  });
}


/***/ }),

/***/ "./node_modules/webrtc-adapter/src/js/chrome/getdisplaymedia.js":
/*!**********************************************************************!*\
  !*** ./node_modules/webrtc-adapter/src/js/chrome/getdisplaymedia.js ***!
  \**********************************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   shimGetDisplayMedia: () => (/* binding */ shimGetDisplayMedia)
/* harmony export */ });
/*
 *  Copyright (c) 2018 The adapter.js project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree.
 */
/* eslint-env node */

function shimGetDisplayMedia(window, getSourceId) {
  if (window.navigator.mediaDevices &&
    'getDisplayMedia' in window.navigator.mediaDevices) {
    return;
  }
  if (!(window.navigator.mediaDevices)) {
    return;
  }
  // getSourceId is a function that returns a promise resolving with
  // the sourceId of the screen/window/tab to be shared.
  if (typeof getSourceId !== 'function') {
    console.error('shimGetDisplayMedia: getSourceId argument is not ' +
        'a function');
    return;
  }
  window.navigator.mediaDevices.getDisplayMedia =
    function getDisplayMedia(constraints) {
      return getSourceId(constraints)
        .then(sourceId => {
          const widthSpecified = constraints.video && constraints.video.width;
          const heightSpecified = constraints.video &&
            constraints.video.height;
          const frameRateSpecified = constraints.video &&
            constraints.video.frameRate;
          constraints.video = {
            mandatory: {
              chromeMediaSource: 'desktop',
              chromeMediaSourceId: sourceId,
              maxFrameRate: frameRateSpecified || 3
            }
          };
          if (widthSpecified) {
            constraints.video.mandatory.maxWidth = widthSpecified;
          }
          if (heightSpecified) {
            constraints.video.mandatory.maxHeight = heightSpecified;
          }
          return window.navigator.mediaDevices.getUserMedia(constraints);
        });
    };
}


/***/ }),

/***/ "./node_modules/webrtc-adapter/src/js/chrome/getusermedia.js":
/*!*******************************************************************!*\
  !*** ./node_modules/webrtc-adapter/src/js/chrome/getusermedia.js ***!
  \*******************************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   shimGetUserMedia: () => (/* binding */ shimGetUserMedia)
/* harmony export */ });
/* harmony import */ var _utils_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../utils.js */ "./node_modules/webrtc-adapter/src/js/utils.js");
/*
 *  Copyright (c) 2016 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree.
 */
/* eslint-env node */


const logging = _utils_js__WEBPACK_IMPORTED_MODULE_0__.log;

function shimGetUserMedia(window, browserDetails) {
  const navigator = window && window.navigator;

  if (!navigator.mediaDevices) {
    return;
  }

  const constraintsToChrome_ = function(c) {
    if (typeof c !== 'object' || c.mandatory || c.optional) {
      return c;
    }
    const cc = {};
    Object.keys(c).forEach(key => {
      if (key === 'require' || key === 'advanced' || key === 'mediaSource') {
        return;
      }
      const r = (typeof c[key] === 'object') ? c[key] : {ideal: c[key]};
      if (r.exact !== undefined && typeof r.exact === 'number') {
        r.min = r.max = r.exact;
      }
      const oldname_ = function(prefix, name) {
        if (prefix) {
          return prefix + name.charAt(0).toUpperCase() + name.slice(1);
        }
        return (name === 'deviceId') ? 'sourceId' : name;
      };
      if (r.ideal !== undefined) {
        cc.optional = cc.optional || [];
        let oc = {};
        if (typeof r.ideal === 'number') {
          oc[oldname_('min', key)] = r.ideal;
          cc.optional.push(oc);
          oc = {};
          oc[oldname_('max', key)] = r.ideal;
          cc.optional.push(oc);
        } else {
          oc[oldname_('', key)] = r.ideal;
          cc.optional.push(oc);
        }
      }
      if (r.exact !== undefined && typeof r.exact !== 'number') {
        cc.mandatory = cc.mandatory || {};
        cc.mandatory[oldname_('', key)] = r.exact;
      } else {
        ['min', 'max'].forEach(mix => {
          if (r[mix] !== undefined) {
            cc.mandatory = cc.mandatory || {};
            cc.mandatory[oldname_(mix, key)] = r[mix];
          }
        });
      }
    });
    if (c.advanced) {
      cc.optional = (cc.optional || []).concat(c.advanced);
    }
    return cc;
  };

  const shimConstraints_ = function(constraints, func) {
    if (browserDetails.version >= 61) {
      return func(constraints);
    }
    constraints = JSON.parse(JSON.stringify(constraints));
    if (constraints && typeof constraints.audio === 'object') {
      const remap = function(obj, a, b) {
        if (a in obj && !(b in obj)) {
          obj[b] = obj[a];
          delete obj[a];
        }
      };
      constraints = JSON.parse(JSON.stringify(constraints));
      remap(constraints.audio, 'autoGainControl', 'googAutoGainControl');
      remap(constraints.audio, 'noiseSuppression', 'googNoiseSuppression');
      constraints.audio = constraintsToChrome_(constraints.audio);
    }
    if (constraints && typeof constraints.video === 'object') {
      // Shim facingMode for mobile & surface pro.
      let face = constraints.video.facingMode;
      face = face && ((typeof face === 'object') ? face : {ideal: face});
      const getSupportedFacingModeLies = browserDetails.version < 66;

      if ((face && (face.exact === 'user' || face.exact === 'environment' ||
                    face.ideal === 'user' || face.ideal === 'environment')) &&
          !(navigator.mediaDevices.getSupportedConstraints &&
            navigator.mediaDevices.getSupportedConstraints().facingMode &&
            !getSupportedFacingModeLies)) {
        delete constraints.video.facingMode;
        let matches;
        if (face.exact === 'environment' || face.ideal === 'environment') {
          matches = ['back', 'rear'];
        } else if (face.exact === 'user' || face.ideal === 'user') {
          matches = ['front'];
        }
        if (matches) {
          // Look for matches in label, or use last cam for back (typical).
          return navigator.mediaDevices.enumerateDevices()
            .then(devices => {
              devices = devices.filter(d => d.kind === 'videoinput');
              let dev = devices.find(d => matches.some(match =>
                d.label.toLowerCase().includes(match)));
              if (!dev && devices.length && matches.includes('back')) {
                dev = devices[devices.length - 1]; // more likely the back cam
              }
              if (dev) {
                constraints.video.deviceId = face.exact
                  ? {exact: dev.deviceId}
                  : {ideal: dev.deviceId};
              }
              constraints.video = constraintsToChrome_(constraints.video);
              logging('chrome: ' + JSON.stringify(constraints));
              return func(constraints);
            });
        }
      }
      constraints.video = constraintsToChrome_(constraints.video);
    }
    logging('chrome: ' + JSON.stringify(constraints));
    return func(constraints);
  };

  const shimError_ = function(e) {
    if (browserDetails.version >= 64) {
      return e;
    }
    return {
      name: {
        PermissionDeniedError: 'NotAllowedError',
        PermissionDismissedError: 'NotAllowedError',
        InvalidStateError: 'NotAllowedError',
        DevicesNotFoundError: 'NotFoundError',
        ConstraintNotSatisfiedError: 'OverconstrainedError',
        TrackStartError: 'NotReadableError',
        MediaDeviceFailedDueToShutdown: 'NotAllowedError',
        MediaDeviceKillSwitchOn: 'NotAllowedError',
        TabCaptureError: 'AbortError',
        ScreenCaptureError: 'AbortError',
        DeviceCaptureError: 'AbortError'
      }[e.name] || e.name,
      message: e.message,
      constraint: e.constraint || e.constraintName,
      toString() {
        return this.name + (this.message && ': ') + this.message;
      }
    };
  };

  const getUserMedia_ = function(constraints, onSuccess, onError) {
    shimConstraints_(constraints, c => {
      navigator.webkitGetUserMedia(c, onSuccess, e => {
        if (onError) {
          onError(shimError_(e));
        }
      });
    });
  };
  navigator.getUserMedia = getUserMedia_.bind(navigator);

  // Even though Chrome 45 has navigator.mediaDevices and a getUserMedia
  // function which returns a Promise, it does not accept spec-style
  // constraints.
  if (navigator.mediaDevices.getUserMedia) {
    const origGetUserMedia = navigator.mediaDevices.getUserMedia.
      bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = function(cs) {
      return shimConstraints_(cs, c => origGetUserMedia(c).then(stream => {
        if (c.audio && !stream.getAudioTracks().length ||
            c.video && !stream.getVideoTracks().length) {
          stream.getTracks().forEach(track => {
            track.stop();
          });
          throw new DOMException('', 'NotFoundError');
        }
        return stream;
      }, e => Promise.reject(shimError_(e))));
    };
  }
}


/***/ }),

/***/ "./node_modules/webrtc-adapter/src/js/common_shim.js":
/*!***********************************************************!*\
  !*** ./node_modules/webrtc-adapter/src/js/common_shim.js ***!
  \***********************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   removeExtmapAllowMixed: () => (/* binding */ removeExtmapAllowMixed),
/* harmony export */   shimAddIceCandidateNullOrEmpty: () => (/* binding */ shimAddIceCandidateNullOrEmpty),
/* harmony export */   shimConnectionState: () => (/* binding */ shimConnectionState),
/* harmony export */   shimMaxMessageSize: () => (/* binding */ shimMaxMessageSize),
/* harmony export */   shimParameterlessSetLocalDescription: () => (/* binding */ shimParameterlessSetLocalDescription),
/* harmony export */   shimRTCIceCandidate: () => (/* binding */ shimRTCIceCandidate),
/* harmony export */   shimRTCIceCandidateRelayProtocol: () => (/* binding */ shimRTCIceCandidateRelayProtocol),
/* harmony export */   shimSendThrowTypeError: () => (/* binding */ shimSendThrowTypeError)
/* harmony export */ });
/* harmony import */ var sdp__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! sdp */ "./node_modules/sdp/sdp.js");
/* harmony import */ var sdp__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(sdp__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _utils__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./utils */ "./node_modules/webrtc-adapter/src/js/utils.js");
/*
 *  Copyright (c) 2017 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree.
 */
/* eslint-env node */





function shimRTCIceCandidate(window) {
  // foundation is arbitrarily chosen as an indicator for full support for
  // https://w3c.github.io/webrtc-pc/#rtcicecandidate-interface
  if (!window.RTCIceCandidate || (window.RTCIceCandidate && 'foundation' in
      window.RTCIceCandidate.prototype)) {
    return;
  }

  const NativeRTCIceCandidate = window.RTCIceCandidate;
  window.RTCIceCandidate = function RTCIceCandidate(args) {
    // Remove the a= which shouldn't be part of the candidate string.
    if (typeof args === 'object' && args.candidate &&
        args.candidate.indexOf('a=') === 0) {
      args = JSON.parse(JSON.stringify(args));
      args.candidate = args.candidate.substring(2);
    }

    if (args.candidate && args.candidate.length) {
      // Augment the native candidate with the parsed fields.
      const nativeCandidate = new NativeRTCIceCandidate(args);
      const parsedCandidate = sdp__WEBPACK_IMPORTED_MODULE_0___default().parseCandidate(args.candidate);
      const augmentedCandidate = Object.assign(nativeCandidate,
        parsedCandidate);

      // Add a serializer that does not serialize the extra attributes.
      augmentedCandidate.toJSON = function toJSON() {
        return {
          candidate: augmentedCandidate.candidate,
          sdpMid: augmentedCandidate.sdpMid,
          sdpMLineIndex: augmentedCandidate.sdpMLineIndex,
          usernameFragment: augmentedCandidate.usernameFragment,
        };
      };
      return augmentedCandidate;
    }
    return new NativeRTCIceCandidate(args);
  };
  window.RTCIceCandidate.prototype = NativeRTCIceCandidate.prototype;

  // Hook up the augmented candidate in onicecandidate and
  // addEventListener('icecandidate', ...)
  _utils__WEBPACK_IMPORTED_MODULE_1__.wrapPeerConnectionEvent(window, 'icecandidate', e => {
    if (e.candidate) {
      Object.defineProperty(e, 'candidate', {
        value: new window.RTCIceCandidate(e.candidate),
        writable: 'false'
      });
    }
    return e;
  });
}

function shimRTCIceCandidateRelayProtocol(window) {
  if (!window.RTCIceCandidate || (window.RTCIceCandidate && 'relayProtocol' in
      window.RTCIceCandidate.prototype)) {
    return;
  }

  // Hook up the augmented candidate in onicecandidate and
  // addEventListener('icecandidate', ...)
  _utils__WEBPACK_IMPORTED_MODULE_1__.wrapPeerConnectionEvent(window, 'icecandidate', e => {
    if (e.candidate) {
      const parsedCandidate = sdp__WEBPACK_IMPORTED_MODULE_0___default().parseCandidate(e.candidate.candidate);
      if (parsedCandidate.type === 'relay') {
        // This is a libwebrtc-specific mapping of local type preference
        // to relayProtocol.
        e.candidate.relayProtocol = {
          0: 'tls',
          1: 'tcp',
          2: 'udp',
        }[parsedCandidate.priority >> 24];
      }
    }
    return e;
  });
}

function shimMaxMessageSize(window, browserDetails) {
  if (!window.RTCPeerConnection) {
    return;
  }

  if (!('sctp' in window.RTCPeerConnection.prototype)) {
    Object.defineProperty(window.RTCPeerConnection.prototype, 'sctp', {
      get() {
        return typeof this._sctp === 'undefined' ? null : this._sctp;
      }
    });
  }

  const sctpInDescription = function(description) {
    if (!description || !description.sdp) {
      return false;
    }
    const sections = sdp__WEBPACK_IMPORTED_MODULE_0___default().splitSections(description.sdp);
    sections.shift();
    return sections.some(mediaSection => {
      const mLine = sdp__WEBPACK_IMPORTED_MODULE_0___default().parseMLine(mediaSection);
      return mLine && mLine.kind === 'application'
          && mLine.protocol.indexOf('SCTP') !== -1;
    });
  };

  const getRemoteFirefoxVersion = function(description) {
    // TODO: Is there a better solution for detecting Firefox?
    const match = description.sdp.match(/mozilla...THIS_IS_SDPARTA-(\d+)/);
    if (match === null || match.length < 2) {
      return -1;
    }
    const version = parseInt(match[1], 10);
    // Test for NaN (yes, this is ugly)
    return version !== version ? -1 : version;
  };

  const getCanSendMaxMessageSize = function(remoteIsFirefox) {
    // Every implementation we know can send at least 64 KiB.
    // Note: Although Chrome is technically able to send up to 256 KiB, the
    //       data does not reach the other peer reliably.
    //       See: https://bugs.chromium.org/p/webrtc/issues/detail?id=8419
    let canSendMaxMessageSize = 65536;
    if (browserDetails.browser === 'firefox') {
      if (browserDetails.version < 57) {
        if (remoteIsFirefox === -1) {
          // FF < 57 will send in 16 KiB chunks using the deprecated PPID
          // fragmentation.
          canSendMaxMessageSize = 16384;
        } else {
          // However, other FF (and RAWRTC) can reassemble PPID-fragmented
          // messages. Thus, supporting ~2 GiB when sending.
          canSendMaxMessageSize = 2147483637;
        }
      } else if (browserDetails.version < 60) {
        // Currently, all FF >= 57 will reset the remote maximum message size
        // to the default value when a data channel is created at a later
        // stage. :(
        // See: https://bugzilla.mozilla.org/show_bug.cgi?id=1426831
        canSendMaxMessageSize =
          browserDetails.version === 57 ? 65535 : 65536;
      } else {
        // FF >= 60 supports sending ~2 GiB
        canSendMaxMessageSize = 2147483637;
      }
    }
    return canSendMaxMessageSize;
  };

  const getMaxMessageSize = function(description, remoteIsFirefox) {
    // Note: 65536 bytes is the default value from the SDP spec. Also,
    //       every implementation we know supports receiving 65536 bytes.
    let maxMessageSize = 65536;

    // FF 57 has a slightly incorrect default remote max message size, so
    // we need to adjust it here to avoid a failure when sending.
    // See: https://bugzilla.mozilla.org/show_bug.cgi?id=1425697
    if (browserDetails.browser === 'firefox'
         && browserDetails.version === 57) {
      maxMessageSize = 65535;
    }

    const match = sdp__WEBPACK_IMPORTED_MODULE_0___default().matchPrefix(description.sdp,
      'a=max-message-size:');
    if (match.length > 0) {
      maxMessageSize = parseInt(match[0].substring(19), 10);
    } else if (browserDetails.browser === 'firefox' &&
                remoteIsFirefox !== -1) {
      // If the maximum message size is not present in the remote SDP and
      // both local and remote are Firefox, the remote peer can receive
      // ~2 GiB.
      maxMessageSize = 2147483637;
    }
    return maxMessageSize;
  };

  const origSetRemoteDescription =
      window.RTCPeerConnection.prototype.setRemoteDescription;
  window.RTCPeerConnection.prototype.setRemoteDescription =
    function setRemoteDescription() {
      this._sctp = null;
      // Chrome decided to not expose .sctp in plan-b mode.
      // As usual, adapter.js has to do an 'ugly worakaround'
      // to cover up the mess.
      if (browserDetails.browser === 'chrome' && browserDetails.version >= 76) {
        const {sdpSemantics} = this.getConfiguration();
        if (sdpSemantics === 'plan-b') {
          Object.defineProperty(this, 'sctp', {
            get() {
              return typeof this._sctp === 'undefined' ? null : this._sctp;
            },
            enumerable: true,
            configurable: true,
          });
        }
      }

      if (sctpInDescription(arguments[0])) {
        // Check if the remote is FF.
        const isFirefox = getRemoteFirefoxVersion(arguments[0]);

        // Get the maximum message size the local peer is capable of sending
        const canSendMMS = getCanSendMaxMessageSize(isFirefox);

        // Get the maximum message size of the remote peer.
        const remoteMMS = getMaxMessageSize(arguments[0], isFirefox);

        // Determine final maximum message size
        let maxMessageSize;
        if (canSendMMS === 0 && remoteMMS === 0) {
          maxMessageSize = Number.POSITIVE_INFINITY;
        } else if (canSendMMS === 0 || remoteMMS === 0) {
          maxMessageSize = Math.max(canSendMMS, remoteMMS);
        } else {
          maxMessageSize = Math.min(canSendMMS, remoteMMS);
        }

        // Create a dummy RTCSctpTransport object and the 'maxMessageSize'
        // attribute.
        const sctp = {};
        Object.defineProperty(sctp, 'maxMessageSize', {
          get() {
            return maxMessageSize;
          }
        });
        this._sctp = sctp;
      }

      return origSetRemoteDescription.apply(this, arguments);
    };
}

function shimSendThrowTypeError(window) {
  if (!(window.RTCPeerConnection &&
      'createDataChannel' in window.RTCPeerConnection.prototype)) {
    return;
  }

  // Note: Although Firefox >= 57 has a native implementation, the maximum
  //       message size can be reset for all data channels at a later stage.
  //       See: https://bugzilla.mozilla.org/show_bug.cgi?id=1426831

  function wrapDcSend(dc, pc) {
    const origDataChannelSend = dc.send;
    dc.send = function send() {
      const data = arguments[0];
      const length = data.length || data.size || data.byteLength;
      if (dc.readyState === 'open' &&
          pc.sctp && length > pc.sctp.maxMessageSize) {
        throw new TypeError('Message too large (can send a maximum of ' +
          pc.sctp.maxMessageSize + ' bytes)');
      }
      return origDataChannelSend.apply(dc, arguments);
    };
  }
  const origCreateDataChannel =
    window.RTCPeerConnection.prototype.createDataChannel;
  window.RTCPeerConnection.prototype.createDataChannel =
    function createDataChannel() {
      const dataChannel = origCreateDataChannel.apply(this, arguments);
      wrapDcSend(dataChannel, this);
      return dataChannel;
    };
  _utils__WEBPACK_IMPORTED_MODULE_1__.wrapPeerConnectionEvent(window, 'datachannel', e => {
    wrapDcSend(e.channel, e.target);
    return e;
  });
}


/* shims RTCConnectionState by pretending it is the same as iceConnectionState.
 * See https://bugs.chromium.org/p/webrtc/issues/detail?id=6145#c12
 * for why this is a valid hack in Chrome. In Firefox it is slightly incorrect
 * since DTLS failures would be hidden. See
 * https://bugzilla.mozilla.org/show_bug.cgi?id=1265827
 * for the Firefox tracking bug.
 */
function shimConnectionState(window) {
  if (!window.RTCPeerConnection ||
      'connectionState' in window.RTCPeerConnection.prototype) {
    return;
  }
  const proto = window.RTCPeerConnection.prototype;
  Object.defineProperty(proto, 'connectionState', {
    get() {
      return {
        completed: 'connected',
        checking: 'connecting'
      }[this.iceConnectionState] || this.iceConnectionState;
    },
    enumerable: true,
    configurable: true
  });
  Object.defineProperty(proto, 'onconnectionstatechange', {
    get() {
      return this._onconnectionstatechange || null;
    },
    set(cb) {
      if (this._onconnectionstatechange) {
        this.removeEventListener('connectionstatechange',
          this._onconnectionstatechange);
        delete this._onconnectionstatechange;
      }
      if (cb) {
        this.addEventListener('connectionstatechange',
          this._onconnectionstatechange = cb);
      }
    },
    enumerable: true,
    configurable: true
  });

  ['setLocalDescription', 'setRemoteDescription'].forEach((method) => {
    const origMethod = proto[method];
    proto[method] = function() {
      if (!this._connectionstatechangepoly) {
        this._connectionstatechangepoly = e => {
          const pc = e.target;
          if (pc._lastConnectionState !== pc.connectionState) {
            pc._lastConnectionState = pc.connectionState;
            const newEvent = new Event('connectionstatechange', e);
            pc.dispatchEvent(newEvent);
          }
          return e;
        };
        this.addEventListener('iceconnectionstatechange',
          this._connectionstatechangepoly);
      }
      return origMethod.apply(this, arguments);
    };
  });
}

function removeExtmapAllowMixed(window, browserDetails) {
  /* remove a=extmap-allow-mixed for webrtc.org < M71 */
  if (!window.RTCPeerConnection) {
    return;
  }
  if (browserDetails.browser === 'chrome' && browserDetails.version >= 71) {
    return;
  }
  if (browserDetails.browser === 'safari' && browserDetails.version >= 605) {
    return;
  }
  const nativeSRD = window.RTCPeerConnection.prototype.setRemoteDescription;
  window.RTCPeerConnection.prototype.setRemoteDescription =
  function setRemoteDescription(desc) {
    if (desc && desc.sdp && desc.sdp.indexOf('\na=extmap-allow-mixed') !== -1) {
      const sdp = desc.sdp.split('\n').filter((line) => {
        return line.trim() !== 'a=extmap-allow-mixed';
      }).join('\n');
      // Safari enforces read-only-ness of RTCSessionDescription fields.
      if (window.RTCSessionDescription &&
          desc instanceof window.RTCSessionDescription) {
        arguments[0] = new window.RTCSessionDescription({
          type: desc.type,
          sdp,
        });
      } else {
        desc.sdp = sdp;
      }
    }
    return nativeSRD.apply(this, arguments);
  };
}

function shimAddIceCandidateNullOrEmpty(window, browserDetails) {
  // Support for addIceCandidate(null or undefined)
  // as well as addIceCandidate({candidate: "", ...})
  // https://bugs.chromium.org/p/chromium/issues/detail?id=978582
  // Note: must be called before other polyfills which change the signature.
  if (!(window.RTCPeerConnection && window.RTCPeerConnection.prototype)) {
    return;
  }
  const nativeAddIceCandidate =
      window.RTCPeerConnection.prototype.addIceCandidate;
  if (!nativeAddIceCandidate || nativeAddIceCandidate.length === 0) {
    return;
  }
  window.RTCPeerConnection.prototype.addIceCandidate =
    function addIceCandidate() {
      if (!arguments[0]) {
        if (arguments[1]) {
          arguments[1].apply(null);
        }
        return Promise.resolve();
      }
      // Firefox 68+ emits and processes {candidate: "", ...}, ignore
      // in older versions.
      // Native support for ignoring exists for Chrome M77+.
      // Safari ignores as well, exact version unknown but works in the same
      // version that also ignores addIceCandidate(null).
      if (((browserDetails.browser === 'chrome' && browserDetails.version < 78)
           || (browserDetails.browser === 'firefox'
               && browserDetails.version < 68)
           || (browserDetails.browser === 'safari'))
          && arguments[0] && arguments[0].candidate === '') {
        return Promise.resolve();
      }
      return nativeAddIceCandidate.apply(this, arguments);
    };
}

// Note: Make sure to call this ahead of APIs that modify
// setLocalDescription.length
function shimParameterlessSetLocalDescription(window, browserDetails) {
  if (!(window.RTCPeerConnection && window.RTCPeerConnection.prototype)) {
    return;
  }
  const nativeSetLocalDescription =
      window.RTCPeerConnection.prototype.setLocalDescription;
  if (!nativeSetLocalDescription || nativeSetLocalDescription.length === 0) {
    return;
  }
  window.RTCPeerConnection.prototype.setLocalDescription =
    function setLocalDescription() {
      let desc = arguments[0] || {};
      if (typeof desc !== 'object' || (desc.type && desc.sdp)) {
        return nativeSetLocalDescription.apply(this, arguments);
      }
      // The remaining steps should technically happen when SLD comes off the
      // RTCPeerConnection's operations chain (not ahead of going on it), but
      // this is too difficult to shim. Instead, this shim only covers the
      // common case where the operations chain is empty. This is imperfect, but
      // should cover many cases. Rationale: Even if we can't reduce the glare
      // window to zero on imperfect implementations, there's value in tapping
      // into the perfect negotiation pattern that several browsers support.
      desc = {type: desc.type, sdp: desc.sdp};
      if (!desc.type) {
        switch (this.signalingState) {
          case 'stable':
          case 'have-local-offer':
          case 'have-remote-pranswer':
            desc.type = 'offer';
            break;
          default:
            desc.type = 'answer';
            break;
        }
      }
      if (desc.sdp || (desc.type !== 'offer' && desc.type !== 'answer')) {
        return nativeSetLocalDescription.apply(this, [desc]);
      }
      const func = desc.type === 'offer' ? this.createOffer : this.createAnswer;
      return func.apply(this)
        .then(d => nativeSetLocalDescription.apply(this, [d]));
    };
}


/***/ }),

/***/ "./node_modules/webrtc-adapter/src/js/firefox/firefox_shim.js":
/*!********************************************************************!*\
  !*** ./node_modules/webrtc-adapter/src/js/firefox/firefox_shim.js ***!
  \********************************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   shimAddTransceiver: () => (/* binding */ shimAddTransceiver),
/* harmony export */   shimCreateAnswer: () => (/* binding */ shimCreateAnswer),
/* harmony export */   shimCreateOffer: () => (/* binding */ shimCreateOffer),
/* harmony export */   shimGetDisplayMedia: () => (/* reexport safe */ _getdisplaymedia__WEBPACK_IMPORTED_MODULE_2__.shimGetDisplayMedia),
/* harmony export */   shimGetParameters: () => (/* binding */ shimGetParameters),
/* harmony export */   shimGetUserMedia: () => (/* reexport safe */ _getusermedia__WEBPACK_IMPORTED_MODULE_1__.shimGetUserMedia),
/* harmony export */   shimOnTrack: () => (/* binding */ shimOnTrack),
/* harmony export */   shimPeerConnection: () => (/* binding */ shimPeerConnection),
/* harmony export */   shimRTCDataChannel: () => (/* binding */ shimRTCDataChannel),
/* harmony export */   shimReceiverGetStats: () => (/* binding */ shimReceiverGetStats),
/* harmony export */   shimRemoveStream: () => (/* binding */ shimRemoveStream),
/* harmony export */   shimSenderGetStats: () => (/* binding */ shimSenderGetStats)
/* harmony export */ });
/* harmony import */ var _utils__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../utils */ "./node_modules/webrtc-adapter/src/js/utils.js");
/* harmony import */ var _getusermedia__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./getusermedia */ "./node_modules/webrtc-adapter/src/js/firefox/getusermedia.js");
/* harmony import */ var _getdisplaymedia__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./getdisplaymedia */ "./node_modules/webrtc-adapter/src/js/firefox/getdisplaymedia.js");
/*
 *  Copyright (c) 2016 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree.
 */
/* eslint-env node */






function shimOnTrack(window) {
  if (typeof window === 'object' && window.RTCTrackEvent &&
      ('receiver' in window.RTCTrackEvent.prototype) &&
      !('transceiver' in window.RTCTrackEvent.prototype)) {
    Object.defineProperty(window.RTCTrackEvent.prototype, 'transceiver', {
      get() {
        return {receiver: this.receiver};
      }
    });
  }
}

function shimPeerConnection(window, browserDetails) {
  if (typeof window !== 'object' ||
      !(window.RTCPeerConnection || window.mozRTCPeerConnection)) {
    return; // probably media.peerconnection.enabled=false in about:config
  }
  if (!window.RTCPeerConnection && window.mozRTCPeerConnection) {
    // very basic support for old versions.
    window.RTCPeerConnection = window.mozRTCPeerConnection;
  }

  if (browserDetails.version < 53) {
    // shim away need for obsolete RTCIceCandidate/RTCSessionDescription.
    ['setLocalDescription', 'setRemoteDescription', 'addIceCandidate']
      .forEach(function(method) {
        const nativeMethod = window.RTCPeerConnection.prototype[method];
        const methodObj = {[method]() {
          arguments[0] = new ((method === 'addIceCandidate') ?
            window.RTCIceCandidate :
            window.RTCSessionDescription)(arguments[0]);
          return nativeMethod.apply(this, arguments);
        }};
        window.RTCPeerConnection.prototype[method] = methodObj[method];
      });
  }

  const modernStatsTypes = {
    inboundrtp: 'inbound-rtp',
    outboundrtp: 'outbound-rtp',
    candidatepair: 'candidate-pair',
    localcandidate: 'local-candidate',
    remotecandidate: 'remote-candidate'
  };

  const nativeGetStats = window.RTCPeerConnection.prototype.getStats;
  window.RTCPeerConnection.prototype.getStats = function getStats() {
    const [selector, onSucc, onErr] = arguments;
    return nativeGetStats.apply(this, [selector || null])
      .then(stats => {
        if (browserDetails.version < 53 && !onSucc) {
          // Shim only promise getStats with spec-hyphens in type names
          // Leave callback version alone; misc old uses of forEach before Map
          try {
            stats.forEach(stat => {
              stat.type = modernStatsTypes[stat.type] || stat.type;
            });
          } catch (e) {
            if (e.name !== 'TypeError') {
              throw e;
            }
            // Avoid TypeError: "type" is read-only, in old versions. 34-43ish
            stats.forEach((stat, i) => {
              stats.set(i, Object.assign({}, stat, {
                type: modernStatsTypes[stat.type] || stat.type
              }));
            });
          }
        }
        return stats;
      })
      .then(onSucc, onErr);
  };
}

function shimSenderGetStats(window) {
  if (!(typeof window === 'object' && window.RTCPeerConnection &&
      window.RTCRtpSender)) {
    return;
  }
  if (window.RTCRtpSender && 'getStats' in window.RTCRtpSender.prototype) {
    return;
  }
  const origGetSenders = window.RTCPeerConnection.prototype.getSenders;
  if (origGetSenders) {
    window.RTCPeerConnection.prototype.getSenders = function getSenders() {
      const senders = origGetSenders.apply(this, []);
      senders.forEach(sender => sender._pc = this);
      return senders;
    };
  }

  const origAddTrack = window.RTCPeerConnection.prototype.addTrack;
  if (origAddTrack) {
    window.RTCPeerConnection.prototype.addTrack = function addTrack() {
      const sender = origAddTrack.apply(this, arguments);
      sender._pc = this;
      return sender;
    };
  }
  window.RTCRtpSender.prototype.getStats = function getStats() {
    return this.track ? this._pc.getStats(this.track) :
      Promise.resolve(new Map());
  };
}

function shimReceiverGetStats(window) {
  if (!(typeof window === 'object' && window.RTCPeerConnection &&
      window.RTCRtpSender)) {
    return;
  }
  if (window.RTCRtpSender && 'getStats' in window.RTCRtpReceiver.prototype) {
    return;
  }
  const origGetReceivers = window.RTCPeerConnection.prototype.getReceivers;
  if (origGetReceivers) {
    window.RTCPeerConnection.prototype.getReceivers = function getReceivers() {
      const receivers = origGetReceivers.apply(this, []);
      receivers.forEach(receiver => receiver._pc = this);
      return receivers;
    };
  }
  _utils__WEBPACK_IMPORTED_MODULE_0__.wrapPeerConnectionEvent(window, 'track', e => {
    e.receiver._pc = e.srcElement;
    return e;
  });
  window.RTCRtpReceiver.prototype.getStats = function getStats() {
    return this._pc.getStats(this.track);
  };
}

function shimRemoveStream(window) {
  if (!window.RTCPeerConnection ||
      'removeStream' in window.RTCPeerConnection.prototype) {
    return;
  }
  window.RTCPeerConnection.prototype.removeStream =
    function removeStream(stream) {
      _utils__WEBPACK_IMPORTED_MODULE_0__.deprecated('removeStream', 'removeTrack');
      this.getSenders().forEach(sender => {
        if (sender.track && stream.getTracks().includes(sender.track)) {
          this.removeTrack(sender);
        }
      });
    };
}

function shimRTCDataChannel(window) {
  // rename DataChannel to RTCDataChannel (native fix in FF60):
  // https://bugzilla.mozilla.org/show_bug.cgi?id=1173851
  if (window.DataChannel && !window.RTCDataChannel) {
    window.RTCDataChannel = window.DataChannel;
  }
}

function shimAddTransceiver(window) {
  // https://github.com/webrtcHacks/adapter/issues/998#issuecomment-516921647
  // Firefox ignores the init sendEncodings options passed to addTransceiver
  // https://bugzilla.mozilla.org/show_bug.cgi?id=1396918
  if (!(typeof window === 'object' && window.RTCPeerConnection)) {
    return;
  }
  const origAddTransceiver = window.RTCPeerConnection.prototype.addTransceiver;
  if (origAddTransceiver) {
    window.RTCPeerConnection.prototype.addTransceiver =
      function addTransceiver() {
        this.setParametersPromises = [];
        // WebIDL input coercion and validation
        let sendEncodings = arguments[1] && arguments[1].sendEncodings;
        if (sendEncodings === undefined) {
          sendEncodings = [];
        }
        sendEncodings = [...sendEncodings];
        const shouldPerformCheck = sendEncodings.length > 0;
        if (shouldPerformCheck) {
          // If sendEncodings params are provided, validate grammar
          sendEncodings.forEach((encodingParam) => {
            if ('rid' in encodingParam) {
              const ridRegex = /^[a-z0-9]{0,16}$/i;
              if (!ridRegex.test(encodingParam.rid)) {
                throw new TypeError('Invalid RID value provided.');
              }
            }
            if ('scaleResolutionDownBy' in encodingParam) {
              if (!(parseFloat(encodingParam.scaleResolutionDownBy) >= 1.0)) {
                throw new RangeError('scale_resolution_down_by must be >= 1.0');
              }
            }
            if ('maxFramerate' in encodingParam) {
              if (!(parseFloat(encodingParam.maxFramerate) >= 0)) {
                throw new RangeError('max_framerate must be >= 0.0');
              }
            }
          });
        }
        const transceiver = origAddTransceiver.apply(this, arguments);
        if (shouldPerformCheck) {
          // Check if the init options were applied. If not we do this in an
          // asynchronous way and save the promise reference in a global object.
          // This is an ugly hack, but at the same time is way more robust than
          // checking the sender parameters before and after the createOffer
          // Also note that after the createoffer we are not 100% sure that
          // the params were asynchronously applied so we might miss the
          // opportunity to recreate offer.
          const {sender} = transceiver;
          const params = sender.getParameters();
          if (!('encodings' in params) ||
              // Avoid being fooled by patched getParameters() below.
              (params.encodings.length === 1 &&
               Object.keys(params.encodings[0]).length === 0)) {
            params.encodings = sendEncodings;
            sender.sendEncodings = sendEncodings;
            this.setParametersPromises.push(sender.setParameters(params)
              .then(() => {
                delete sender.sendEncodings;
              }).catch(() => {
                delete sender.sendEncodings;
              })
            );
          }
        }
        return transceiver;
      };
  }
}

function shimGetParameters(window) {
  if (!(typeof window === 'object' && window.RTCRtpSender)) {
    return;
  }
  const origGetParameters = window.RTCRtpSender.prototype.getParameters;
  if (origGetParameters) {
    window.RTCRtpSender.prototype.getParameters =
      function getParameters() {
        const params = origGetParameters.apply(this, arguments);
        if (!('encodings' in params)) {
          params.encodings = [].concat(this.sendEncodings || [{}]);
        }
        return params;
      };
  }
}

function shimCreateOffer(window) {
  // https://github.com/webrtcHacks/adapter/issues/998#issuecomment-516921647
  // Firefox ignores the init sendEncodings options passed to addTransceiver
  // https://bugzilla.mozilla.org/show_bug.cgi?id=1396918
  if (!(typeof window === 'object' && window.RTCPeerConnection)) {
    return;
  }
  const origCreateOffer = window.RTCPeerConnection.prototype.createOffer;
  window.RTCPeerConnection.prototype.createOffer = function createOffer() {
    if (this.setParametersPromises && this.setParametersPromises.length) {
      return Promise.all(this.setParametersPromises)
        .then(() => {
          return origCreateOffer.apply(this, arguments);
        })
        .finally(() => {
          this.setParametersPromises = [];
        });
    }
    return origCreateOffer.apply(this, arguments);
  };
}

function shimCreateAnswer(window) {
  // https://github.com/webrtcHacks/adapter/issues/998#issuecomment-516921647
  // Firefox ignores the init sendEncodings options passed to addTransceiver
  // https://bugzilla.mozilla.org/show_bug.cgi?id=1396918
  if (!(typeof window === 'object' && window.RTCPeerConnection)) {
    return;
  }
  const origCreateAnswer = window.RTCPeerConnection.prototype.createAnswer;
  window.RTCPeerConnection.prototype.createAnswer = function createAnswer() {
    if (this.setParametersPromises && this.setParametersPromises.length) {
      return Promise.all(this.setParametersPromises)
        .then(() => {
          return origCreateAnswer.apply(this, arguments);
        })
        .finally(() => {
          this.setParametersPromises = [];
        });
    }
    return origCreateAnswer.apply(this, arguments);
  };
}


/***/ }),

/***/ "./node_modules/webrtc-adapter/src/js/firefox/getdisplaymedia.js":
/*!***********************************************************************!*\
  !*** ./node_modules/webrtc-adapter/src/js/firefox/getdisplaymedia.js ***!
  \***********************************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   shimGetDisplayMedia: () => (/* binding */ shimGetDisplayMedia)
/* harmony export */ });
/*
 *  Copyright (c) 2018 The adapter.js project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree.
 */
/* eslint-env node */


function shimGetDisplayMedia(window, preferredMediaSource) {
  if (window.navigator.mediaDevices &&
    'getDisplayMedia' in window.navigator.mediaDevices) {
    return;
  }
  if (!(window.navigator.mediaDevices)) {
    return;
  }
  window.navigator.mediaDevices.getDisplayMedia =
    function getDisplayMedia(constraints) {
      if (!(constraints && constraints.video)) {
        const err = new DOMException('getDisplayMedia without video ' +
            'constraints is undefined');
        err.name = 'NotFoundError';
        // from https://heycam.github.io/webidl/#idl-DOMException-error-names
        err.code = 8;
        return Promise.reject(err);
      }
      if (constraints.video === true) {
        constraints.video = {mediaSource: preferredMediaSource};
      } else {
        constraints.video.mediaSource = preferredMediaSource;
      }
      return window.navigator.mediaDevices.getUserMedia(constraints);
    };
}


/***/ }),

/***/ "./node_modules/webrtc-adapter/src/js/firefox/getusermedia.js":
/*!********************************************************************!*\
  !*** ./node_modules/webrtc-adapter/src/js/firefox/getusermedia.js ***!
  \********************************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   shimGetUserMedia: () => (/* binding */ shimGetUserMedia)
/* harmony export */ });
/* harmony import */ var _utils__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../utils */ "./node_modules/webrtc-adapter/src/js/utils.js");
/*
 *  Copyright (c) 2016 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree.
 */
/* eslint-env node */




function shimGetUserMedia(window, browserDetails) {
  const navigator = window && window.navigator;
  const MediaStreamTrack = window && window.MediaStreamTrack;

  navigator.getUserMedia = function(constraints, onSuccess, onError) {
    // Replace Firefox 44+'s deprecation warning with unprefixed version.
    _utils__WEBPACK_IMPORTED_MODULE_0__.deprecated('navigator.getUserMedia',
      'navigator.mediaDevices.getUserMedia');
    navigator.mediaDevices.getUserMedia(constraints).then(onSuccess, onError);
  };

  if (!(browserDetails.version > 55 &&
      'autoGainControl' in navigator.mediaDevices.getSupportedConstraints())) {
    const remap = function(obj, a, b) {
      if (a in obj && !(b in obj)) {
        obj[b] = obj[a];
        delete obj[a];
      }
    };

    const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.
      bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = function(c) {
      if (typeof c === 'object' && typeof c.audio === 'object') {
        c = JSON.parse(JSON.stringify(c));
        remap(c.audio, 'autoGainControl', 'mozAutoGainControl');
        remap(c.audio, 'noiseSuppression', 'mozNoiseSuppression');
      }
      return nativeGetUserMedia(c);
    };

    if (MediaStreamTrack && MediaStreamTrack.prototype.getSettings) {
      const nativeGetSettings = MediaStreamTrack.prototype.getSettings;
      MediaStreamTrack.prototype.getSettings = function() {
        const obj = nativeGetSettings.apply(this, arguments);
        remap(obj, 'mozAutoGainControl', 'autoGainControl');
        remap(obj, 'mozNoiseSuppression', 'noiseSuppression');
        return obj;
      };
    }

    if (MediaStreamTrack && MediaStreamTrack.prototype.applyConstraints) {
      const nativeApplyConstraints =
        MediaStreamTrack.prototype.applyConstraints;
      MediaStreamTrack.prototype.applyConstraints = function(c) {
        if (this.kind === 'audio' && typeof c === 'object') {
          c = JSON.parse(JSON.stringify(c));
          remap(c, 'autoGainControl', 'mozAutoGainControl');
          remap(c, 'noiseSuppression', 'mozNoiseSuppression');
        }
        return nativeApplyConstraints.apply(this, [c]);
      };
    }
  }
}


/***/ }),

/***/ "./node_modules/webrtc-adapter/src/js/safari/safari_shim.js":
/*!******************************************************************!*\
  !*** ./node_modules/webrtc-adapter/src/js/safari/safari_shim.js ***!
  \******************************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   shimAudioContext: () => (/* binding */ shimAudioContext),
/* harmony export */   shimCallbacksAPI: () => (/* binding */ shimCallbacksAPI),
/* harmony export */   shimConstraints: () => (/* binding */ shimConstraints),
/* harmony export */   shimCreateOfferLegacy: () => (/* binding */ shimCreateOfferLegacy),
/* harmony export */   shimGetUserMedia: () => (/* binding */ shimGetUserMedia),
/* harmony export */   shimLocalStreamsAPI: () => (/* binding */ shimLocalStreamsAPI),
/* harmony export */   shimRTCIceServerUrls: () => (/* binding */ shimRTCIceServerUrls),
/* harmony export */   shimRemoteStreamsAPI: () => (/* binding */ shimRemoteStreamsAPI),
/* harmony export */   shimTrackEventTransceiver: () => (/* binding */ shimTrackEventTransceiver)
/* harmony export */ });
/* harmony import */ var _utils__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../utils */ "./node_modules/webrtc-adapter/src/js/utils.js");
/*
 *  Copyright (c) 2016 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree.
 */



function shimLocalStreamsAPI(window) {
  if (typeof window !== 'object' || !window.RTCPeerConnection) {
    return;
  }
  if (!('getLocalStreams' in window.RTCPeerConnection.prototype)) {
    window.RTCPeerConnection.prototype.getLocalStreams =
      function getLocalStreams() {
        if (!this._localStreams) {
          this._localStreams = [];
        }
        return this._localStreams;
      };
  }
  if (!('addStream' in window.RTCPeerConnection.prototype)) {
    const _addTrack = window.RTCPeerConnection.prototype.addTrack;
    window.RTCPeerConnection.prototype.addStream = function addStream(stream) {
      if (!this._localStreams) {
        this._localStreams = [];
      }
      if (!this._localStreams.includes(stream)) {
        this._localStreams.push(stream);
      }
      // Try to emulate Chrome's behaviour of adding in audio-video order.
      // Safari orders by track id.
      stream.getAudioTracks().forEach(track => _addTrack.call(this, track,
        stream));
      stream.getVideoTracks().forEach(track => _addTrack.call(this, track,
        stream));
    };

    window.RTCPeerConnection.prototype.addTrack =
      function addTrack(track, ...streams) {
        if (streams) {
          streams.forEach((stream) => {
            if (!this._localStreams) {
              this._localStreams = [stream];
            } else if (!this._localStreams.includes(stream)) {
              this._localStreams.push(stream);
            }
          });
        }
        return _addTrack.apply(this, arguments);
      };
  }
  if (!('removeStream' in window.RTCPeerConnection.prototype)) {
    window.RTCPeerConnection.prototype.removeStream =
      function removeStream(stream) {
        if (!this._localStreams) {
          this._localStreams = [];
        }
        const index = this._localStreams.indexOf(stream);
        if (index === -1) {
          return;
        }
        this._localStreams.splice(index, 1);
        const tracks = stream.getTracks();
        this.getSenders().forEach(sender => {
          if (tracks.includes(sender.track)) {
            this.removeTrack(sender);
          }
        });
      };
  }
}

function shimRemoteStreamsAPI(window) {
  if (typeof window !== 'object' || !window.RTCPeerConnection) {
    return;
  }
  if (!('getRemoteStreams' in window.RTCPeerConnection.prototype)) {
    window.RTCPeerConnection.prototype.getRemoteStreams =
      function getRemoteStreams() {
        return this._remoteStreams ? this._remoteStreams : [];
      };
  }
  if (!('onaddstream' in window.RTCPeerConnection.prototype)) {
    Object.defineProperty(window.RTCPeerConnection.prototype, 'onaddstream', {
      get() {
        return this._onaddstream;
      },
      set(f) {
        if (this._onaddstream) {
          this.removeEventListener('addstream', this._onaddstream);
          this.removeEventListener('track', this._onaddstreampoly);
        }
        this.addEventListener('addstream', this._onaddstream = f);
        this.addEventListener('track', this._onaddstreampoly = (e) => {
          e.streams.forEach(stream => {
            if (!this._remoteStreams) {
              this._remoteStreams = [];
            }
            if (this._remoteStreams.includes(stream)) {
              return;
            }
            this._remoteStreams.push(stream);
            const event = new Event('addstream');
            event.stream = stream;
            this.dispatchEvent(event);
          });
        });
      }
    });
    const origSetRemoteDescription =
      window.RTCPeerConnection.prototype.setRemoteDescription;
    window.RTCPeerConnection.prototype.setRemoteDescription =
      function setRemoteDescription() {
        const pc = this;
        if (!this._onaddstreampoly) {
          this.addEventListener('track', this._onaddstreampoly = function(e) {
            e.streams.forEach(stream => {
              if (!pc._remoteStreams) {
                pc._remoteStreams = [];
              }
              if (pc._remoteStreams.indexOf(stream) >= 0) {
                return;
              }
              pc._remoteStreams.push(stream);
              const event = new Event('addstream');
              event.stream = stream;
              pc.dispatchEvent(event);
            });
          });
        }
        return origSetRemoteDescription.apply(pc, arguments);
      };
  }
}

function shimCallbacksAPI(window) {
  if (typeof window !== 'object' || !window.RTCPeerConnection) {
    return;
  }
  const prototype = window.RTCPeerConnection.prototype;
  const origCreateOffer = prototype.createOffer;
  const origCreateAnswer = prototype.createAnswer;
  const setLocalDescription = prototype.setLocalDescription;
  const setRemoteDescription = prototype.setRemoteDescription;
  const addIceCandidate = prototype.addIceCandidate;

  prototype.createOffer =
    function createOffer(successCallback, failureCallback) {
      const options = (arguments.length >= 2) ? arguments[2] : arguments[0];
      const promise = origCreateOffer.apply(this, [options]);
      if (!failureCallback) {
        return promise;
      }
      promise.then(successCallback, failureCallback);
      return Promise.resolve();
    };

  prototype.createAnswer =
    function createAnswer(successCallback, failureCallback) {
      const options = (arguments.length >= 2) ? arguments[2] : arguments[0];
      const promise = origCreateAnswer.apply(this, [options]);
      if (!failureCallback) {
        return promise;
      }
      promise.then(successCallback, failureCallback);
      return Promise.resolve();
    };

  let withCallback = function(description, successCallback, failureCallback) {
    const promise = setLocalDescription.apply(this, [description]);
    if (!failureCallback) {
      return promise;
    }
    promise.then(successCallback, failureCallback);
    return Promise.resolve();
  };
  prototype.setLocalDescription = withCallback;

  withCallback = function(description, successCallback, failureCallback) {
    const promise = setRemoteDescription.apply(this, [description]);
    if (!failureCallback) {
      return promise;
    }
    promise.then(successCallback, failureCallback);
    return Promise.resolve();
  };
  prototype.setRemoteDescription = withCallback;

  withCallback = function(candidate, successCallback, failureCallback) {
    const promise = addIceCandidate.apply(this, [candidate]);
    if (!failureCallback) {
      return promise;
    }
    promise.then(successCallback, failureCallback);
    return Promise.resolve();
  };
  prototype.addIceCandidate = withCallback;
}

function shimGetUserMedia(window) {
  const navigator = window && window.navigator;

  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    // shim not needed in Safari 12.1
    const mediaDevices = navigator.mediaDevices;
    const _getUserMedia = mediaDevices.getUserMedia.bind(mediaDevices);
    navigator.mediaDevices.getUserMedia = (constraints) => {
      return _getUserMedia(shimConstraints(constraints));
    };
  }

  if (!navigator.getUserMedia && navigator.mediaDevices &&
    navigator.mediaDevices.getUserMedia) {
    navigator.getUserMedia = function getUserMedia(constraints, cb, errcb) {
      navigator.mediaDevices.getUserMedia(constraints)
        .then(cb, errcb);
    }.bind(navigator);
  }
}

function shimConstraints(constraints) {
  if (constraints && constraints.video !== undefined) {
    return Object.assign({},
      constraints,
      {video: _utils__WEBPACK_IMPORTED_MODULE_0__.compactObject(constraints.video)}
    );
  }

  return constraints;
}

function shimRTCIceServerUrls(window) {
  if (!window.RTCPeerConnection) {
    return;
  }
  // migrate from non-spec RTCIceServer.url to RTCIceServer.urls
  const OrigPeerConnection = window.RTCPeerConnection;
  window.RTCPeerConnection =
    function RTCPeerConnection(pcConfig, pcConstraints) {
      if (pcConfig && pcConfig.iceServers) {
        const newIceServers = [];
        for (let i = 0; i < pcConfig.iceServers.length; i++) {
          let server = pcConfig.iceServers[i];
          if (server.urls === undefined && server.url) {
            _utils__WEBPACK_IMPORTED_MODULE_0__.deprecated('RTCIceServer.url', 'RTCIceServer.urls');
            server = JSON.parse(JSON.stringify(server));
            server.urls = server.url;
            delete server.url;
            newIceServers.push(server);
          } else {
            newIceServers.push(pcConfig.iceServers[i]);
          }
        }
        pcConfig.iceServers = newIceServers;
      }
      return new OrigPeerConnection(pcConfig, pcConstraints);
    };
  window.RTCPeerConnection.prototype = OrigPeerConnection.prototype;
  // wrap static methods. Currently just generateCertificate.
  if ('generateCertificate' in OrigPeerConnection) {
    Object.defineProperty(window.RTCPeerConnection, 'generateCertificate', {
      get() {
        return OrigPeerConnection.generateCertificate;
      }
    });
  }
}

function shimTrackEventTransceiver(window) {
  // Add event.transceiver member over deprecated event.receiver
  if (typeof window === 'object' && window.RTCTrackEvent &&
      'receiver' in window.RTCTrackEvent.prototype &&
      !('transceiver' in window.RTCTrackEvent.prototype)) {
    Object.defineProperty(window.RTCTrackEvent.prototype, 'transceiver', {
      get() {
        return {receiver: this.receiver};
      }
    });
  }
}

function shimCreateOfferLegacy(window) {
  const origCreateOffer = window.RTCPeerConnection.prototype.createOffer;
  window.RTCPeerConnection.prototype.createOffer =
    function createOffer(offerOptions) {
      if (offerOptions) {
        if (typeof offerOptions.offerToReceiveAudio !== 'undefined') {
          // support bit values
          offerOptions.offerToReceiveAudio =
            !!offerOptions.offerToReceiveAudio;
        }
        const audioTransceiver = this.getTransceivers().find(transceiver =>
          transceiver.receiver.track.kind === 'audio');
        if (offerOptions.offerToReceiveAudio === false && audioTransceiver) {
          if (audioTransceiver.direction === 'sendrecv') {
            if (audioTransceiver.setDirection) {
              audioTransceiver.setDirection('sendonly');
            } else {
              audioTransceiver.direction = 'sendonly';
            }
          } else if (audioTransceiver.direction === 'recvonly') {
            if (audioTransceiver.setDirection) {
              audioTransceiver.setDirection('inactive');
            } else {
              audioTransceiver.direction = 'inactive';
            }
          }
        } else if (offerOptions.offerToReceiveAudio === true &&
            !audioTransceiver) {
          this.addTransceiver('audio', {direction: 'recvonly'});
        }

        if (typeof offerOptions.offerToReceiveVideo !== 'undefined') {
          // support bit values
          offerOptions.offerToReceiveVideo =
            !!offerOptions.offerToReceiveVideo;
        }
        const videoTransceiver = this.getTransceivers().find(transceiver =>
          transceiver.receiver.track.kind === 'video');
        if (offerOptions.offerToReceiveVideo === false && videoTransceiver) {
          if (videoTransceiver.direction === 'sendrecv') {
            if (videoTransceiver.setDirection) {
              videoTransceiver.setDirection('sendonly');
            } else {
              videoTransceiver.direction = 'sendonly';
            }
          } else if (videoTransceiver.direction === 'recvonly') {
            if (videoTransceiver.setDirection) {
              videoTransceiver.setDirection('inactive');
            } else {
              videoTransceiver.direction = 'inactive';
            }
          }
        } else if (offerOptions.offerToReceiveVideo === true &&
            !videoTransceiver) {
          this.addTransceiver('video', {direction: 'recvonly'});
        }
      }
      return origCreateOffer.apply(this, arguments);
    };
}

function shimAudioContext(window) {
  if (typeof window !== 'object' || window.AudioContext) {
    return;
  }
  window.AudioContext = window.webkitAudioContext;
}



/***/ }),

/***/ "./node_modules/webrtc-adapter/src/js/utils.js":
/*!*****************************************************!*\
  !*** ./node_modules/webrtc-adapter/src/js/utils.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   compactObject: () => (/* binding */ compactObject),
/* harmony export */   deprecated: () => (/* binding */ deprecated),
/* harmony export */   detectBrowser: () => (/* binding */ detectBrowser),
/* harmony export */   disableLog: () => (/* binding */ disableLog),
/* harmony export */   disableWarnings: () => (/* binding */ disableWarnings),
/* harmony export */   extractVersion: () => (/* binding */ extractVersion),
/* harmony export */   filterStats: () => (/* binding */ filterStats),
/* harmony export */   log: () => (/* binding */ log),
/* harmony export */   walkStats: () => (/* binding */ walkStats),
/* harmony export */   wrapPeerConnectionEvent: () => (/* binding */ wrapPeerConnectionEvent)
/* harmony export */ });
/*
 *  Copyright (c) 2016 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree.
 */
/* eslint-env node */


let logDisabled_ = true;
let deprecationWarnings_ = true;

/**
 * Extract browser version out of the provided user agent string.
 *
 * @param {!string} uastring userAgent string.
 * @param {!string} expr Regular expression used as match criteria.
 * @param {!number} pos position in the version string to be returned.
 * @return {!number} browser version.
 */
function extractVersion(uastring, expr, pos) {
  const match = uastring.match(expr);
  return match && match.length >= pos && parseInt(match[pos], 10);
}

// Wraps the peerconnection event eventNameToWrap in a function
// which returns the modified event object (or false to prevent
// the event).
function wrapPeerConnectionEvent(window, eventNameToWrap, wrapper) {
  if (!window.RTCPeerConnection) {
    return;
  }
  const proto = window.RTCPeerConnection.prototype;
  const nativeAddEventListener = proto.addEventListener;
  proto.addEventListener = function(nativeEventName, cb) {
    if (nativeEventName !== eventNameToWrap) {
      return nativeAddEventListener.apply(this, arguments);
    }
    const wrappedCallback = (e) => {
      const modifiedEvent = wrapper(e);
      if (modifiedEvent) {
        if (cb.handleEvent) {
          cb.handleEvent(modifiedEvent);
        } else {
          cb(modifiedEvent);
        }
      }
    };
    this._eventMap = this._eventMap || {};
    if (!this._eventMap[eventNameToWrap]) {
      this._eventMap[eventNameToWrap] = new Map();
    }
    this._eventMap[eventNameToWrap].set(cb, wrappedCallback);
    return nativeAddEventListener.apply(this, [nativeEventName,
      wrappedCallback]);
  };

  const nativeRemoveEventListener = proto.removeEventListener;
  proto.removeEventListener = function(nativeEventName, cb) {
    if (nativeEventName !== eventNameToWrap || !this._eventMap
        || !this._eventMap[eventNameToWrap]) {
      return nativeRemoveEventListener.apply(this, arguments);
    }
    if (!this._eventMap[eventNameToWrap].has(cb)) {
      return nativeRemoveEventListener.apply(this, arguments);
    }
    const unwrappedCb = this._eventMap[eventNameToWrap].get(cb);
    this._eventMap[eventNameToWrap].delete(cb);
    if (this._eventMap[eventNameToWrap].size === 0) {
      delete this._eventMap[eventNameToWrap];
    }
    if (Object.keys(this._eventMap).length === 0) {
      delete this._eventMap;
    }
    return nativeRemoveEventListener.apply(this, [nativeEventName,
      unwrappedCb]);
  };

  Object.defineProperty(proto, 'on' + eventNameToWrap, {
    get() {
      return this['_on' + eventNameToWrap];
    },
    set(cb) {
      if (this['_on' + eventNameToWrap]) {
        this.removeEventListener(eventNameToWrap,
          this['_on' + eventNameToWrap]);
        delete this['_on' + eventNameToWrap];
      }
      if (cb) {
        this.addEventListener(eventNameToWrap,
          this['_on' + eventNameToWrap] = cb);
      }
    },
    enumerable: true,
    configurable: true
  });
}

function disableLog(bool) {
  if (typeof bool !== 'boolean') {
    return new Error('Argument type: ' + typeof bool +
        '. Please use a boolean.');
  }
  logDisabled_ = bool;
  return (bool) ? 'adapter.js logging disabled' :
    'adapter.js logging enabled';
}

/**
 * Disable or enable deprecation warnings
 * @param {!boolean} bool set to true to disable warnings.
 */
function disableWarnings(bool) {
  if (typeof bool !== 'boolean') {
    return new Error('Argument type: ' + typeof bool +
        '. Please use a boolean.');
  }
  deprecationWarnings_ = !bool;
  return 'adapter.js deprecation warnings ' + (bool ? 'disabled' : 'enabled');
}

function log() {
  if (typeof window === 'object') {
    if (logDisabled_) {
      return;
    }
    if (typeof console !== 'undefined' && typeof console.log === 'function') {
      console.log.apply(console, arguments);
    }
  }
}

/**
 * Shows a deprecation warning suggesting the modern and spec-compatible API.
 */
function deprecated(oldMethod, newMethod) {
  if (!deprecationWarnings_) {
    return;
  }
  console.warn(oldMethod + ' is deprecated, please use ' + newMethod +
      ' instead.');
}

/**
 * Browser detector.
 *
 * @return {object} result containing browser and version
 *     properties.
 */
function detectBrowser(window) {
  // Returned result object.
  const result = {browser: null, version: null};

  // Fail early if it's not a browser
  if (typeof window === 'undefined' || !window.navigator) {
    result.browser = 'Not a browser.';
    return result;
  }

  const {navigator} = window;

  if (navigator.mozGetUserMedia) { // Firefox.
    result.browser = 'firefox';
    result.version = extractVersion(navigator.userAgent,
      /Firefox\/(\d+)\./, 1);
  } else if (navigator.webkitGetUserMedia ||
      (window.isSecureContext === false && window.webkitRTCPeerConnection)) {
    // Chrome, Chromium, Webview, Opera.
    // Version matches Chrome/WebRTC version.
    // Chrome 74 removed webkitGetUserMedia on http as well so we need the
    // more complicated fallback to webkitRTCPeerConnection.
    result.browser = 'chrome';
    result.version = extractVersion(navigator.userAgent,
      /Chrom(e|ium)\/(\d+)\./, 2);
  } else if (window.RTCPeerConnection &&
      navigator.userAgent.match(/AppleWebKit\/(\d+)\./)) { // Safari.
    result.browser = 'safari';
    result.version = extractVersion(navigator.userAgent,
      /AppleWebKit\/(\d+)\./, 1);
    result.supportsUnifiedPlan = window.RTCRtpTransceiver &&
        'currentDirection' in window.RTCRtpTransceiver.prototype;
  } else { // Default fallthrough: not supported.
    result.browser = 'Not a supported browser.';
    return result;
  }

  return result;
}

/**
 * Checks if something is an object.
 *
 * @param {*} val The something you want to check.
 * @return true if val is an object, false otherwise.
 */
function isObject(val) {
  return Object.prototype.toString.call(val) === '[object Object]';
}

/**
 * Remove all empty objects and undefined values
 * from a nested object -- an enhanced and vanilla version
 * of Lodash's `compact`.
 */
function compactObject(data) {
  if (!isObject(data)) {
    return data;
  }

  return Object.keys(data).reduce(function(accumulator, key) {
    const isObj = isObject(data[key]);
    const value = isObj ? compactObject(data[key]) : data[key];
    const isEmptyObject = isObj && !Object.keys(value).length;
    if (value === undefined || isEmptyObject) {
      return accumulator;
    }
    return Object.assign(accumulator, {[key]: value});
  }, {});
}

/* iterates the stats graph recursively. */
function walkStats(stats, base, resultSet) {
  if (!base || resultSet.has(base.id)) {
    return;
  }
  resultSet.set(base.id, base);
  Object.keys(base).forEach(name => {
    if (name.endsWith('Id')) {
      walkStats(stats, stats.get(base[name]), resultSet);
    } else if (name.endsWith('Ids')) {
      base[name].forEach(id => {
        walkStats(stats, stats.get(id), resultSet);
      });
    }
  });
}

/* filter getStats for a sender/receiver track. */
function filterStats(result, track, outbound) {
  const streamStatsType = outbound ? 'outbound-rtp' : 'inbound-rtp';
  const filteredResult = new Map();
  if (track === null) {
    return filteredResult;
  }
  const trackStats = [];
  result.forEach(value => {
    if (value.type === 'track' &&
        value.trackIdentifier === track.id) {
      trackStats.push(value);
    }
  });
  trackStats.forEach(trackStat => {
    result.forEach(stats => {
      if (stats.type === streamStatsType && stats.trackId === trackStat.id) {
        walkStats(result, stats, filteredResult);
      }
    });
  });
  return filteredResult;
}



/***/ }),

/***/ "./src/awrtc/index.ts":
/*!****************************!*\
  !*** ./src/awrtc/index.ts ***!
  \****************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   AWebRtcCall: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.AWebRtcCall),
/* harmony export */   AWebRtcPeer: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.AWebRtcPeer),
/* harmony export */   AudioProcessor: () => (/* reexport safe */ _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.AudioProcessor),
/* harmony export */   AutoplayResolver: () => (/* reexport safe */ _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.AutoplayResolver),
/* harmony export */   BrowserMediaNetwork: () => (/* reexport safe */ _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.BrowserMediaNetwork),
/* harmony export */   BrowserMediaStream: () => (/* reexport safe */ _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.BrowserMediaStream),
/* harmony export */   BrowserWebRtcCall: () => (/* reexport safe */ _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.BrowserWebRtcCall),
/* harmony export */   CAPI_DeviceApi_LastUpdate: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_DeviceApi_LastUpdate),
/* harmony export */   CAPI_DeviceApi_RequestUpdate: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_DeviceApi_RequestUpdate),
/* harmony export */   CAPI_DeviceApi_Update: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_DeviceApi_Update),
/* harmony export */   CAPI_InitAsync: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_InitAsync),
/* harmony export */   CAPI_MediaNetwork_Configure: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_Configure),
/* harmony export */   CAPI_MediaNetwork_Create: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_Create),
/* harmony export */   CAPI_MediaNetwork_GetConfigurationError: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_GetConfigurationError),
/* harmony export */   CAPI_MediaNetwork_GetConfigurationError_Length: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_GetConfigurationError_Length),
/* harmony export */   CAPI_MediaNetwork_GetConfigurationState: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_GetConfigurationState),
/* harmony export */   CAPI_MediaNetwork_HasAudioTrack: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_HasAudioTrack),
/* harmony export */   CAPI_MediaNetwork_HasUserMedia: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_HasUserMedia),
/* harmony export */   CAPI_MediaNetwork_HasVideoTrack: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_HasVideoTrack),
/* harmony export */   CAPI_MediaNetwork_IsAvailable: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_IsAvailable),
/* harmony export */   CAPI_MediaNetwork_IsMute: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_IsMute),
/* harmony export */   CAPI_MediaNetwork_ResetConfiguration: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_ResetConfiguration),
/* harmony export */   CAPI_MediaNetwork_SetMute: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_SetMute),
/* harmony export */   CAPI_MediaNetwork_SetVolume: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_SetVolume),
/* harmony export */   CAPI_MediaNetwork_SetVolumePan: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_SetVolumePan),
/* harmony export */   CAPI_MediaNetwork_TryGetFrame: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_TryGetFrame),
/* harmony export */   CAPI_MediaNetwork_TryGetFrameDataLength: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_TryGetFrameDataLength),
/* harmony export */   CAPI_MediaNetwork_TryGetFrame_Resolution: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_TryGetFrame_Resolution),
/* harmony export */   CAPI_MediaNetwork_TryGetFrame_ToTexture: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_MediaNetwork_TryGetFrame_ToTexture),
/* harmony export */   CAPI_Media_EnableScreenCapture: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_Media_EnableScreenCapture),
/* harmony export */   CAPI_Media_GetAudioInputDevices: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_Media_GetAudioInputDevices),
/* harmony export */   CAPI_Media_GetAudioInputDevices_Length: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_Media_GetAudioInputDevices_Length),
/* harmony export */   CAPI_Media_GetVideoDevices: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_Media_GetVideoDevices),
/* harmony export */   CAPI_Media_GetVideoDevices_Length: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_Media_GetVideoDevices_Length),
/* harmony export */   CAPI_PollInitState: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_PollInitState),
/* harmony export */   CAPI_SLog_SetLogLevel: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_SLog_SetLogLevel),
/* harmony export */   CAPI_VideoInput_AddCanvasDevice: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_VideoInput_AddCanvasDevice),
/* harmony export */   CAPI_VideoInput_AddDevice: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_VideoInput_AddDevice),
/* harmony export */   CAPI_VideoInput_RemoveDevice: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_VideoInput_RemoveDevice),
/* harmony export */   CAPI_VideoInput_UpdateFrame: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_VideoInput_UpdateFrame),
/* harmony export */   CAPI_WebRtcNetwork_CheckEventLength: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_CheckEventLength),
/* harmony export */   CAPI_WebRtcNetwork_Connect: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_Connect),
/* harmony export */   CAPI_WebRtcNetwork_Create: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_Create),
/* harmony export */   CAPI_WebRtcNetwork_Dequeue: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_Dequeue),
/* harmony export */   CAPI_WebRtcNetwork_DequeueEm: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_DequeueEm),
/* harmony export */   CAPI_WebRtcNetwork_DequeueRtcEvent: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_DequeueRtcEvent),
/* harmony export */   CAPI_WebRtcNetwork_Disconnect: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_Disconnect),
/* harmony export */   CAPI_WebRtcNetwork_EventDataToUint8Array: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_EventDataToUint8Array),
/* harmony export */   CAPI_WebRtcNetwork_Flush: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_Flush),
/* harmony export */   CAPI_WebRtcNetwork_GetBufferedAmount: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_GetBufferedAmount),
/* harmony export */   CAPI_WebRtcNetwork_IsAvailable: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_IsAvailable),
/* harmony export */   CAPI_WebRtcNetwork_IsBrowserSupported: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_IsBrowserSupported),
/* harmony export */   CAPI_WebRtcNetwork_Peek: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_Peek),
/* harmony export */   CAPI_WebRtcNetwork_PeekEm: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_PeekEm),
/* harmony export */   CAPI_WebRtcNetwork_PeekEventDataLength: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_PeekEventDataLength),
/* harmony export */   CAPI_WebRtcNetwork_Release: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_Release),
/* harmony export */   CAPI_WebRtcNetwork_RequestStats: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_RequestStats),
/* harmony export */   CAPI_WebRtcNetwork_SendData: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_SendData),
/* harmony export */   CAPI_WebRtcNetwork_SendDataEm: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_SendDataEm),
/* harmony export */   CAPI_WebRtcNetwork_Shutdown: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_Shutdown),
/* harmony export */   CAPI_WebRtcNetwork_StartServer: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_StartServer),
/* harmony export */   CAPI_WebRtcNetwork_StopServer: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_StopServer),
/* harmony export */   CAPI_WebRtcNetwork_Update: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.CAPI_WebRtcNetwork_Update),
/* harmony export */   CallAcceptedEventArgs: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.CallAcceptedEventArgs),
/* harmony export */   CallEndedEventArgs: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.CallEndedEventArgs),
/* harmony export */   CallErrorType: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.CallErrorType),
/* harmony export */   CallEventArgs: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.CallEventArgs),
/* harmony export */   CallEventType: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.CallEventType),
/* harmony export */   ConnectionId: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId),
/* harmony export */   DataMessageEventArgs: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.DataMessageEventArgs),
/* harmony export */   Debug: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.Debug),
/* harmony export */   DeviceApi: () => (/* reexport safe */ _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.DeviceApi),
/* harmony export */   Encoder: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.Encoder),
/* harmony export */   Encoding: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.Encoding),
/* harmony export */   ErrorEventArgs: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.ErrorEventArgs),
/* harmony export */   FramePixelFormat: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.FramePixelFormat),
/* harmony export */   FrameUpdateEventArgs: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.FrameUpdateEventArgs),
/* harmony export */   GetUnityCanvas: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.GetUnityCanvas),
/* harmony export */   GetUnityContext: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.GetUnityContext),
/* harmony export */   Helper: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.Helper),
/* harmony export */   IFrameData: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.IFrameData),
/* harmony export */   LazyFrame: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.LazyFrame),
/* harmony export */   List: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.List),
/* harmony export */   LocalNetwork: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.LocalNetwork),
/* harmony export */   Media: () => (/* reexport safe */ _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.Media),
/* harmony export */   MediaConfig: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.MediaConfig),
/* harmony export */   MediaConfigurationState: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.MediaConfigurationState),
/* harmony export */   MediaDevice: () => (/* reexport safe */ _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.MediaDevice),
/* harmony export */   MediaPeer: () => (/* reexport safe */ _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.MediaPeer),
/* harmony export */   MediaUpdatedEventArgs: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.MediaUpdatedEventArgs),
/* harmony export */   MessageEventArgs: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.MessageEventArgs),
/* harmony export */   NetEventDataType: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.NetEventDataType),
/* harmony export */   NetEventType: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType),
/* harmony export */   NetworkConfig: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.NetworkConfig),
/* harmony export */   NetworkEvent: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent),
/* harmony export */   Output: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.Output),
/* harmony export */   PeerConfig: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.PeerConfig),
/* harmony export */   Queue: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.Queue),
/* harmony export */   Random: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.Random),
/* harmony export */   RawFrame: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.RawFrame),
/* harmony export */   RtcEvent: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.RtcEvent),
/* harmony export */   RtcEventType: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.RtcEventType),
/* harmony export */   SLog: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog),
/* harmony export */   SLogLevel: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.SLogLevel),
/* harmony export */   SLogger: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.SLogger),
/* harmony export */   SignalingInfo: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.SignalingInfo),
/* harmony export */   StatsEvent: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.StatsEvent),
/* harmony export */   StreamAddedEvent: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.StreamAddedEvent),
/* harmony export */   UTF16Encoding: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.UTF16Encoding),
/* harmony export */   VideoInput: () => (/* reexport safe */ _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.VideoInput),
/* harmony export */   VideoInputType: () => (/* reexport safe */ _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.VideoInputType),
/* harmony export */   WaitForIncomingCallEventArgs: () => (/* reexport safe */ _media_index__WEBPACK_IMPORTED_MODULE_1__.WaitForIncomingCallEventArgs),
/* harmony export */   WebRtcDataPeer: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.WebRtcDataPeer),
/* harmony export */   WebRtcHelper: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.WebRtcHelper),
/* harmony export */   WebRtcInternalState: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.WebRtcInternalState),
/* harmony export */   WebRtcNetwork: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.WebRtcNetwork),
/* harmony export */   WebRtcNetworkServerState: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.WebRtcNetworkServerState),
/* harmony export */   WebRtcPeerState: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.WebRtcPeerState),
/* harmony export */   WebsocketConnectionStatus: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus),
/* harmony export */   WebsocketNetwork: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketNetwork),
/* harmony export */   WebsocketServerStatus: () => (/* reexport safe */ _network_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketServerStatus),
/* harmony export */   gCAPI_WebRtcNetwork_Instances: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.gCAPI_WebRtcNetwork_Instances),
/* harmony export */   gCAPI_WebRtcNetwork_InstancesNextIndex: () => (/* reexport safe */ _unity_index__WEBPACK_IMPORTED_MODULE_3__.gCAPI_WebRtcNetwork_InstancesNextIndex)
/* harmony export */ });
/* harmony import */ var _network_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./network/index */ "./src/awrtc/network/index.ts");
/* harmony import */ var _media_index__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./media/index */ "./src/awrtc/media/index.ts");
/* harmony import */ var _media_browser_index__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./media_browser/index */ "./src/awrtc/media_browser/index.ts");
/* harmony import */ var _unity_index__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./unity/index */ "./src/awrtc/unity/index.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
console.debug("loading awrtc modules ...");
//this should trigger webpack to include
//the webrtc adapter.js. It changes the 
//global WebRTC calls and adds backwards
//and browser compatibility.


//for simplicity browser and unity are merged here
//it could as well be built and deployed separately


console.debug("loading awrtc modules completed!");


/***/ }),

/***/ "./src/awrtc/media/AWebRtcCall.ts":
/*!****************************************!*\
  !*** ./src/awrtc/media/AWebRtcCall.ts ***!
  \****************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   AWebRtcCall: () => (/* binding */ AWebRtcCall)
/* harmony export */ });
/* harmony import */ var _IMediaNetwork__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./IMediaNetwork */ "./src/awrtc/media/IMediaNetwork.ts");
/* harmony import */ var _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./CallEventArgs */ "./src/awrtc/media/CallEventArgs.ts");
/* harmony import */ var _network_Helper__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ../network/Helper */ "./src/awrtc/network/Helper.ts");
/* harmony import */ var _network_NetworkConfig__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ../network/NetworkConfig */ "./src/awrtc/network/NetworkConfig.ts");
/* harmony import */ var _MediaConfig__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! ./MediaConfig */ "./src/awrtc/media/MediaConfig.ts");
/* harmony import */ var _network_index__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! ../network/index */ "./src/awrtc/network/index.ts");
/* harmony import */ var _network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! ../network/IWebRtcNetwork */ "./src/awrtc/network/IWebRtcNetwork.ts");







class CallException {
    ErrorMsg() {
    }
    constructor(errorMsg) {
        this.mErrorMsg = errorMsg;
    }
}
class InvalidOperationException extends CallException {
}
/// <summary>
/// State of the call. Mainly used to check for bugs / invalid states.
/// </summary>
var CallState;
(function (CallState) {
    /// <summary>
    /// Not yet initialized / bug
    /// </summary>
    CallState[CallState["Invalid"] = 0] = "Invalid";
    /// <summary>
    /// Object is initialized but local media not yet configured
    /// </summary>
    CallState[CallState["Initialized"] = 1] = "Initialized";
    /// <summary>
    /// In process of accessing the local media devices.
    /// </summary>
    CallState[CallState["Configuring"] = 2] = "Configuring";
    /// <summary>
    /// Configured. Video/Audio can be accessed and call is ready to start
    /// </summary>
    CallState[CallState["Configured"] = 3] = "Configured";
    /// <summary>
    /// In process of requesting an address from the server to then listen and wait for
    /// an incoming call.
    /// </summary>
    CallState[CallState["RequestingAddress"] = 4] = "RequestingAddress";
    /// <summary>
    /// Call is listening on an address and waiting for an incoming call
    /// </summary>
    CallState[CallState["WaitingForIncomingCall"] = 5] = "WaitingForIncomingCall";
    /// <summary>
    /// Call is in the process of connecting to another call object.
    /// </summary>
    CallState[CallState["WaitingForOutgoingCall"] = 6] = "WaitingForOutgoingCall";
    /// <summary>
    /// Indicating that the call object is at least connected to another object
    /// </summary>
    CallState[CallState["InCall"] = 7] = "InCall";
    //CallAcceptedIncoming,
    //CallAcceptedOutgoing,
    /// <summary>
    /// Call ended / conference room closed
    /// </summary>
    CallState[CallState["Closed"] = 8] = "Closed";
})(CallState || (CallState = {}));
/*
class ConnectionMetaData
{
}
*/
class ConnectionInfo {
    constructor() {
        this.mConnectionIds = new Array();
        //public GetMeta(id:ConnectionId) : ConnectionMetaData
        //{
        //    return this.mConnectionMeta[id.id];
        //}
    }
    //private mConnectionMeta: { [id: number]: ConnectionMetaData } = {};
    AddConnection(id, incoming) {
        this.mConnectionIds.push(id.id);
        //this.mConnectionMeta[id.id] = new ConnectionMetaData();
    }
    RemConnection(id) {
        let index = this.mConnectionIds.indexOf(id.id);
        if (index >= 0) {
            this.mConnectionIds.splice(index, 1);
        }
        else {
            _network_Helper__WEBPACK_IMPORTED_MODULE_2__.SLog.LE("tried to remove an unknown connection with id " + id.id);
        }
        //delete this.mConnectionMeta[id.id];
    }
    HasConnection(id) {
        return this.mConnectionIds.indexOf(id.id) != -1;
    }
    GetIds() {
        return this.mConnectionIds;
    }
}
/**This class wraps an implementation of
 * IMediaStream and converts its polling system
 * to an easier to use event based system.
 *
 * Ideally use only features defined by
 * ICall to avoid having to deal with internal changes
 * in future updates.
 */
class AWebRtcCall {
    addEventListener(listener) {
        this.mCallEventHandlers.push(listener);
    }
    removeEventListener(listener) {
        this.mCallEventHandlers = this.mCallEventHandlers.filter(h => h !== listener);
    }
    get State() {
        return this.mState;
    }
    constructor(config = null) {
        this.MESSAGE_TYPE_INVALID = 0;
        this.MESSAGE_TYPE_DATA = 1;
        this.MESSAGE_TYPE_STRING = 2;
        this.MESSAGE_TYPE_CONTROL = 3;
        this.mNetworkConfig = new _network_NetworkConfig__WEBPACK_IMPORTED_MODULE_3__.NetworkConfig();
        this.mMediaConfig = new _MediaConfig__WEBPACK_IMPORTED_MODULE_4__.MediaConfig();
        this.mCallEventHandlers = [];
        this.mNetwork = null;
        this.mConnectionInfo = new ConnectionInfo();
        this.mConferenceMode = false;
        this.mState = CallState.Invalid;
        this.mIsDisposed = false;
        this.mServerInactive = true;
        this.mPendingListenCall = false;
        this.mPendingCallCall = false;
        this.mPendingAddress = null;
        if (config != null) {
            this.mNetworkConfig = config;
            this.mConferenceMode = config.IsConference;
        }
    }
    Initialize(network) {
        this.mNetwork = network;
        this.mState = CallState.Initialized;
    }
    Configure(config) {
        this.CheckDisposed();
        /*
        if (this.mState != CallState.Initialized) {
            throw new InvalidOperationException("Method can't be used in state " + this.mState);
        }
        */
        this.mState = CallState.Configuring;
        _network_Helper__WEBPACK_IMPORTED_MODULE_2__.SLog.Log("Enter state CallState.Configuring");
        this.mMediaConfig = config;
        this.mNetwork.Configure(this.mMediaConfig);
    }
    Call(address) {
        this.CheckDisposed();
        if (this.mState != CallState.Initialized
            && this.mState != CallState.Configuring
            && this.mState != CallState.Configured) {
            throw new InvalidOperationException("Method can't be used in state " + this.mState);
        }
        if (this.mConferenceMode) {
            throw new InvalidOperationException("Method can't be used in conference calls.");
        }
        _network_Helper__WEBPACK_IMPORTED_MODULE_2__.SLog.Log("Call to " + address);
        this.EnsureConfiguration();
        if (this.mState == CallState.Configured) {
            this.ProcessCall(address);
        }
        else {
            this.PendingCall(address);
        }
    }
    Listen(address) {
        this.CheckDisposed();
        if (this.mState != CallState.Initialized
            && this.mState != CallState.Configuring
            && this.mState != CallState.Configured) {
            throw new InvalidOperationException("Method can't be used in state " + this.mState);
        }
        this.EnsureConfiguration();
        if (this.mState == CallState.Configured) {
            this.ProcessListen(address);
        }
        else {
            this.PendingListen(address);
        }
    }
    Send(message, reliable, id) {
        this.CheckDisposed();
        if (reliable == null)
            reliable = true;
        if (id) {
            this.InternalSendTo(message, reliable, id);
        }
        else {
            this.InternalSendToAll(message, reliable);
        }
    }
    InternalSendToAll(message, reliable) {
        let data = this.PackStringMsg(message);
        ;
        for (let id of this.mConnectionInfo.GetIds()) {
            _network_Helper__WEBPACK_IMPORTED_MODULE_2__.SLog.L("Send message to " + id + "! " + message);
            this.InternalSendRawTo(data, new _network_index__WEBPACK_IMPORTED_MODULE_5__.ConnectionId(id), reliable);
        }
    }
    InternalSendTo(message, reliable, id) {
        let data = this.PackStringMsg(message);
        this.InternalSendRawTo(data, id, reliable);
    }
    SendData(message, reliable, id) {
        this.CheckDisposed();
        let data = this.PackDataMsg(message);
        this.InternalSendRawTo(data, id, reliable);
    }
    PackStringMsg(message) {
        let data = _network_Helper__WEBPACK_IMPORTED_MODULE_2__.Encoding.UTF16.GetBytes(message);
        let buff = new Uint8Array(data.length + 1);
        buff[0] = this.MESSAGE_TYPE_STRING;
        for (let i = 0; i < data.length; i++) {
            buff[i + 1] = data[i];
        }
        return buff;
    }
    UnpackStringMsg(message) {
        let buff = new Uint8Array(message.length - 1);
        for (let i = 0; i < buff.length; i++) {
            buff[i] = message[i + 1];
        }
        let res = _network_Helper__WEBPACK_IMPORTED_MODULE_2__.Encoding.UTF16.GetString(buff);
        return res;
    }
    PackDataMsg(data) {
        let buff = new Uint8Array(data.length + 1);
        buff[0] = this.MESSAGE_TYPE_DATA;
        for (let i = 0; i < data.length; i++) {
            buff[i + 1] = data[i];
        }
        return buff;
    }
    UnpackDataMsg(message) {
        let buff = new Uint8Array(message.length - 1);
        for (let i = 0; i < buff.length; i++) {
            buff[i] = message[i + 1];
        }
        return buff;
    }
    InternalSendRawTo(rawdata, id, reliable) {
        this.mNetwork.SendData(id, rawdata, reliable);
    }
    Update() {
        if (this.mIsDisposed)
            return;
        if (this.mNetwork == null)
            return;
        this.mNetwork.Update();
        //waiting for the media configuration?
        if (this.mState == CallState.Configuring) {
            var configState = this.mNetwork.GetConfigurationState();
            if (configState == _IMediaNetwork__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.Failed) {
                this.OnConfigurationFailed(this.mNetwork.GetConfigurationError());
                //bugfix: user might dispose the call during the event above
                if (this.mIsDisposed)
                    return;
                if (this.mNetwork != null)
                    this.mNetwork.ResetConfiguration();
            }
            else if (configState == _IMediaNetwork__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.Successful) {
                this.OnConfigurationComplete();
                if (this.mIsDisposed)
                    return;
            }
        }
        let evt;
        while ((evt = this.mNetwork.Dequeue()) != null) {
            switch (evt.Type) {
                case _network_index__WEBPACK_IMPORTED_MODULE_5__.NetEventType.NewConnection:
                    if (this.mState == CallState.WaitingForIncomingCall
                        || (this.mConferenceMode && this.mState == CallState.InCall)) //keep accepting connections after 
                     {
                        //remove ability to accept incoming connections
                        if (this.mConferenceMode == false)
                            this.mNetwork.StopServer();
                        this.mState = CallState.InCall;
                        this.mConnectionInfo.AddConnection(evt.ConnectionId, true);
                        this.TriggerCallEvent(new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallAcceptedEventArgs(evt.ConnectionId));
                        if (this.mIsDisposed)
                            return;
                    }
                    else if (this.mState == CallState.WaitingForOutgoingCall) {
                        this.mConnectionInfo.AddConnection(evt.ConnectionId, false);
                        //only possible in 1 on 1 calls
                        this.mState = CallState.InCall;
                        this.TriggerCallEvent(new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallAcceptedEventArgs(evt.ConnectionId));
                        if (this.mIsDisposed)
                            return;
                    }
                    else {
                        //Debug.Assert(mState == CallState.WaitingForIncomingCall || mState == CallState.WaitingForOutgoingCall);
                        _network_Helper__WEBPACK_IMPORTED_MODULE_2__.SLog.LogWarning("Received incoming connection during invalid state " + this.mState);
                    }
                    break;
                case _network_index__WEBPACK_IMPORTED_MODULE_5__.NetEventType.ConnectionFailed:
                    //call failed
                    if (this.mState == CallState.WaitingForOutgoingCall) {
                        this.TriggerCallEvent(new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.ErrorEventArgs(_CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallEventType.ConnectionFailed));
                        if (this.mIsDisposed)
                            return;
                        this.mState = CallState.Configured;
                    }
                    else {
                        //Debug.Assert(mState == CallState.WaitingForOutgoingCall);
                        _network_Helper__WEBPACK_IMPORTED_MODULE_2__.SLog.LogError("Received ConnectionFailed during " + this.mState);
                    }
                    break;
                case _network_index__WEBPACK_IMPORTED_MODULE_5__.NetEventType.Disconnected:
                    if (this.mConnectionInfo.HasConnection(evt.ConnectionId)) {
                        this.mConnectionInfo.RemConnection(evt.ConnectionId);
                        //call ended
                        if (this.mConferenceMode == false && this.mConnectionInfo.GetIds().length == 0) {
                            this.mState = CallState.Closed;
                        }
                        this.TriggerCallEvent(new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallEndedEventArgs(evt.ConnectionId));
                        if (this.mIsDisposed)
                            return;
                    }
                    break;
                case _network_index__WEBPACK_IMPORTED_MODULE_5__.NetEventType.ServerInitialized:
                    //incoming calls possible
                    this.mServerInactive = false;
                    this.mState = CallState.WaitingForIncomingCall;
                    this.TriggerCallEvent(new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.WaitForIncomingCallEventArgs(evt.Info));
                    if (this.mIsDisposed)
                        return;
                    break;
                case _network_index__WEBPACK_IMPORTED_MODULE_5__.NetEventType.ServerInitFailed:
                    this.mServerInactive = true;
                    //reset state to the earlier state which is Configured (as without configuration no
                    //listening possible). Local camera/audio will keep running
                    this.mState = CallState.Configured;
                    this.TriggerCallEvent(new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.ErrorEventArgs(_CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallEventType.ListeningFailed));
                    if (this.mIsDisposed)
                        return;
                    break;
                case _network_index__WEBPACK_IMPORTED_MODULE_5__.NetEventType.ServerClosed:
                    this.mServerInactive = true;
                    //no incoming calls possible anymore
                    if (this.mState == CallState.WaitingForIncomingCall || this.mState == CallState.RequestingAddress) {
                        this.mState = CallState.Configured;
                        //might need to be handled as a special timeout event?
                        this.TriggerCallEvent(new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.ErrorEventArgs(_CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallEventType.ListeningFailed, _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallErrorType.Unknown, "Server closed the connection while waiting for incoming calls."));
                        if (this.mIsDisposed)
                            return;
                    }
                    else {
                        //event is normal during other states as the server connection will be closed after receiving a call
                    }
                    break;
                case _network_index__WEBPACK_IMPORTED_MODULE_5__.NetEventType.ReliableMessageReceived:
                case _network_index__WEBPACK_IMPORTED_MODULE_5__.NetEventType.UnreliableMessageReceived:
                    let reliable = evt.Type === _network_index__WEBPACK_IMPORTED_MODULE_5__.NetEventType.ReliableMessageReceived;
                    //chat message received
                    if (evt.MessageData.length >= 2) {
                        if (evt.MessageData[0] == this.MESSAGE_TYPE_STRING) {
                            let message = this.UnpackStringMsg(evt.MessageData);
                            this.TriggerCallEvent(new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.MessageEventArgs(evt.ConnectionId, message, reliable));
                        }
                        else if (evt.MessageData[0] == this.MESSAGE_TYPE_DATA) {
                            let message = this.UnpackDataMsg(evt.MessageData);
                            this.TriggerCallEvent(new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.DataMessageEventArgs(evt.ConnectionId, message, reliable));
                        }
                        else {
                            //invalid message?
                        }
                    }
                    else {
                        //invalid message?
                    }
                    if (this.mIsDisposed)
                        return;
                    break;
            }
        }
        let handleLocalFrames = true;
        let handleRemoteFrames = true;
        if (this.mMediaConfig.FrameUpdates && handleLocalFrames) {
            let localFrame = this.mNetwork.TryGetFrame(_network_index__WEBPACK_IMPORTED_MODULE_5__.ConnectionId.INVALID);
            if (localFrame != null) {
                this.FrameToCallEvent(_network_index__WEBPACK_IMPORTED_MODULE_5__.ConnectionId.INVALID, localFrame);
                if (this.mIsDisposed)
                    return;
            }
        }
        if (this.mMediaConfig.FrameUpdates && handleRemoteFrames) {
            for (var id of this.mConnectionInfo.GetIds()) {
                let conId = new _network_index__WEBPACK_IMPORTED_MODULE_5__.ConnectionId(id);
                let remoteFrame = this.mNetwork.TryGetFrame(conId);
                if (remoteFrame != null) {
                    this.FrameToCallEvent(conId, remoteFrame);
                    if (this.mIsDisposed)
                        return;
                }
            }
        }
        let rtcEvent = null;
        while ((rtcEvent = this.mNetwork.DequeueRtcEvent()) != null) {
            this.MediaEventToCallEvent(rtcEvent);
        }
        this.mNetwork.Flush();
    }
    FrameToCallEvent(id, frame) {
        let args = new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.FrameUpdateEventArgs(id, frame);
        this.TriggerCallEvent(args);
    }
    MediaEventToCallEvent(inevt) {
        if (inevt.EventType == _network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_6__.RtcEventType.StreamAdded) {
            const evt = inevt;
            let args = new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.MediaUpdatedEventArgs(evt.ConnectionId, evt.Args);
            this.TriggerCallEvent(args);
        }
        else {
            _network_Helper__WEBPACK_IMPORTED_MODULE_2__.SLog.L("Event type " + _network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_6__.RtcEventType[inevt.EventType] + " ignored.");
        }
    }
    PendingCall(address) {
        this.mPendingAddress = address;
        this.mPendingCallCall = true;
        this.mPendingListenCall = false;
    }
    ProcessCall(address) {
        this.mState = CallState.WaitingForOutgoingCall;
        this.mNetwork.Connect(address);
        this.ClearPending();
    }
    PendingListen(address) {
        this.mPendingAddress = address;
        this.mPendingCallCall = false;
        this.mPendingListenCall = true;
    }
    ProcessListen(address) {
        _network_Helper__WEBPACK_IMPORTED_MODULE_2__.SLog.Log("Listen at " + address);
        this.mServerInactive = false;
        this.mState = CallState.RequestingAddress;
        this.mNetwork.StartServer(address);
        this.ClearPending();
    }
    DoPending() {
        if (this.mPendingCallCall) {
            this.ProcessCall(this.mPendingAddress);
        }
        else if (this.mPendingListenCall) {
            this.ProcessListen(this.mPendingAddress);
        }
        this.ClearPending();
    }
    ClearPending() {
        this.mPendingAddress = null;
        this.mPendingCallCall = null;
        this.mPendingListenCall = null;
    }
    CheckDisposed() {
        if (this.mIsDisposed)
            throw new InvalidOperationException("Object is disposed. No method calls possible.");
    }
    EnsureConfiguration() {
        if (this.mState == CallState.Initialized) {
            _network_Helper__WEBPACK_IMPORTED_MODULE_2__.SLog.Log("Use default configuration");
            this.Configure(new _MediaConfig__WEBPACK_IMPORTED_MODULE_4__.MediaConfig());
        }
        else {
        }
    }
    TriggerCallEvent(args) {
        let arr = this.mCallEventHandlers.slice();
        for (let callback of arr) {
            callback(this, args);
        }
    }
    OnConfigurationComplete() {
        if (this.mIsDisposed)
            return;
        this.mState = CallState.Configured;
        _network_Helper__WEBPACK_IMPORTED_MODULE_2__.SLog.Log("Enter state CallState.Configured");
        this.TriggerCallEvent(new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallEventArgs(_CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallEventType.ConfigurationComplete));
        if (this.mIsDisposed == false)
            this.DoPending();
    }
    OnConfigurationFailed(error) {
        _network_Helper__WEBPACK_IMPORTED_MODULE_2__.SLog.LogWarning("Configuration failed: " + error);
        if (this.mIsDisposed)
            return;
        this.mState = CallState.Initialized;
        this.TriggerCallEvent(new _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.ErrorEventArgs(_CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallEventType.ConfigurationFailed, _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallErrorType.Unknown, error));
        //bugfix: user might dispose the call during the event above
        if (this.mIsDisposed == false)
            this.ClearPending();
    }
    DisposeInternal(disposing) {
        //nothing to dispose but subclasses overwrite this
        if (!this.mIsDisposed) {
            if (disposing) {
            }
            this.mIsDisposed = true;
        }
    }
    Dispose() {
        this.DisposeInternal(true);
    }
    HasAudioTrack(remoteUserId) {
        if (!this.mNetwork)
            return false;
        return this.mNetwork.HasAudioTrack(remoteUserId);
    }
    HasVideoTrack(remoteUserId) {
        if (!this.mNetwork)
            return false;
        return this.mNetwork.HasVideoTrack(remoteUserId);
    }
}


/***/ }),

/***/ "./src/awrtc/media/CallEventArgs.ts":
/*!******************************************!*\
  !*** ./src/awrtc/media/CallEventArgs.ts ***!
  \******************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   CallAcceptedEventArgs: () => (/* binding */ CallAcceptedEventArgs),
/* harmony export */   CallEndedEventArgs: () => (/* binding */ CallEndedEventArgs),
/* harmony export */   CallErrorType: () => (/* binding */ CallErrorType),
/* harmony export */   CallEventArgs: () => (/* binding */ CallEventArgs),
/* harmony export */   CallEventType: () => (/* binding */ CallEventType),
/* harmony export */   DataMessageEventArgs: () => (/* binding */ DataMessageEventArgs),
/* harmony export */   ErrorEventArgs: () => (/* binding */ ErrorEventArgs),
/* harmony export */   FrameUpdateEventArgs: () => (/* binding */ FrameUpdateEventArgs),
/* harmony export */   MediaUpdatedEventArgs: () => (/* binding */ MediaUpdatedEventArgs),
/* harmony export */   MessageEventArgs: () => (/* binding */ MessageEventArgs),
/* harmony export */   WaitForIncomingCallEventArgs: () => (/* binding */ WaitForIncomingCallEventArgs)
/* harmony export */ });
/* harmony import */ var _network_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../network/index */ "./src/awrtc/network/index.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

/// <summary>
/// Type of the event.
/// </summary>
var CallEventType;
(function (CallEventType) {
    /// <summary>
    /// Used if the event value wasn't initialized
    /// </summary>
    CallEventType[CallEventType["Invalid"] = 0] = "Invalid";
    /// <summary>
    /// The call object is successfully connected to the server waiting for another user 
    /// to connect.
    /// </summary>
    CallEventType[CallEventType["WaitForIncomingCall"] = 1] = "WaitForIncomingCall";
    /// <summary>
    /// A call was accepted
    /// </summary>
    CallEventType[CallEventType["CallAccepted"] = 2] = "CallAccepted";
    /// <summary>
    /// The call ended
    /// </summary>
    CallEventType[CallEventType["CallEnded"] = 3] = "CallEnded";
    /**
     * Backwards compatibility. Use MediaUpdate
     */
    CallEventType[CallEventType["FrameUpdate"] = 4] = "FrameUpdate";
    /// <summary>
    /// Text message arrived
    /// </summary>
    CallEventType[CallEventType["Message"] = 5] = "Message";
    /// <summary>
    /// Connection failed. Might be due to an server, network error or the address didn't exist
    /// Using ErrorEventArgs
    /// </summary>
    CallEventType[CallEventType["ConnectionFailed"] = 6] = "ConnectionFailed";
    /// <summary>
    /// Listening failed. Address might be in use or due to server/network error
    /// Using ErrorEventArgs
    /// </summary>
    CallEventType[CallEventType["ListeningFailed"] = 7] = "ListeningFailed";
    /// <summary>
    /// Event triggered after the local media was successfully configured. 
    /// If requested the call object will have access to the users camera and/or audio now and
    /// the local camera frames can be received in events. 
    /// </summary>
    CallEventType[CallEventType["ConfigurationComplete"] = 8] = "ConfigurationComplete";
    /// <summary>
    /// Configuration failed. This happens if the configuration requested features
    /// the system doesn't support e.g. no camera, camera doesn't support the requested resolution
    /// or the user didn't allow the website to access the camera/microphone in WebGL mode.
    /// </summary>
    CallEventType[CallEventType["ConfigurationFailed"] = 9] = "ConfigurationFailed";
    /// <summary>
    /// Reliable or unreliable data msg arrived
    /// </summary>
    CallEventType[CallEventType["DataMessage"] = 10] = "DataMessage";
    /**
     *
     */
    CallEventType[CallEventType["MediaUpdate"] = 20] = "MediaUpdate";
})(CallEventType || (CallEventType = {}));
class CallEventArgs {
    get Type() {
        return this.mType;
    }
    constructor(type) {
        this.mType = CallEventType.Invalid;
        this.mType = type;
    }
}
class CallAcceptedEventArgs extends CallEventArgs {
    get ConnectionId() {
        return this.mConnectionId;
    }
    constructor(connectionId) {
        super(CallEventType.CallAccepted);
        this.mConnectionId = _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID;
        this.mConnectionId = connectionId;
    }
}
class CallEndedEventArgs extends CallEventArgs {
    get ConnectionId() {
        return this.mConnectionId;
    }
    constructor(connectionId) {
        super(CallEventType.CallEnded);
        this.mConnectionId = _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID;
        this.mConnectionId = connectionId;
    }
}
var CallErrorType;
(function (CallErrorType) {
    CallErrorType[CallErrorType["Unknown"] = 0] = "Unknown";
})(CallErrorType || (CallErrorType = {}));
class ErrorEventArgs extends CallEventArgs {
    get ErrorMessage() {
        return this.mErrorMessage;
    }
    get ErrorType() {
        return this.mErrorType;
    }
    constructor(eventType, type, errorMessage) {
        super(eventType);
        this.mErrorType = CallErrorType.Unknown;
        this.mErrorType = type;
        this.mErrorMessage = errorMessage;
        if (this.mErrorMessage == null) {
            switch (eventType) {
                //use some generic error messages as the underlaying system doesn't report the errors yet.
                case CallEventType.ConnectionFailed:
                    this.mErrorMessage = "Connection failed.";
                    break;
                case CallEventType.ListeningFailed:
                    this.mErrorMessage = "Failed to allow incoming connections. Address already in use or server connection failed.";
                    break;
                default:
                    this.mErrorMessage = "Unknown error.";
                    break;
            }
        }
    }
}
class WaitForIncomingCallEventArgs extends CallEventArgs {
    get Address() {
        return this.mAddress;
    }
    constructor(address) {
        super(CallEventType.WaitForIncomingCall);
        this.mAddress = address;
    }
}
class MessageEventArgs extends CallEventArgs {
    get ConnectionId() {
        return this.mConnectionId;
    }
    get Content() {
        return this.mContent;
    }
    get Reliable() {
        return this.mReliable;
    }
    constructor(id, message, reliable) {
        super(CallEventType.Message);
        this.mConnectionId = _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID;
        this.mConnectionId = id;
        this.mContent = message;
        this.mReliable = reliable;
    }
}
class DataMessageEventArgs extends CallEventArgs {
    get ConnectionId() {
        return this.mConnectionId;
    }
    get Content() {
        return this.mContent;
    }
    get Reliable() {
        return this.mReliable;
    }
    constructor(id, message, reliable) {
        super(CallEventType.DataMessage);
        this.mConnectionId = _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID;
        this.mConnectionId = id;
        this.mContent = message;
        this.mReliable = reliable;
    }
}
/**
 * Replaces the FrameUpdateEventArgs. Instead of
 * giving access to video frames only this gives access to
 * video html tag once it is created.
 * TODO: Add audio + video tracks + flag that indicates added, updated or removed
 * after renegotiation is added.
 */
class MediaUpdatedEventArgs extends CallEventArgs {
    get ConnectionId() {
        return this.mConnectionId;
    }
    /// <summary>
    /// False if the frame is from a local camera. True if it is received from
    /// via network.
    /// </summary>
    get IsRemote() {
        return this.mConnectionId.id != _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID.id;
    }
    get VideoElement() {
        return this.mVideoElement;
    }
    constructor(conId, videoElement) {
        super(CallEventType.MediaUpdate);
        this.mConnectionId = _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID;
        this.mConnectionId = conId;
        this.mVideoElement = videoElement;
    }
}
/// <summary>
/// Will be replaced with MediaUpdatedEventArgs.
/// It doesn't make a lot of sense in HTML only
/// </summary>
class FrameUpdateEventArgs extends CallEventArgs {
    /// <summary>
    /// Raw image data. Note that the byte array contained in RawFrame will be reused
    /// for the next frames received. Only valid until the next call of ICall.Update
    /// </summary>
    get Frame() {
        return this.mFrame;
    }
    get ConnectionId() {
        return this.mConnectionId;
    }
    /// <summary>
    /// False if the frame is from a local camera. True if it is received from
    /// via network.
    /// </summary>
    get IsRemote() {
        return this.mConnectionId.id != _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID.id;
    }
    /// <summary>
    /// Constructor
    /// </summary>
    /// <param name="conId"></param>
    /// <param name="frame"></param>
    constructor(conId, frame) {
        super(CallEventType.FrameUpdate);
        this.mConnectionId = _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID;
        this.mConnectionId = conId;
        this.mFrame = frame;
    }
}


/***/ }),

/***/ "./src/awrtc/media/ICall.ts":
/*!**********************************!*\
  !*** ./src/awrtc/media/ICall.ts ***!
  \**********************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);



/***/ }),

/***/ "./src/awrtc/media/IMediaNetwork.ts":
/*!******************************************!*\
  !*** ./src/awrtc/media/IMediaNetwork.ts ***!
  \******************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   MediaConfigurationState: () => (/* binding */ MediaConfigurationState),
/* harmony export */   StreamAddedEvent: () => (/* binding */ StreamAddedEvent)
/* harmony export */ });
/* harmony import */ var _network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../network/IWebRtcNetwork */ "./src/awrtc/network/IWebRtcNetwork.ts");

var MediaConfigurationState;
(function (MediaConfigurationState) {
    MediaConfigurationState[MediaConfigurationState["Invalid"] = 0] = "Invalid";
    MediaConfigurationState[MediaConfigurationState["NoConfiguration"] = 1] = "NoConfiguration";
    MediaConfigurationState[MediaConfigurationState["InProgress"] = 2] = "InProgress";
    MediaConfigurationState[MediaConfigurationState["Successful"] = 3] = "Successful";
    MediaConfigurationState[MediaConfigurationState["Failed"] = 4] = "Failed";
})(MediaConfigurationState || (MediaConfigurationState = {}));
class StreamAddedEvent extends _network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_0__.RtcEvent {
    get Args() {
        return this.mArgs;
    }
    constructor(id, args) {
        super(_network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_0__.RtcEventType.StreamAdded, id);
        this.mArgs = args;
    }
}


/***/ }),

/***/ "./src/awrtc/media/MediaConfig.ts":
/*!****************************************!*\
  !*** ./src/awrtc/media/MediaConfig.ts ***!
  \****************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   MediaConfig: () => (/* binding */ MediaConfig)
/* harmony export */ });
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
/// <summary>
/// Configuration for the WebRtcCall class.
/// 
/// Allows to turn on / off video and audio + configure the used servers to initialize the connection and
/// avoid firewalls.
/// </summary>
class MediaConfig {
    constructor() {
        this.mAudio = false;
        this.mVideo = false;
        this.mVideoDeviceName = "";
        this.mAudioInputDevice = "";
        this.mMinWidth = -1;
        this.mMinHeight = -1;
        this.mMaxWidth = -1;
        this.mMaxHeight = -1;
        this.mIdealWidth = -1;
        this.mIdealHeight = -1;
        this.mMinFps = -1;
        this.mMaxFps = -1;
        this.mIdealFps = -1;
        this.mVideoCodecs = [];
        this.mVideoBitrateKbits = null;
        this.mVideoContentHint = null;
        this.mFrameUpdates = false;
    }
    get Audio() {
        return this.mAudio;
    }
    set Audio(value) {
        this.mAudio = value;
    }
    get Video() {
        return this.mVideo;
    }
    set Video(value) {
        this.mVideo = value;
    }
    get VideoDeviceName() {
        return this.mVideoDeviceName;
    }
    set VideoDeviceName(value) {
        this.mVideoDeviceName = value;
    }
    get AudioInputDevice() {
        return this.mAudioInputDevice;
    }
    set AudioInputDevice(value) {
        this.mAudioInputDevice = value;
    }
    get MinWidth() {
        return this.mMinWidth;
    }
    set MinWidth(value) {
        this.mMinWidth = value;
    }
    get MinHeight() {
        return this.mMinHeight;
    }
    set MinHeight(value) {
        this.mMinHeight = value;
    }
    get MaxWidth() {
        return this.mMaxWidth;
    }
    set MaxWidth(value) {
        this.mMaxWidth = value;
    }
    get MaxHeight() {
        return this.mMaxHeight;
    }
    set MaxHeight(value) {
        this.mMaxHeight = value;
    }
    get IdealWidth() {
        return this.mIdealWidth;
    }
    set IdealWidth(value) {
        this.mIdealWidth = value;
    }
    get IdealHeight() {
        return this.mIdealHeight;
    }
    set IdealHeight(value) {
        this.mIdealHeight = value;
    }
    get MinFps() {
        return this.mMinFps;
    }
    set MinFps(value) {
        this.mMinFps = value;
    }
    get MaxFps() {
        return this.mMaxFps;
    }
    set MaxFps(value) {
        this.mMaxFps = value;
    }
    get IdealFps() {
        return this.mIdealFps;
    }
    set IdealFps(value) {
        this.mIdealFps = value;
    }
    get VideoCodecs() {
        return this.mVideoCodecs;
    }
    set VideoCodecs(value) {
        this.mVideoCodecs = value;
    }
    get VideoBitrateKbits() {
        return this.mVideoBitrateKbits;
    }
    set VideoBitrateKbits(value) {
        this.mVideoBitrateKbits = value;
    }
    get VideoContentHint() {
        return this.mVideoContentHint;
    }
    set VideoContentHint(value) {
        this.mVideoContentHint = value;
    }
    /** false - frame updates aren't generated. Useful for browser mode
     *  true  - library will deliver frames as ByteArray
    */
    get FrameUpdates() {
        return this.mFrameUpdates;
    }
    set FrameUpdates(value) {
        this.mFrameUpdates = value;
    }
    clone() {
        const config_data = JSON.parse(JSON.stringify(this));
        return Object.assign(new MediaConfig(), config_data);
    }
    toString() {
        return JSON.stringify(this);
    }
}


/***/ }),

/***/ "./src/awrtc/media/RawFrame.ts":
/*!*************************************!*\
  !*** ./src/awrtc/media/RawFrame.ts ***!
  \*************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   FramePixelFormat: () => (/* binding */ FramePixelFormat),
/* harmony export */   IFrameData: () => (/* binding */ IFrameData),
/* harmony export */   LazyFrame: () => (/* binding */ LazyFrame),
/* harmony export */   RawFrame: () => (/* binding */ RawFrame)
/* harmony export */ });
/* harmony import */ var _network_Helper__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../network/Helper */ "./src/awrtc/network/Helper.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

var FramePixelFormat;
(function (FramePixelFormat) {
    FramePixelFormat[FramePixelFormat["Invalid"] = 0] = "Invalid";
    FramePixelFormat[FramePixelFormat["Format32bppargb"] = 1] = "Format32bppargb";
})(FramePixelFormat || (FramePixelFormat = {}));
//replace with interface after typescript 2.0 update (properties in interfaces aren't supported yet)
class IFrameData {
    get Format() {
        return FramePixelFormat.Format32bppargb;
    }
    get Buffer() {
        return null;
    }
    get Width() {
        return -1;
    }
    get Height() {
        return -1;
    }
    constructor() { }
    ToTexture(gl, texture) {
        return false;
    }
}
//Container for the raw bytes of the current frame + height and width.
//Format is currently fixed based on the browser getImageData format
class RawFrame extends IFrameData {
    get Buffer() {
        return this.mBuffer;
    }
    get Width() {
        return this.mWidth;
    }
    get Height() {
        return this.mHeight;
    }
    constructor(buffer, width, height) {
        super();
        this.mBuffer = null;
        this.mBuffer = buffer;
        this.mWidth = width;
        this.mHeight = height;
    }
}
/**
 * This class is suppose to increase the speed of the java script implementation.
 * Instead of creating RawFrames every Update call (because the real fps are unknown currently) it will
 * only create a lazy frame which will delay the creation of the RawFrame until the user actually tries
 * to access any data.
 * Thus if the game slows down or the user doesn't access any data the expensive copy is avoided.
 *
 * This comes with the downside of risking a change in Width / Height at the moment. In theory the video could
 * change the resolution causing the values of Width / Height to change over time before Buffer is accessed to create
 * a copy that will be save to use. This should be ok as long as the frame is used at the time it is received.
 */
class LazyFrame extends IFrameData {
    get FrameGenerator() {
        return this.mFrameGenerator;
    }
    get Buffer() {
        this.GenerateFrame();
        if (this.mRawFrame == null)
            return null;
        return this.mRawFrame.Buffer;
    }
    /**Returns the expected width of the frame.
     * Watch out this might change inbetween frames!
     *
     */
    get Width() {
        if (this.mRawFrame == null) {
            return this.mFrameGenerator.VideoElement.videoWidth;
        }
        else {
            return this.mRawFrame.Width;
        }
        /*
        this.GenerateFrame();
        if (this.mRawFrame == null)
            return -1;
        return this.mRawFrame.Width;
        */
    }
    /**Returns the expected height of the frame.
     * Watch out this might change inbetween frames!
     *
     */
    get Height() {
        if (this.mRawFrame == null) {
            return this.mFrameGenerator.VideoElement.videoHeight;
        }
        else {
            return this.mRawFrame.Height;
        }
        /*
        this.GenerateFrame();
        if (this.mRawFrame == null)
            return -1;
        return this.mRawFrame.Height;
        */
    }
    constructor(frameGenerator) {
        super();
        this.mFrameGenerator = frameGenerator;
    }
    /**Intendet for use via the Unity plugin.
     * Will copy the image directly into a texture to avoid overhead of a CPU side copy.
     *
     * The given texture should have the correct size before calling this method.
     *
     * @param gl
     * @param texture
     */
    ToTexture(gl, texture) {
        gl.bindTexture(gl.TEXTURE_2D, texture);
        /*
        const level = 0;
        const internalFormat = gl.RGBA;
        const srcFormat = gl.RGBA;
        const srcType = gl.UNSIGNED_BYTE;
        gl.texImage2D(gl.TEXTURE_2D, level, internalFormat, srcFormat, srcType, this.mFrameGenerator.VideoElement);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        */
        gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGB, gl.UNSIGNED_BYTE, this.mFrameGenerator.VideoElement);
        return true;
    }
    /*
    public ToTexture2(gl: WebGL2RenderingContext) : WebGLTexture{
        let tex = gl.createTexture()
        this.ToTexture(gl, tex)
        return;
    }
    */
    //Called before access of any frame data triggering the creation of the raw frame data
    GenerateFrame() {
        if (this.mRawFrame == null) {
            try {
                this.mRawFrame = this.mFrameGenerator.CreateFrame();
            }
            catch (exception) {
                this.mRawFrame = null;
                _network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.LogWarning("frame skipped in GenerateFrame due to exception: " + JSON.stringify(exception));
            }
        }
    }
}


/***/ }),

/***/ "./src/awrtc/media/index.ts":
/*!**********************************!*\
  !*** ./src/awrtc/media/index.ts ***!
  \**********************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   AWebRtcCall: () => (/* reexport safe */ _AWebRtcCall__WEBPACK_IMPORTED_MODULE_0__.AWebRtcCall),
/* harmony export */   CallAcceptedEventArgs: () => (/* reexport safe */ _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallAcceptedEventArgs),
/* harmony export */   CallEndedEventArgs: () => (/* reexport safe */ _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallEndedEventArgs),
/* harmony export */   CallErrorType: () => (/* reexport safe */ _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallErrorType),
/* harmony export */   CallEventArgs: () => (/* reexport safe */ _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallEventArgs),
/* harmony export */   CallEventType: () => (/* reexport safe */ _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.CallEventType),
/* harmony export */   DataMessageEventArgs: () => (/* reexport safe */ _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.DataMessageEventArgs),
/* harmony export */   ErrorEventArgs: () => (/* reexport safe */ _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.ErrorEventArgs),
/* harmony export */   FramePixelFormat: () => (/* reexport safe */ _RawFrame__WEBPACK_IMPORTED_MODULE_5__.FramePixelFormat),
/* harmony export */   FrameUpdateEventArgs: () => (/* reexport safe */ _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.FrameUpdateEventArgs),
/* harmony export */   IFrameData: () => (/* reexport safe */ _RawFrame__WEBPACK_IMPORTED_MODULE_5__.IFrameData),
/* harmony export */   LazyFrame: () => (/* reexport safe */ _RawFrame__WEBPACK_IMPORTED_MODULE_5__.LazyFrame),
/* harmony export */   MediaConfig: () => (/* reexport safe */ _MediaConfig__WEBPACK_IMPORTED_MODULE_4__.MediaConfig),
/* harmony export */   MediaConfigurationState: () => (/* reexport safe */ _IMediaNetwork__WEBPACK_IMPORTED_MODULE_3__.MediaConfigurationState),
/* harmony export */   MediaUpdatedEventArgs: () => (/* reexport safe */ _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.MediaUpdatedEventArgs),
/* harmony export */   MessageEventArgs: () => (/* reexport safe */ _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.MessageEventArgs),
/* harmony export */   RawFrame: () => (/* reexport safe */ _RawFrame__WEBPACK_IMPORTED_MODULE_5__.RawFrame),
/* harmony export */   StreamAddedEvent: () => (/* reexport safe */ _IMediaNetwork__WEBPACK_IMPORTED_MODULE_3__.StreamAddedEvent),
/* harmony export */   WaitForIncomingCallEventArgs: () => (/* reexport safe */ _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__.WaitForIncomingCallEventArgs)
/* harmony export */ });
/* harmony import */ var _AWebRtcCall__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./AWebRtcCall */ "./src/awrtc/media/AWebRtcCall.ts");
/* harmony import */ var _CallEventArgs__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./CallEventArgs */ "./src/awrtc/media/CallEventArgs.ts");
/* harmony import */ var _ICall__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./ICall */ "./src/awrtc/media/ICall.ts");
/* harmony import */ var _IMediaNetwork__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./IMediaNetwork */ "./src/awrtc/media/IMediaNetwork.ts");
/* harmony import */ var _MediaConfig__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! ./MediaConfig */ "./src/awrtc/media/MediaConfig.ts");
/* harmony import */ var _RawFrame__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! ./RawFrame */ "./src/awrtc/media/RawFrame.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/








/***/ }),

/***/ "./src/awrtc/media_browser/AudioProcessor.ts":
/*!***************************************************!*\
  !*** ./src/awrtc/media_browser/AudioProcessor.ts ***!
  \***************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   AudioProcessor: () => (/* binding */ AudioProcessor)
/* harmony export */ });
/* harmony import */ var _network_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../network/index */ "./src/awrtc/network/index.ts");
/* harmony import */ var _AutoplayResolver__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./AutoplayResolver */ "./src/awrtc/media_browser/AutoplayResolver.ts");


//Adds a UI to the page to test audio processing
const AUDIO_PROCESSING_TEST_UI = false;
/**AudioProcessor can be used to process remote audio before playback.
 * Under normal conditions it is suppose to insert itself in-between
 * the received remote AudioTrack and create a new output audio track.
 *
 */
class AudioProcessor {
    constructor() {
        this.panningControl = null;
        this.audioCtx = new AudioContext();
        const contextRef = this.audioCtx;
        this.audioCtx.onstatechange = () => {
            //watch out this triggers after disposal and this.audioCtx is null
            _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("AudioContext state changed to ", contextRef.state);
        };
        this.gainNode = new GainNode(this.audioCtx);
        this.panNode = new StereoPannerNode(this.audioCtx);
        if (AUDIO_PROCESSING_TEST_UI) {
            this.panningControl = this.CreatePanningControl();
            // changes panning based on ui
            this.panningControl.oninput = () => {
                this.panNode.pan.value = Number(this.panningControl.value);
            };
        }
        console.warn("AudioProcessor constructed in state ", this.audioCtx.state);
        if (this.audioCtx.state == "suspended") {
            _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("Audio context was created as suspended.");
            this.RequestAutoplayFix();
        }
    }
    Dispose() {
        if (this.panningControl)
            this.panningControl.remove();
        this.audioCtx.close();
        this.audioCtx = null;
    }
    CreatePanningControl() {
        // Create the input element
        const input = document.createElement('input');
        // Set attributes
        input.className = 'panning-control';
        input.type = 'range';
        input.min = '-1';
        input.max = '1';
        input.step = '0.1';
        input.value = '0';
        // Append the input to the body or any other desired parent element
        document.body.appendChild(input); // Change 'document.body' if you want to append to another element
        return input;
    }
    SetVolumePan(volume, pan) {
        this.gainNode.gain.setValueAtTime(volume, this.audioCtx.currentTime);
        this.panNode.pan.value = Number(pan);
    }
    RequestAutoplayFix() {
        _AutoplayResolver__WEBPACK_IMPORTED_MODULE_1__.AutoplayResolver.RequestAutoplayFix(this);
    }
    ResolveAutoplay() {
        if (this.audioCtx) {
            _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("Attempting to resume audio context.");
            this.audioCtx.resume().then(() => {
                _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("AudioContext resumed successfully.");
                _AutoplayResolver__WEBPACK_IMPORTED_MODULE_1__.AutoplayResolver.RemoveCompleted(this);
            }).catch((error) => {
                _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LW("AudioContext failed to resume:", error);
            });
        }
        else {
            _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("ResolveAutoplay called after disposal.");
        }
    }
    InjectPanner(instream) {
        this.source = this.audioCtx.createMediaStreamSource(instream);
        //moves audio from input stream to the panner
        this.source.connect(this.gainNode);
        this.gainNode.connect(this.panNode);
        //for testing outputs panner audio directly
        //this.panNode.connect(this.audioCtx.destination);
        if (AudioProcessor.AUDIO_PROCESSING_CHROME_WORKAROUND) {
            //for workaround we output directly and instead attach the unprocessed track to the video element
            //which we later silence.
            this.panNode.connect(this.audioCtx.destination);
            return instream;
        }
        else {
            // stream destination to convert back to a MediaStream
            const mediaStreamDestination = this.audioCtx.createMediaStreamDestination();
            this.panNode.connect(mediaStreamDestination);
            //Create a new stream that adds our video track back in as well
            const stream = new MediaStream();
            stream.addTrack(mediaStreamDestination.stream.getAudioTracks()[0]);
            if (instream.getVideoTracks().length > 0)
                stream.addTrack(instream.getVideoTracks()[0]);
            //return track should work the same as our input track but with panning applied
            return stream;
        }
    }
}
//
//
/**for this workaround we do continue using the HTMLVideoElement with original video
 * and original audio but mute the audio.
 * The actual audio output is done via an AudioContext.
 * For other browsers we forward audio to a new AudioTrack and then replace the original.
 *
 */
AudioProcessor.AUDIO_PROCESSING_CHROME_WORKAROUND = true;


/***/ }),

/***/ "./src/awrtc/media_browser/AutoplayResolver.ts":
/*!*****************************************************!*\
  !*** ./src/awrtc/media_browser/AutoplayResolver.ts ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   AutoplayResolver: () => (/* binding */ AutoplayResolver)
/* harmony export */ });
/* harmony import */ var _network_Helper__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../network/Helper */ "./src/awrtc/network/Helper.ts");

/**
 * AutoplayResolver is used to detect instances of blocked playback due to the browsers autoplay protection
 * e.g. a typical scenario when this happens is:
 * 1. You load the webpage
 * 2. The user interacts with the webpage to wait for an incoming call (without themselves sending audio)
 * 3. The user then receives an incoming call
 * 4. The system will attempt to show the received video feed. However, the browser will block the playback
 * because the user hasn't played any audio themselves yet.
 *
 * In this situation the event onautoplayblocked will trigger. The user must then interact with the UI
 * e.g. a button and during the event handler AutoplayResolver.Resolve must be called. The browser
 * detects that this interaction comes from the user and then allows all blocked elements to play.
 *
 * In case of the Unity plugin a click / touch event handler is registered with unity's WebGL canvas
 * and once the user interacts with it, it is automatically resolved.
 *
 */
class AutoplayResolver {
    static HasCompleted() {
        return AutoplayResolver.sBlockedStreams.size == 0;
    }
    //This will record a reference to the given object and call onautoplayblocked
    static RequestAutoplayFix(res) {
        AutoplayResolver.sBlockedStreams.add(res);
        //call handler to request user interaction
        if (AutoplayResolver.onautoplayblocked !== null) {
            AutoplayResolver.onautoplayblocked();
        }
    }
    //Call this from a user event handler to try play video elements / resume audio contexts
    static Resolve() {
        _network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.L("ResolveAutoplay. Trying to restart video / turn on audio after user interaction ");
        let streams = AutoplayResolver.sBlockedStreams;
        for (let v of Array.from(streams)) {
            try {
                v.ResolveAutoplay();
            }
            catch (ex) {
                _network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.LE("AutoplayResolver.Resolve failed: " + ex);
                //remove to avoid running into the error repeatedly
                this.Remove(v);
            }
        }
    }
    //call when autoplay issue was resolved. can be called multiple times without error
    static RemoveCompleted(res) {
        AutoplayResolver.sBlockedStreams.delete(res);
    }
    //Call when stream is disposed. can be called multiple times without error
    static Remove(res) {
        AutoplayResolver.sBlockedStreams.delete(res);
    }
}
/**Register an event handler here to detect when a stream gets blocked from playback
 * AutoplayResolver.Resolve() must then be called via an event handler to ensure
 * the browser detects the user allowed playback.
 */
AutoplayResolver.onautoplayblocked = () => {
    _network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.LW("Playback of a media stream was blocked. Set an event handler to "
        + "AutoplayResolver.onautoplayblocked to allow the user to start playback.");
};
AutoplayResolver.sBlockedStreams = new Set();


/***/ }),

/***/ "./src/awrtc/media_browser/BrowserMediaNetwork.ts":
/*!********************************************************!*\
  !*** ./src/awrtc/media_browser/BrowserMediaNetwork.ts ***!
  \********************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   BrowserMediaNetwork: () => (/* binding */ BrowserMediaNetwork)
/* harmony export */ });
/* harmony import */ var _network_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../network/index */ "./src/awrtc/network/index.ts");
/* harmony import */ var _media_IMediaNetwork__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../media/IMediaNetwork */ "./src/awrtc/media/IMediaNetwork.ts");
/* harmony import */ var _media_MediaConfig__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ../media/MediaConfig */ "./src/awrtc/media/MediaConfig.ts");
/* harmony import */ var _MediaPeer__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./MediaPeer */ "./src/awrtc/media_browser/MediaPeer.ts");
/* harmony import */ var _BrowserMediaStream__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! ./BrowserMediaStream */ "./src/awrtc/media_browser/BrowserMediaStream.ts");
/* harmony import */ var _DeviceApi__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! ./DeviceApi */ "./src/awrtc/media_browser/DeviceApi.ts");
/* harmony import */ var _Media__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! ./Media */ "./src/awrtc/media_browser/Media.ts");
var __awaiter = (undefined && undefined.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/







/**Avoid using this class directly whenever possible. Use BrowserWebRtcCall instead.
 * BrowserMediaNetwork might be subject to frequent changes to keep up with changes
 * in all other platforms.
 *
 * IMediaNetwork implementation for the browser. The class is mostly identical with the
 * C# version. Main goal is to have an interface that can easily be wrapped to other
 * programming languages and gives access to basic WebRTC features such as receiving
 * and sending audio and video + signaling via websockets.
 *
 * BrowserMediaNetwork can be used to stream a local audio and video track to a group of
 * multiple peers and receive remote tracks. The handling of the peers itself
 * remains the same as WebRtcNetwork.
 * Local tracks are created after calling Configure. This will request access from the
 * user. After the user allowed access GetConfigurationState will return Configured.
 * Every incoming and outgoing peer that is established after this will receive
 * the local audio and video track.
 * So far Configure can only be called once before any peers are connected.
 *
 *
 */
class BrowserMediaNetwork extends _network_index__WEBPACK_IMPORTED_MODULE_0__.WebRtcNetwork {
    constructor(config) {
        super(config);
        this.mMediaConfig = new _media_MediaConfig__WEBPACK_IMPORTED_MODULE_2__.MediaConfig();
        //keeps track of audio / video tracks based on local devices
        //will be shared with all connected peers.
        this.mLocalStream = null;
        this.mConfigurationState = _media_IMediaNetwork__WEBPACK_IMPORTED_MODULE_1__.MediaConfigurationState.Invalid;
        this.mConfigurationError = null;
        this.MediaPeer_InternalMediaStreamAdded = (peer, stream) => {
            this.EnqueueRtcEvent(new _media_IMediaNetwork__WEBPACK_IMPORTED_MODULE_1__.StreamAddedEvent(peer.ConnectionId, stream.VideoElement));
        };
        this.log = new _network_index__WEBPACK_IMPORTED_MODULE_0__.SLogger("MediaNetwork" + this.mId);
        this.mConfigurationState = _media_IMediaNetwork__WEBPACK_IMPORTED_MODULE_1__.MediaConfigurationState.NoConfiguration;
    }
    /** Returns MediaStream or fails with exception
     *
     */
    GetMedia(config) {
        return __awaiter(this, void 0, void 0, function* () {
            if (_DeviceApi__WEBPACK_IMPORTED_MODULE_5__.DeviceApi.IsUserMediaAvailable() == false) {
                let error = "Configuration failed. navigator.mediaDevices is undefined. The browser might not allow media access." +
                    "Is the page loaded via http or file URL? Some browsers only support media access via https!";
                throw error;
            }
            let stream = yield _Media__WEBPACK_IMPORTED_MODULE_6__.Media.SharedInstance.getUserMedia(config);
            return stream;
        });
    }
    /**Triggers the creation of a local audio and video track. After this
     * call the user might get a request to allow access to the requested
     * devices.
     *
     * @param config Detail configuration for audio/video devices.
     */
    Configure(config) {
        if (this.mIsDisposed) {
            this.OnConfigurationFailed("Network has been disposed.");
            return;
        }
        this.mMediaConfig = config;
        this.mConfigurationError = null;
        this.mConfigurationState = _media_IMediaNetwork__WEBPACK_IMPORTED_MODULE_1__.MediaConfigurationState.InProgress;
        if (this.mLocalStream !== null) {
            this.mLocalStream.Dispose();
            this.mLocalStream = null;
        }
        if (config.Audio || config.Video) {
            _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("calling GetUserMedia. Media config: " + JSON.stringify(config));
            setTimeout(() => __awaiter(this, void 0, void 0, function* () {
                try {
                    let stream = yield this.GetMedia(config);
                    if (this.mIsDisposed)
                        return;
                    this.mLocalStream = new _BrowserMediaStream__WEBPACK_IMPORTED_MODULE_4__.BrowserMediaStream(true, this.log);
                    stream.getTracks().forEach((x) => { this.mLocalStream.UpdateTrack(x); });
                    //ensure local audio is not replayed
                    this.mLocalStream.SetMute(true);
                    this.OnLocalStreamUpdated();
                    this.OnConfigurationSuccess();
                }
                catch (error) {
                    this.OnConfigurationFailed("Accessing media failed due to exception: " + error);
                }
            }), 0);
        }
        else {
            this.OnLocalStreamUpdated();
            this.OnConfigurationSuccess();
        }
    }
    OnLocalStreamUpdated() {
        //update all peers on the change
        Object.values(this.IdToConnection).forEach(x => x.SetLocalStream(this.mLocalStream, this.mMediaConfig));
        //set event handler to trigger once all meta data & video element is available
        if (this.mLocalStream != null) {
            this.mLocalStream.InternalStreamAdded = (stream) => {
                this.EnqueueRtcEvent(new _media_IMediaNetwork__WEBPACK_IMPORTED_MODULE_1__.StreamAddedEvent(_network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, this.mLocalStream.VideoElement));
            };
        }
    }
    /**Call this every time a new frame is shown to the user in realtime
     * applications.
     *
     */
    Update() {
        super.Update();
        if (this.mLocalStream != null)
            this.mLocalStream.Update();
    }
    /**
     * Call this every frame after interacting with this instance.
     *
     * This call might flush buffered messages in the future and clear
     * events that the user didn't process to avoid buffer overflows.
     *
     */
    Flush() {
        super.Flush();
    }
    /**Poll this after Configure is called to get the result.
     * Won't change after state is Configured or Failed.
     *
     */
    GetConfigurationState() {
        return this.mConfigurationState;
    }
    /**Returns the error message if the configure process failed.
     * This usally either happens because the user refused access
     * or no device fulfills the configuration given
     * (e.g. device doesn't support the given resolution)
     *
     */
    GetConfigurationError() {
        return this.mConfigurationError;
    }
    /**Resets the configuration state to allow multiple attempts
     * to call Configure.
     *
     */
    ResetConfiguration() {
        this.mConfigurationState = _media_IMediaNetwork__WEBPACK_IMPORTED_MODULE_1__.MediaConfigurationState.NoConfiguration;
        this.mMediaConfig = new _media_MediaConfig__WEBPACK_IMPORTED_MODULE_2__.MediaConfig();
        this.mConfigurationError = null;
    }
    OnConfigurationSuccess() {
        this.mConfigurationState = _media_IMediaNetwork__WEBPACK_IMPORTED_MODULE_1__.MediaConfigurationState.Successful;
    }
    OnConfigurationFailed(error) {
        this.mConfigurationError = error;
        this.mConfigurationState = _media_IMediaNetwork__WEBPACK_IMPORTED_MODULE_1__.MediaConfigurationState.Failed;
    }
    /**Allows to peek at the current frame.
     * Added to allow the emscripten C / C# side to allocate memory before
     * actually getting the frame.
     *
     * @param id
     */
    PeekFrame(id) {
        if (id == null)
            return;
        if (id.id == _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID.id) {
            if (this.mLocalStream != null) {
                return this.mLocalStream.PeekFrame();
            }
        }
        else {
            let peer = this.IdToConnection[id.id];
            if (peer != null) {
                return peer.PeekFrame();
            }
            //TODO: iterate over media peers and do the same as above
        }
        return null;
    }
    TryGetFrame(id) {
        if (id == null)
            return;
        if (id.id == _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID.id) {
            if (this.mLocalStream != null) {
                return this.mLocalStream.TryGetFrame();
            }
        }
        else {
            let peer = this.IdToConnection[id.id];
            if (peer != null) {
                return peer.TryGetRemoteFrame();
            }
            //TODO: iterate over media peers and do the same as above
        }
        return null;
    }
    /**
     * Remote audio control for each peer.
     *
     * @param volume 0 - mute and 1 - max volume
     * @param id peer id
     */
    SetVolume(volume, id) {
        _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("SetVolume called. Volume: " + volume + " id: " + id.id);
        let peer = this.IdToConnection[id.id];
        if (peer != null) {
            return peer.SetVolume(volume);
        }
    }
    SetVolumePan(volume, pan, id) {
        _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("SetVolumePan called. Volume: " + volume + "pan: " + pan + " id: " + id.id);
        let peer = this.IdToConnection[id.id];
        if (peer != null) {
            return peer.SetVolumePan(volume, pan);
        }
    }
    /** Allows to check if a specific peer has a remote
     * audio track attached.
     *
     * @param id
     */
    HasAudioTrack(id) {
        let peer = this.IdToConnection[id.id];
        if (peer != null) {
            return peer.HasAudioTrack();
        }
        return false;
    }
    /** Allows to check if a specific peer has a remote
     * video track attached.
     *
     * @param id
     */
    HasVideoTrack(id) {
        let peer = this.IdToConnection[id.id];
        if (peer != null) {
            return peer.HasVideoTrack();
        }
        return false;
    }
    /**Returns true if no local audio available or it is muted.
     * False if audio is available (could still not work due to 0 volume, hardware
     * volume control or a dummy audio input device is being used)
     */
    IsMute() {
        if (this.mLocalStream != null && this.mLocalStream.Stream != null) {
            var stream = this.mLocalStream.Stream;
            var tracks = stream.getAudioTracks();
            if (tracks.length > 0) {
                if (tracks[0].enabled)
                    return false;
            }
        }
        return true;
    }
    /**Sets the local audio device to mute / unmute it.
     *
     * @param value
     */
    SetMute(value) {
        if (this.mLocalStream != null && this.mLocalStream.Stream != null) {
            var stream = this.mLocalStream.Stream;
            var tracks = stream.getAudioTracks();
            if (tracks.length > 0) {
                tracks[0].enabled = !value;
            }
        }
    }
    CreatePeer(peerId) {
        const peerConfig = new _network_index__WEBPACK_IMPORTED_MODULE_0__.PeerConfig(this.mNetConfig);
        let peer = new _MediaPeer__WEBPACK_IMPORTED_MODULE_3__.MediaPeer(peerId, peerConfig, this.mMediaConfig, this.log);
        peer.InternalStreamAdded = this.MediaPeer_InternalMediaStreamAdded;
        if (this.mLocalStream != null)
            setTimeout(() => __awaiter(this, void 0, void 0, function* () {
                //SLog.L("Updating local stream");
                yield peer.SetLocalStream(this.mLocalStream, this.mMediaConfig);
                this.log.L("Set local stream to new peer");
            }));
        return peer;
    }
    DisposeInternal() {
        super.DisposeInternal();
        this.DisposeLocalStream();
    }
    DisposeLocalStream() {
        if (this.mLocalStream != null) {
            this.mLocalStream.Dispose();
            this.mLocalStream = null;
        }
    }
}


/***/ }),

/***/ "./src/awrtc/media_browser/BrowserMediaStream.ts":
/*!*******************************************************!*\
  !*** ./src/awrtc/media_browser/BrowserMediaStream.ts ***!
  \*******************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   BrowserMediaStream: () => (/* binding */ BrowserMediaStream)
/* harmony export */ });
/* harmony import */ var _media_RawFrame__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../media/RawFrame */ "./src/awrtc/media/RawFrame.ts");
/* harmony import */ var _network_Helper__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../network/Helper */ "./src/awrtc/network/Helper.ts");
/* harmony import */ var _AudioProcessor__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./AudioProcessor */ "./src/awrtc/media_browser/AudioProcessor.ts");
/* harmony import */ var _AutoplayResolver__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./AutoplayResolver */ "./src/awrtc/media_browser/AutoplayResolver.ts");
/*
Copyright (c) 2024, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/




/**
 * true - replay audio via AudioProcessor via AudioContext to use panning
 * false - replay via HTMLVideoElement ( pre V0.9864)
 *
 */
const AUDIO_PROCESSING = true;
/**
 * Mostly used for debugging at the moment. Browser API doesn't seem to have a standard way to
 * determine if a frame was updated. This class currently uses several different methods based
 * on availability
 *
 */
var FrameEventMethod;
(function (FrameEventMethod) {
    /**We use a set default framerate. FPS is unknown and we can't recognize if a frame was updated.
     * Used for remote video tracks on firefox as the "framerate" property will not be set.
     */
    FrameEventMethod["DEFAULT_FALLBACK"] = "DEFAULT_FALLBACK";
    /**
     * Using the tracks meta data to decide the framerate. We might drop frames or deliver them twice
     * because we can't tell when exactly they are updated.
     * Some video devices also claim 30 FPS but generate less causing us to waste performance copying the same image
     * multiple times
     *
     * This system works with local video in firefox
     */
    FrameEventMethod["TRACK"] = "TRACK";
    /**
     *  uses frame numbers returned by the browser. This works for webkit based browsers only so far.
     *  Firefox is either missing the needed properties or they return always 0
     */
    FrameEventMethod["EXACT"] = "EXACT";
})(FrameEventMethod || (FrameEventMethod = {}));
/**Internal use only.
 * Bundles all functionality related to MediaStream, Tracks and video processing.
 * It creates two HTML elements: Video and Canvas to interact with the video stream
 * and convert the visible frame data to Uint8Array for compatibility with the
 * unity plugin and all other platforms.
 *
 */
class BrowserMediaStream {
    get Stream() {
        return this.mStream;
    }
    get VideoElement() {
        return this.mVideoElement;
    }
    /**If previously play triggered an error the AutoplayResolver can
     * attempt to play again after the user interacted with the webpage.
     *
     * Mostly used on iOS Safari. This feature is error prone and can only
     * be manually tested keep log verbose!
     */
    ResolveAutoplay() {
        //call play again if needed
        if (this.mVideoElement) {
            if (this.mVideoElement.paused) {
                this.log.L("Attempting to play HTMLVideoElement");
                this.mVideoElement.play().then(() => {
                    _AutoplayResolver__WEBPACK_IMPORTED_MODULE_3__.AutoplayResolver.RemoveCompleted(this);
                    this.log.L("Playing video element was successful");
                }).catch((error) => {
                    //if this happens we have no way of recovering for now
                    this.log.LE("Playing video element failed with error: " + error);
                });
            }
            else {
                this.log.L("ResolveAutoplay called but HTMLVideoElement was already playing");
            }
        }
        else {
            this.log.L("ResolveAutoplay after disposal");
        }
    }
    constructor(isLocal, baseLogger = new _network_Helper__WEBPACK_IMPORTED_MODULE_1__.SLogger("")) {
        this.mCurrentFrame = null;
        this.mInstanceId = 0;
        this.mIdentity = "Stream";
        this.mCanvasElement = null;
        this.mIsActive = false;
        this.mAudioProcessor = null;
        this.mMsPerFrame = 1.0 / BrowserMediaStream.DEFAULT_FRAMERATE * 1000;
        this.mFrameEventMethod = FrameEventMethod.DEFAULT_FALLBACK;
        //used to buffer last volume level as part of the
        //autoplat workaround that will mute the audio until it gets the ok from the user
        this.mDefaultVolume = 0.5;
        //Time the last frame was generated
        this.mLastFrameTime = 0;
        this.mNextFrameTime = 0;
        /** Number of the last frame (not yet supported in all browsers)
         * if it remains at <= 0 then we just generate frames based on
         * the timer above
         */
        this.mLastFrameNumber = 0;
        this.mHasVideo = false;
        this.InternalStreamAdded = null;
        this.mStream = new MediaStream();
        this.mLocal = isLocal;
        this.mInstanceId = BrowserMediaStream.sNextInstanceId;
        BrowserMediaStream.sNextInstanceId++;
        this.log = baseLogger.CreateSub(this.mIdentity + this.mInstanceId);
        this.mMsPerFrame = 1.0 / BrowserMediaStream.DEFAULT_FRAMERATE * 1000;
        this.mFrameEventMethod = FrameEventMethod.DEFAULT_FALLBACK;
        this.SetupElements();
    }
    /**Adds or replaces a track with a new track of the same kind
     *
     * @param new_track Track to add / replace the current track with
     */
    UpdateTrack(new_track) {
        //make sure we only have 1 track each
        this.mStream.getTracks().forEach((track) => {
            if (track.kind == new_track.kind) {
                this.log.L("Replacing track of type " + track.kind);
                this.mStream.removeTrack(track);
            }
        });
        //this.log.L("Adding new track of type " + new_track.kind + " muted?" + new_track.muted + " enabled?" + new_track.enabled);
        this.mStream.addTrack(new_track);
        this.UpdateAudioProcessing();
        this.UpdateVideoProcessing();
    }
    /**Removes a track from the Stream.
     *
     * @param track track to remove
     */
    RemoveTrack(track) {
        return;
        this.log.L("Removing track of type " + track.kind + " muted?" + track.muted + " enabled?" + track.enabled);
        this.mStream.removeTrack(track);
    }
    /**
     * This resets the srcObject property of the VideoElement.
     * Used to force a clean reload after removing a track
     * (Chrome single negotiation workaround)
     */
    ResetObject() {
        if (AUDIO_PROCESSING && this.mStream.getAudioTracks().length > 0 && this.mLocal == false) {
            if (this.mAudioProcessor == null)
                this.mAudioProcessor = new _AudioProcessor__WEBPACK_IMPORTED_MODULE_2__.AudioProcessor();
            this.mVideoElement.srcObject = this.mAudioProcessor.InjectPanner(this.mStream);
            if (_AudioProcessor__WEBPACK_IMPORTED_MODULE_2__.AudioProcessor.AUDIO_PROCESSING_CHROME_WORKAROUND) {
                //If audio processing is active and chrome is used we play audio
                //via the audio context in AudioProcessor and keep the original audio track connected to the HTMLVideoElement
                this.mVideoElement.muted = true;
            }
        }
        else {
            this.mVideoElement.srcObject = this.mStream;
        }
    }
    /**Tries to determine a good video framerate.
     * 1. Either the browser returns exact frame numbers via webkitDecodedFrameCount
     * 2. or it might have a property frameRate (likely only available for local video)
     * 3. if all fails we use DEFAULT_FRAMERATE as a fallback
     */
    DetermineFrameEventMethod() {
        if (this.mVideoElement) {
            if (this.mStream.getVideoTracks().length > 0) {
                let vtrack = this.mStream.getVideoTracks()[0];
                let settings = vtrack.getSettings();
                let fps = settings.frameRate;
                if (fps) {
                    if (BrowserMediaStream.VERBOSE) {
                        this.log.LV("Track FPS: " + fps);
                    }
                    this.mMsPerFrame = 1.0 / fps * 1000;
                    this.mFrameEventMethod = FrameEventMethod.TRACK;
                }
            }
            //try to get the video fps via the track
            //fails on firefox if the track comes from a remote source
            if (this.GetFrameNumber() != -1) {
                if (BrowserMediaStream.VERBOSE) {
                    this.log.LV("Get frame available.");
                }
                //browser returns exact frame information
                this.mFrameEventMethod = FrameEventMethod.EXACT;
            }
            //failed to determine any frame rate. This happens on firefox with
            //remote tracks
            if (this.mFrameEventMethod === FrameEventMethod.DEFAULT_FALLBACK) {
                //firefox and co won't tell us the FPS for remote stream
                this.log.LW("Framerate unknown for stream " + this.mInstanceId + ". Using default framerate of " + BrowserMediaStream.DEFAULT_FRAMERATE);
            }
        }
    }
    UpdateAudioProcessing() {
        this.ResetObject();
    }
    /**
     * Called when meta data first becomes available or when tracks are changed.
     * Might trigger several times in the row e.g. first a track might be available but width/height unknown
     * until onloadmetadata triggers and calls it again.
     *
     * If video is available this checks the current framerate and creates a canvas
     * used to process frames.
     * If video is unavailable it destroys the canvas.
     *
     *
     */
    UpdateVideoProcessing() {
        if (!this.mVideoElement)
            return;
        if (this.mStream.getVideoTracks().length == 0) {
            this.mHasVideo = false;
            this.DestroyCanvas();
            return;
        }
        this.mHasVideo = true;
        this.DetermineFrameEventMethod();
        //ensure we have a canvas if not created during a previous UpdateVideoProcessing yet
        if (!this.mCanvasElement)
            this.SetupCanvas();
    }
    TriggerAutoplayBlockled() {
        _AutoplayResolver__WEBPACK_IMPORTED_MODULE_3__.AutoplayResolver.RequestAutoplayFix(this);
    }
    TryPlay() {
        let playPromise = this.mVideoElement.play();
        this.mDefaultVolume = this.mVideoElement.volume;
        playPromise.then(function () {
            //all good
        }).catch((error) => {
            //browser blocked replay. print error & setup auto play workaround
            this.log.LW("Media playback failed. This might be caused by the browser blocking autoplay.");
            console.error(error);
            this.TriggerAutoplayBlockled();
        });
    }
    SetupElements() {
        let source = "remote";
        if (this.mLocal)
            source = "local";
        this.mVideoElement = this.SetupVideoElement();
        //TOOD: investigate bug here
        //In some cases onloadedmetadata is never called. This might happen due to a 
        //bug in firefox or might be related to a device / driver error
        //So far it only happens randomly (maybe 1 in 10 tries) on a single test device and only
        //with 720p. (video device "BisonCam, NB Pro" on MSI laptop)
        this.log.L("video element created for " + source + " video tracks: " + this.mStream.getVideoTracks().length + " audio:" + this.mStream.getAudioTracks().length);
        this.mVideoElement.onloadedmetadata = (e) => {
            //we might have shutdown everything by now already
            if (this.mVideoElement == null) {
                this.log.L("Stream destroyed by the time onloadedmetadata triggered. Skip event.");
                return;
            }
            this.TryPlay();
            if (this.InternalStreamAdded != null)
                this.InternalStreamAdded(this);
            this.UpdateVideoProcessing();
            let video_log = "onloadedmetadata: " + source + " audio: " + (this.mStream.getAudioTracks().length > 0) + " Resolution: " + this.mVideoElement.videoWidth + "x" + this.mVideoElement.videoHeight
                + " fps method: " + this.mFrameEventMethod + " " + Math.round(1000 / (this.mMsPerFrame));
            this.log.L(video_log);
            this.mIsActive = true;
        };
        //set the src value and trigger onloadedmetadata above
        try {
            this.ResetObject();
        }
        catch (error) {
            //old way of doing it. won't work anymore in firefox and possibly other browsers
            this.mVideoElement.src = window.URL.createObjectURL(this.mStream);
        }
    }
    /** Returns the current frame number.
     *  Treat a return value of 0 or smaller as unknown.
     * (Browsers might have the property but
     * always return 0)
     */
    GetFrameNumber() {
        let frameNumber;
        if (this.mVideoElement) {
            if (this.mVideoElement.webkitDecodedFrameCount) {
                frameNumber = this.mVideoElement.webkitDecodedFrameCount;
            }
            /*
            None of these work and future versions might return numbers that are only
            updated once a second or so. For now it is best to ignore these.

            TODO: update 2023 these do still not work with remote streams and just return 0 (mozPainted only works if the element is visible)
            this.mVideoElement.currentTime also won't work because it is unrelated to framerate
            else if((this.mVideoElement as any).mozParsedFrames)
            {
                frameNumber = (this.mVideoElement as any).mozParsedFrames;
            }else if((this.mVideoElement as any).mozDecodedFrames)
            {
                frameNumber = (this.mVideoElement as any).mozDecodedFrames;
            }else if((this.mVideoElement as any).decodedFrameCount)
            {
                frameNumber = (this.mVideoElement as any).decodedFrameCount;
            }
            */
            else {
                frameNumber = -1;
            }
        }
        else {
            frameNumber = -1;
        }
        return frameNumber;
    }
    TryGetFrame() {
        //make sure we get the newest frame
        //this.EnsureLatestFrame();
        //remove the buffered frame if any
        var result = this.mCurrentFrame;
        this.mCurrentFrame = null;
        return result;
    }
    SetMute(mute) {
        if (this.mVideoElement) {
            //Usually, we use mute only for local video. If used for another reason on remote video
            //while audio processing is active it will fail due to chrome workaround (audio isn't replayed
            //via video element). Shouldn't ever happen under normal usage.
            if (this.mAudioProcessor && _AudioProcessor__WEBPACK_IMPORTED_MODULE_2__.AudioProcessor.AUDIO_PROCESSING_CHROME_WORKAROUND) {
                this.log.LW("SetMute ignored due to audio processing");
                return;
            }
            this.mVideoElement.muted = mute;
        }
    }
    PeekFrame() {
        //this.EnsureLatestFrame();
        return this.mCurrentFrame;
    }
    /** Ensures we have the latest frame ready
     * for the next PeekFrame / TryGetFrame calls
     */
    EnsureLatestFrame() {
        if (this.HasNewerFrame()) {
            this.GenerateFrame();
            return true;
        }
        return false;
    }
    /** checks if the html tag has a newer frame available
     * (or if 1/30th of a second passed since last frame if
     * this info isn't available)
     */
    HasNewerFrame() {
        if (this.mIsActive
            && this.mHasVideo
            && this.mCanvasElement != null
            && this.mVideoElement.videoWidth > 0 //these are 0 for a while when a new video track is added
            && this.mVideoElement.videoHeight > 0) {
            if (this.mLastFrameNumber > 0) {
                this.mFrameEventMethod = FrameEventMethod.EXACT;
                //we are getting frame numbers. use those to 
                //check if we have a new one
                if (this.GetFrameNumber() > this.mLastFrameNumber) {
                    return true;
                }
            }
            else {
                //many browsers do not share the frame info
                let now = new Date().getTime();
                if (this.mNextFrameTime <= now) {
                    {
                        return true;
                    }
                }
            }
        }
        return false;
    }
    Update() {
        this.EnsureLatestFrame();
    }
    DestroyCanvas() {
        //for testing we might add it to the DOM. make sure we remove it again.
        if (this.mCanvasElement != null && this.mCanvasElement.parentElement != null) {
            this.mCanvasElement.parentElement.removeChild(this.mCanvasElement);
        }
        this.mCanvasElement = null;
    }
    DestroyVideoElement() {
        //for testing we might add it to the DOM. make sure we remove it again.
        if (this.mVideoElement != null && this.mVideoElement.parentElement != null) {
            this.mVideoElement.parentElement.removeChild(this.mVideoElement);
        }
        this.mVideoElement = null;
    }
    Dispose() {
        this.log.L("Disposing stream " + this.mInstanceId);
        this.mIsActive = false;
        _AutoplayResolver__WEBPACK_IMPORTED_MODULE_3__.AutoplayResolver.Remove(this);
        this.DestroyCanvas();
        if (this.mAudioProcessor != null)
            this.mAudioProcessor.Dispose();
        this.DestroyVideoElement();
        this.mStream.getTracks().forEach((x) => { x.stop(); });
        this.mStream = null;
    }
    CreateFrame() {
        if (this.mVideoElement.videoWidth <= 0 || this.mVideoElement.videoHeight <= 0) {
            //This happens right after we attached a new track to an existing stream. 
            //onloadedmetadata is not triggered in this situation so we have to
            //keep polling until we can finally get a frame
            //TODO: Update HasNewerFrame to prevent this from being triggered
            //if there is no better workaround that actually allows us to detect
            //the metadata when ready
            this.log.LW("Frame skipped. videoWidth / videoHeight were 0.");
            return null;
        }
        this.mCanvasElement.width = this.mVideoElement.videoWidth;
        this.mCanvasElement.height = this.mVideoElement.videoHeight;
        let ctx = this.mCanvasElement.getContext("2d");
        /*
        var fillBackgroundFirst = true;
        if (fillBackgroundFirst) {
            ctx.clearRect(0, 0, this.mCanvasElement.width, this.mCanvasElement.height);
        }
        */
        ctx.drawImage(this.mVideoElement, 0, 0);
        try {
            //risk of security exception in firefox
            let imgData = ctx.getImageData(0, 0, this.mCanvasElement.width, this.mCanvasElement.height);
            var imgRawData = imgData.data;
            var array = new Uint8Array(imgRawData.buffer);
            return new _media_RawFrame__WEBPACK_IMPORTED_MODULE_0__.RawFrame(array, this.mCanvasElement.width, this.mCanvasElement.height);
        }
        catch (exception) {
            //show white frame for now
            var array = new Uint8Array(this.mCanvasElement.width * this.mCanvasElement.height * 4);
            array.fill(255, 0, array.length - 1);
            let res = new _media_RawFrame__WEBPACK_IMPORTED_MODULE_0__.RawFrame(array, this.mCanvasElement.width, this.mCanvasElement.height);
            //attempted workaround for firefox bug / suspected cause: 
            // * root cause seems to be an internal origin-clean flag within the canvas. If set to false reading from the
            //   canvas triggers a security exceptions. This is usually used if the canvas contains data that isn't 
            //   suppose to be accessible e.g. a picture from another domain
            // * while moving the image to the canvas the origin-clean flag seems to be set to false but only 
            //   during the first few frames. (maybe a race condition within firefox? A higher CPU workload increases the risk)
            // * the canvas will work and look just fine but calling getImageData isn't allowed anymore
            // * After a few frames the video is back to normal but the canvas will still have the flag set to false
            // 
            //Solution:
            // * Recreate the canvas if the exception is triggered. During the next few frames firefox should get its flag right
            //   and then stop causing the error. It might recreate the canvas multiple times until it finally works as we
            //   can't detect if the video element will trigger the issue until we tried to access the data
            this.log.LW("Firefox workaround: Refused access to the remote video buffer. Retrying next frame...");
            this.DestroyCanvas();
            this.SetupCanvas();
            return res;
        }
    }
    //Old buffed frame was replaced with a wrapepr that avoids buffering internally
    //Only point of generate frame is now to ensure a consistent framerate
    GenerateFrame() {
        this.mLastFrameNumber = this.GetFrameNumber();
        let now = new Date().getTime();
        //js timing is very inaccurate. reduce time until next frame if we are
        //late with this one.
        let diff = now - this.mNextFrameTime;
        let delta = (this.mMsPerFrame - diff);
        delta = Math.min(this.mMsPerFrame, Math.max(1, delta));
        this.mLastFrameTime = now;
        this.mNextFrameTime = now + delta;
        //this.log.LV("last frame , new frame", this.mLastFrameTime, this.mNextFrameTime, delta);
        this.mCurrentFrame = new _media_RawFrame__WEBPACK_IMPORTED_MODULE_0__.LazyFrame(this);
    }
    SetupVideoElement() {
        var videoElement = document.createElement("video");
        //width/doesn't seem to be important
        videoElement.width = 320;
        videoElement.height = 240;
        if (this.mLocal == false)
            videoElement.controls = true;
        //needed for Safari on iPhone
        videoElement.setAttribute("playsinline", "");
        videoElement.id = "awrtc_mediastream_video_" + this.mInstanceId;
        if (BrowserMediaStream.DEBUG_SHOW_ELEMENTS)
            document.body.appendChild(videoElement);
        return videoElement;
    }
    SetupCanvas() {
        if (this.mVideoElement == null) {
            this.log.LE("SetupCanvas was called without HTMLVideoElement");
            return;
        }
        var canvas = document.createElement("canvas");
        //removed. Video width / height might be 0 at the start until metadata is available
        //console.log("Creating canvas with width " + this.mVideoElement.videoWidth);
        //canvas.width = this.mVideoElement.videoWidth;
        //canvas.height = this.mVideoElement.videoHeight;
        canvas.id = "awrtc_mediastream_canvas_" + this.mInstanceId;
        if (BrowserMediaStream.DEBUG_SHOW_ELEMENTS)
            document.body.appendChild(canvas);
        this.mCanvasElement = canvas;
    }
    SetVolume(volume) {
        if (this.mVideoElement == null) {
            return;
        }
        if (volume < 0)
            volume = 0;
        if (volume > 1)
            volume = 1;
        if (this.mAudioProcessor != null) {
            this.mAudioProcessor.SetVolumePan(volume, 0);
        }
        else {
            this.mVideoElement.volume = volume;
        }
    }
    SetVolumePan(volume, pan) {
        if (this.mVideoElement == null) {
            return;
        }
        if (volume < 0)
            volume = 0;
        if (volume > 1)
            volume = 1;
        if (this.mAudioProcessor != null) {
            this.mAudioProcessor.SetVolumePan(volume, pan);
        }
        else {
            this.log.LW("setVolumePan ignored the pan value. Audio processing is not available.");
            this.mVideoElement.volume = volume;
        }
    }
    /**
     * @returns true if a audio track is attached to the stream, false if not
     */
    HasAudioTrack() {
        if (this.mStream != null && this.mStream.getAudioTracks() != null
            && this.mStream.getAudioTracks().length > 0) {
            return true;
        }
        return false;
    }
    /**
     * @returns true if a video track is attached to the stream, false it not
     */
    HasVideoTrack() {
        if (this.mStream != null && this.mStream.getVideoTracks() != null
            && this.mStream.getVideoTracks().length > 0) {
            return true;
        }
        return false;
    }
    /**
     *
     * @returns the used audio track or null if none
     */
    GetAudioTrack() {
        if (this.mStream.getAudioTracks().length > 0)
            return this.mStream.getAudioTracks()[0];
        return null;
    }
    /**
     * @returns the used video track or null if none
     */
    GetVideoTrack() {
        if (this.mStream.getVideoTracks().length > 0)
            return this.mStream.getVideoTracks()[0];
        return null;
    }
}
//no double buffering in java script as it forces us to create a new frame each time
//for debugging. Will attach the HTMLVideoElement used to play the local and remote
//video streams to the document.
BrowserMediaStream.DEBUG_SHOW_ELEMENTS = false;
//Gives each FrameBuffer and its HTMLVideoElement a fixed id for debugging purposes.
BrowserMediaStream.sNextInstanceId = 1;
BrowserMediaStream.VERBOSE = false;
//Framerate used as a workaround if
//the actual framerate is unknown due to browser restrictions
BrowserMediaStream.DEFAULT_FRAMERATE = 30;


/***/ }),

/***/ "./src/awrtc/media_browser/BrowserWebRtcCall.ts":
/*!******************************************************!*\
  !*** ./src/awrtc/media_browser/BrowserWebRtcCall.ts ***!
  \******************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   BrowserWebRtcCall: () => (/* binding */ BrowserWebRtcCall)
/* harmony export */ });
/* harmony import */ var _media_AWebRtcCall__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../media/AWebRtcCall */ "./src/awrtc/media/AWebRtcCall.ts");
/* harmony import */ var _BrowserMediaNetwork__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./BrowserMediaNetwork */ "./src/awrtc/media_browser/BrowserMediaNetwork.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/


/**Browser version of the C# version of WebRtcCall.
 *
 * See ICall interface for detailed documentation.
 * BrowserWebRtcCall mainly exists to allow other versions
 * in the future that might build on a different IMediaNetwork
 * interface (Maybe something running inside Webassembly?).
 */
class BrowserWebRtcCall extends _media_AWebRtcCall__WEBPACK_IMPORTED_MODULE_0__.AWebRtcCall {
    constructor(config) {
        super(config);
        this.Initialize(this.CreateNetwork());
    }
    CreateNetwork() {
        return new _BrowserMediaNetwork__WEBPACK_IMPORTED_MODULE_1__.BrowserMediaNetwork(this.mNetworkConfig);
    }
    DisposeInternal(disposing) {
        super.DisposeInternal(disposing);
        if (disposing) {
            if (this.mNetwork != null)
                this.mNetwork.Dispose();
            this.mNetwork = null;
        }
    }
}


/***/ }),

/***/ "./src/awrtc/media_browser/DeviceApi.ts":
/*!**********************************************!*\
  !*** ./src/awrtc/media_browser/DeviceApi.ts ***!
  \**********************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   DeviceApi: () => (/* binding */ DeviceApi),
/* harmony export */   MediaDevice: () => (/* binding */ MediaDevice)
/* harmony export */ });
/* harmony import */ var _network_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../network/index */ "./src/awrtc/network/index.ts");
var __awaiter = (undefined && undefined.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

class MediaDevice {
    constructor() {
        //unique id
        this.Id = null;
        //user readable name. Either defaultLabel or an exact name if available
        this.Name = null;
    }
}
class DeviceInfoInternal extends MediaDevice {
    constructor() {
        super(...arguments);
        //this is a guessed label for the device. e.g. videoinput 1 if full device information
        //wasn't available yet. This will be kept the same even when MediaDevice.Name is updated
        //to allow the UI to reuse old values
        this.fallbackLabel = null;
        //True if the label is a generic name. False if it contains an exact device name
        this.isLabelGuessed = true;
    }
}
/**This keeps the device information we collected and
 * allows addressing devices with just a string (not just an id)
 * to keep the API consistant with the other platforms that lack
 * access to unique ID's.
 */
class DeviceDb {
    constructor() {
        this.mDeviceInfo = {};
        this.mDeviceCounter = 1;
    }
    get DeviceDict() {
        return this.mDeviceInfo;
    }
    //Returns a string list of all device labels / names. 
    //This is for the compatibility to old platforms (will be removed one day)
    GetDeviceLabels() {
        const labels = Object.values(this.mDeviceInfo).map((x) => x.Name);
        return labels;
    }
    //Returns a list of all devices with id and label. Replaces GetDeviceLabels
    GetDeviceList() {
        const devs = Object.values(this.mDeviceInfo);
        return devs;
    }
    //Updates the internal device list. Keeps track of id, label and guessed labels in case the
    //actual label is not known yet
    UpdateDeviceList(devices) {
        let newDeviceInfo = {};
        for (let info of devices) {
            let newInfo = new DeviceInfoInternal();
            newInfo.Id = info.deviceId;
            newInfo.kind = info.kind;
            let oldKnownInfo = null;
            //if we already know a device with that id get the info
            if (newInfo.Id in this.mDeviceInfo) {
                oldKnownInfo = this.mDeviceInfo[newInfo.Id];
            }
            //reuse the old defaultLabel or create a new one
            if (oldKnownInfo != null) {
                newInfo.fallbackLabel = oldKnownInfo.fallbackLabel;
            }
            else {
                newInfo.fallbackLabel = info.kind + " " + this.mDeviceCounter;
                this.mDeviceCounter++;
            }
            //check if we know a proper label or got one this update
            if (oldKnownInfo != null && oldKnownInfo.isLabelGuessed == false) {
                //we already have the label -> reuse it
                newInfo.Name = oldKnownInfo.Name;
                newInfo.isLabelGuessed = false;
            }
            else if (info.label) {
                //we got a new label -> use this instead of the old one
                newInfo.Name = info.label;
                newInfo.isLabelGuessed = false;
            }
            else {
                //no known label -> use the fallback label we set earlier
                newInfo.Name = newInfo.fallbackLabel;
                newInfo.isLabelGuessed = true;
            }
            newDeviceInfo[newInfo.Id] = newInfo;
        }
        this.mDeviceInfo = newDeviceInfo;
    }
}
class DeviceApi {
    /**
     * Returns time in ms when the device list was last updated
     */
    static get LastUpdate() {
        return DeviceApi.sLastUpdate;
    }
    /** True if the device list was updated at least once
     *
     */
    static get HasInfo() {
        return DeviceApi.sLastUpdate > 0;
    }
    /** True if a device list was requiested
     * but the results are not yet available
     */
    static get IsPending() {
        return DeviceApi.sIsPending;
    }
    /** Returns the last error detected
     *
     */
    static get LastError() {
        return this.sLastError;
    }
    /** Adds a new event handler
     *
     * @param evt
     */
    static AddOnChangedHandler(evt) {
        DeviceApi.sUpdateEvents.push(evt);
    }
    /** Removes an event handler
     *
     * @param evt
     */
    static RemOnChangedHandler(evt) {
        let index = DeviceApi.sUpdateEvents.indexOf(evt);
        if (index >= 0) {
            DeviceApi.sUpdateEvents.splice(index, 1);
        }
        else {
            _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LW("Tried to remove an unknown event handler in DeviceApi.RemOnChangedHandler");
        }
    }
    static TriggerChangedEvent() {
        for (let v of DeviceApi.sUpdateEvents) {
            try {
                v();
            }
            catch (e) {
                _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LE("Error in DeviceApi user event handler: " + e);
                console.error(e);
            }
        }
    }
    static get VideoDevices() {
        return DeviceApi.sVideoDeviceInfo.DeviceDict;
    }
    static GetVideoDevices() {
        return DeviceApi.sVideoDeviceInfo.GetDeviceLabels();
    }
    static GetVideoInputDevices() {
        return DeviceApi.sVideoDeviceInfo.GetDeviceList();
    }
    static GetAudioInputDevices() {
        return DeviceApi.sAudioInputDeviceInfo.GetDeviceList();
    }
    static Reset() {
        DeviceApi.sUpdateEvents = [];
        DeviceApi.sLastUpdate = 0;
        DeviceApi.sVideoDeviceInfo = new DeviceDb();
        DeviceApi.sAudioInputDeviceInfo = new DeviceDb();
        DeviceApi.sAccessStream = null;
        DeviceApi.sLastError = null;
        DeviceApi.sIsPending = false;
    }
    /**Updates the device list based on the current
     * access. Gives the devices numbers if the name isn't known.
     */
    static Update() {
        DeviceApi.sLastError = null;
        if (DeviceApi.IsApiAvailable()) {
            DeviceApi.sIsPending = true;
            navigator.mediaDevices.enumerateDevices()
                .then(DeviceApi.InternalOnEnum)
                .catch(DeviceApi.InternalOnErrorCatch);
        }
        else {
            DeviceApi.InternalOnErrorString(DeviceApi.ENUM_FAILED);
        }
    }
    /**Updates the device list and allows to wait until the results
     * are available
     *
     * @returns
     */
    static UpdateAsync() {
        return __awaiter(this, void 0, void 0, function* () {
            return new Promise((resolve, fail) => {
                DeviceApi.sLastError = null;
                if (DeviceApi.IsApiAvailable() == false) {
                    DeviceApi.InternalOnErrorString(DeviceApi.ENUM_FAILED);
                    fail(DeviceApi.ENUM_FAILED);
                }
                resolve();
            }).then(() => {
                DeviceApi.sIsPending = true;
                return navigator.mediaDevices.enumerateDevices()
                    .then(DeviceApi.InternalOnEnum)
                    .catch(DeviceApi.InternalOnErrorCatch);
            });
        });
    }
    /**Checks if the API is available in the browser.
     * false - browser doesn't support this API
     * true - browser supports the API (might still refuse to give
     * us access later on)
     */
    static IsApiAvailable() {
        if (navigator && navigator.mediaDevices && navigator.mediaDevices.enumerateDevices)
            return true;
        return false;
    }
    /**Asks the user for access first to get the full
     * device names.
     */
    static RequestUpdate() {
        DeviceApi.sLastError = null;
        if (DeviceApi.IsApiAvailable()) {
            DeviceApi.sIsPending = true;
            let constraints = { video: true };
            navigator.mediaDevices.getUserMedia(constraints)
                .then(DeviceApi.InternalOnStream)
                .catch(DeviceApi.InternalOnErrorCatch);
        }
        else {
            DeviceApi.InternalOnErrorString("Can't access mediaDevices or enumerateDevices");
        }
    }
    static GetDeviceId(label) {
        let devs = DeviceApi.VideoDevices;
        for (var key in devs) {
            let dev = devs[key];
            if (dev.Name == label || dev.fallbackLabel == label || dev.Id == label) {
                return dev.Id;
            }
        }
        return null;
    }
    static IsUserMediaAvailable() {
        if (navigator && navigator.mediaDevices)
            return true;
        return false;
    }
    //translates our cross-platform MediaConfig to MediaStreamConstraints
    static ToConstraints(config) {
        var constraints = {
            audio: config.Audio
        };
        if (config.Audio && config.AudioInputDevice) {
            constraints.audio = { deviceId: { exact: config.AudioInputDevice } };
        }
        let width = {};
        let height = {};
        let video = {};
        let fps = {};
        if (config.MinWidth != -1)
            width.min = config.MinWidth;
        if (config.MaxWidth != -1)
            width.max = config.MaxWidth;
        if (config.IdealWidth != -1)
            width.ideal = config.IdealWidth;
        if (config.MinHeight != -1)
            height.min = config.MinHeight;
        if (config.MaxHeight != -1)
            height.max = config.MaxHeight;
        if (config.IdealHeight != -1)
            height.ideal = config.IdealHeight;
        if (config.MinFps != -1)
            fps.min = config.MinFps;
        if (config.MaxFps != -1)
            fps.max = config.MaxFps;
        if (config.IdealFps != -1)
            fps.ideal = config.IdealFps;
        //user requested specific device? get it now to properly add it to the
        //constraints later
        let deviceId = null;
        if (config.Video && config.VideoDeviceName && config.VideoDeviceName !== "") {
            deviceId = DeviceApi.GetDeviceId(config.VideoDeviceName);
            _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("using device " + config.VideoDeviceName);
            if (deviceId === "") {
                //Workaround for Chrome 81: If no camera access is allowed chrome returns the deviceId ""
                //thus we can only request any video device. We can't select a specific one
                deviceId = null;
            }
            else if (deviceId !== null) {
                //all good
            }
            else {
                _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LE("Failed to find deviceId for label " + config.VideoDeviceName);
                throw new Error("Unknown device " + config.VideoDeviceName);
            }
        }
        //watch out: unity changed behaviour and will now
        //give 0 / 1 instead of false/true
        //using === won't work
        if (config.Video == false) {
            //video is off
            video = false;
        }
        else {
            if (Object.keys(width).length > 0) {
                video.width = width;
            }
            if (Object.keys(height).length > 0) {
                video.height = height;
            }
            if (Object.keys(fps).length > 0) {
                video.frameRate = fps;
            }
            if (deviceId !== null) {
                video.deviceId = { "exact": deviceId };
            }
            //if we didn't add anything we need to set it to true
            //at least (I assume?)
            if (Object.keys(video).length == 0) {
                video = true;
            }
        }
        constraints.video = video;
        return constraints;
    }
    static getBrowserUserMedia(constraints) {
        return __awaiter(this, void 0, void 0, function* () {
            /**There appears to be a Chrome big in version 121 and likely earlier.
             * that triggers an exception "DOMException: Could not start video source"
             * when used with our default values on the first attempt when vising a webpage.
             *
             * Reproduce with:
            setTimeout(async () => {
                await navigator.mediaDevices.getUserMedia(
                    {
                        audio: true,
                        video:
                        {
                            width:
                            {
                                ideal: 1280
                            },
                            height:
                            {
                                ideal: 720
                            }
                        }
                    })
            }, 1);
                happens only if the default video device does not support a resolution of 1280x720
                It works fine on the second attempt after the user allowed camera and we can
                attach a deviceId to the constraints.
             */
            const res = yield navigator.mediaDevices.getUserMedia(constraints);
            //after calling getUserMedia the browsers give us more accurate device names
            //buffer names now for any future synchronous access
            DeviceApi.Update();
            return res;
        });
    }
    //similar to the browsers user media but with some added workarounds to
    //support cross platform compatibility (e.g. using -1 for unset values)
    static getAssetUserMedia(config) {
        return __awaiter(this, void 0, void 0, function* () {
            const constraints = DeviceApi.ToConstraints(config);
            const result = yield DeviceApi.getBrowserUserMedia(constraints);
            return result;
        });
    }
}
DeviceApi.ENUM_FAILED = "Can't access mediaDevices or enumerateDevices";
DeviceApi.sVideoDeviceInfo = new DeviceDb();
DeviceApi.sAudioInputDeviceInfo = new DeviceDb();
DeviceApi.sAccessStream = null;
DeviceApi.sLastUpdate = 0;
DeviceApi.sIsPending = false;
DeviceApi.sLastError = null;
/**List of event handlers that are triggered when
 * the device list was updated
 */
DeviceApi.sUpdateEvents = [];
DeviceApi.InternalOnEnum = (devices) => {
    DeviceApi.sIsPending = false;
    DeviceApi.sLastUpdate = new Date().getTime();
    const videoInputDevices = devices.filter((x) => x.kind == "videoinput");
    const audioInputDevices = devices.filter((x) => x.kind == "audioinput");
    DeviceApi.sVideoDeviceInfo.UpdateDeviceList(videoInputDevices);
    DeviceApi.sAudioInputDeviceInfo.UpdateDeviceList(audioInputDevices);
    if (DeviceApi.sAccessStream) {
        var tracks = DeviceApi.sAccessStream.getTracks();
        for (var i = 0; i < tracks.length; i++) {
            tracks[i].stop();
        }
        DeviceApi.sAccessStream = null;
    }
    DeviceApi.TriggerChangedEvent();
};
DeviceApi.InternalOnErrorCatch = (err) => {
    DeviceApi.InternalOnErrorString(JSON.stringify(err));
};
DeviceApi.InternalOnErrorString = (err) => {
    DeviceApi.sIsPending = false;
    DeviceApi.sLastError = err;
    _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LE(err);
    DeviceApi.TriggerChangedEvent();
};
DeviceApi.InternalOnStream = (stream) => {
    DeviceApi.sAccessStream = stream;
    DeviceApi.Update();
};


/***/ }),

/***/ "./src/awrtc/media_browser/Media.ts":
/*!******************************************!*\
  !*** ./src/awrtc/media_browser/Media.ts ***!
  \******************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   Media: () => (/* binding */ Media)
/* harmony export */ });
/* harmony import */ var _DeviceApi__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./DeviceApi */ "./src/awrtc/media_browser/DeviceApi.ts");
/* harmony import */ var _VideoInput__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./VideoInput */ "./src/awrtc/media_browser/VideoInput.ts");
var __awaiter = (undefined && undefined.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};


class Media {
    /**
     * Singleton used for now as the browser version is missing a proper factory yet.
     * Might be removed later.
     */
    static get SharedInstance() {
        return this.sSharedInstance;
    }
    static ResetSharedInstance() {
        this.sSharedInstance = new Media();
    }
    get VideoInput() {
        if (this.videoInput === null)
            this.videoInput = new _VideoInput__WEBPACK_IMPORTED_MODULE_1__.VideoInput();
        return this.videoInput;
    }
    constructor() {
        this.videoInput = null;
        this.mScreenCaptureDevice = "_screen";
        this.mAllowScreenCapture = false;
        this.mAllowAudioCapture = false;
    }
    EnableScreenCapture(deviceName, captureAudio) {
        this.mScreenCaptureDevice = deviceName;
        this.mAllowScreenCapture = true;
        this.mAllowAudioCapture = captureAudio;
    }
    DisableScreenCapture() {
        this.mAllowScreenCapture = false;
        this.mAllowAudioCapture = false;
    }
    GetVideoDevices() {
        let device_list = _DeviceApi__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.GetVideoDevices();
        if (this.VideoInput != null) {
            const virtual_devices = this.VideoInput.GetDeviceNames();
            device_list = device_list.concat(virtual_devices);
        }
        if (this.mAllowScreenCapture) {
            device_list.push(this.mScreenCaptureDevice);
        }
        return device_list;
    }
    GetAudioInputDevices() {
        let device_list = _DeviceApi__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.GetAudioInputDevices();
        return device_list;
    }
    static IsNameSet(videoDeviceName) {
        if (videoDeviceName !== null && videoDeviceName !== "") {
            return true;
        }
        return false;
    }
    getUserMedia(config_in) {
        return __awaiter(this, void 0, void 0, function* () {
            const configNeeded = config_in.clone();
            const result = new MediaStream();
            //first we check if the video device corresponds to a non physical camera
            if (configNeeded.Video && Media.IsNameSet(configNeeded.VideoDeviceName)) {
                //a specific video device is requested.
                if (this.videoInput != null && this.videoInput.HasDevice(configNeeded.VideoDeviceName)) {
                    //we found a video input device that matches. add a track to it to the results
                    const videoInputStream = this.videoInput.GetStream(configNeeded.VideoDeviceName);
                    result.addTrack(videoInputStream.getVideoTracks()[0]);
                    configNeeded.Video = false;
                }
                else if (this.mAllowScreenCapture && configNeeded.VideoDeviceName === this.mScreenCaptureDevice) {
                    //we found a screen capture device that matches. add a track to it to the results
                    let constraints = {};
                    if (configNeeded.IdealWidth <= 0 && configNeeded.IdealHeight <= 0) {
                        constraints.video = true;
                    }
                    else {
                        let vconstraints = {};
                        if (configNeeded.IdealWidth > 0)
                            vconstraints.width = configNeeded.IdealWidth;
                        if (configNeeded.IdealHeight > 0)
                            vconstraints.height = configNeeded.IdealHeight;
                        constraints.video = vconstraints;
                    }
                    if (this.mAllowAudioCapture && configNeeded.Audio)
                        constraints.audio = true;
                    const screenStream = yield navigator.mediaDevices.getDisplayMedia(constraints);
                    if (screenStream.getVideoTracks().length > 0) {
                        result.addTrack(screenStream.getVideoTracks()[0]);
                    }
                    else {
                        //TODO: improve error handling
                        console.warn("Failed to get video access via getDisplayMedia");
                    }
                    if (constraints.audio) {
                        if (screenStream.getAudioTracks().length > 0) {
                            result.addTrack(screenStream.getAudioTracks()[0]);
                            configNeeded.Audio = false;
                        }
                        else {
                            //TODO: The API needs to be more clear here. Unclear if we should continue here
                            //or fail.
                            console.warn("Failed to get audio access via getDisplayMedia.");
                        }
                    }
                    configNeeded.Video = false;
                }
            }
            //any devices still needed? try to get them via the physical device api
            if (configNeeded.Video || configNeeded.Audio) {
                const deviceStream = yield _DeviceApi__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.getAssetUserMedia(configNeeded);
                deviceStream.getTracks().forEach(x => result.addTrack(x));
            }
            return result;
        });
    }
}
//experimental. Will be used instead of the device api to create streams 
Media.sSharedInstance = new Media();


/***/ }),

/***/ "./src/awrtc/media_browser/MediaPeer.ts":
/*!**********************************************!*\
  !*** ./src/awrtc/media_browser/MediaPeer.ts ***!
  \**********************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   MediaPeer: () => (/* binding */ MediaPeer)
/* harmony export */ });
/* harmony import */ var _network_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../network/index */ "./src/awrtc/network/index.ts");
/* harmony import */ var _BrowserMediaStream__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./BrowserMediaStream */ "./src/awrtc/media_browser/BrowserMediaStream.ts");
var __awaiter = (undefined && undefined.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
/*
Copyright (c) 2022, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/


class MediaPeer extends _network_index__WEBPACK_IMPORTED_MODULE_0__.WebRtcDataPeer {
    constructor(connectionId, peerConfig, mediaConfig, baseLogger) {
        super(connectionId, peerConfig, baseLogger);
        //TODO: Remove debug values and replace with MediaConfig
        this.DEBUG_QUALITY = false;
        this.DEBUG_preferredCodec = null;
        //if true it rewrites the profile id for iOS 4k
        this.DEBUG_IosWorkaround = false;
        this.mRemoteStream = null;
        this.mAudioSender = null;
        this.mVideoSender = null;
        //quick workaround to allow html user to get the HTMLVideoElement once it is
        //created. Might be done via events later to make wrapping to unity/emscripten possible
        this.InternalStreamAdded = null;
        this.mMediaConfig = mediaConfig;
    }
    OnSetup() {
        super.OnSetup();
        this.mPeer.ontrack = (ev) => { this.OnTrack(ev); };
    }
    getTransceiverByKind(kind) {
        for (const tr of this.mPeer.getTransceivers()) {
            if ((tr.receiver !== null && tr.receiver.track !== null && tr.receiver.track.kind == kind)
                || (tr.sender !== null && tr.sender.track !== null && tr.sender.track.kind == kind)) {
                return tr;
            }
        }
        return null;
    }
    static ReorderList(lst, mimeTypes) {
        // Sort the list based on priorities
        lst.sort((a, b) => {
            //get the index for each codec
            const indexA = mimeTypes.findIndex(mimeType => a.mimeType.includes(mimeType));
            const indexB = mimeTypes.findIndex(mimeType => b.mimeType.includes(mimeType));
            //use the index as priority. if the index is not found we use the lowest priority (length of the array)
            const priorityA = indexA === -1 ? mimeTypes.length : indexA;
            const priorityB = indexB === -1 ? mimeTypes.length : indexB;
            return priorityA - priorityB;
        });
        return lst;
    }
    /*
    private static ReorderList(lst: RTCRtpCodecCapability[], mimeType: string) {
        const containsKeyword = lst.filter(item => item.mimeType.includes(mimeType));
        const doesNotContainKeyword = lst.filter(item => !item.mimeType.includes(mimeType));
        return containsKeyword.concat(doesNotContainKeyword);
    }*/
    /**Called when the transceiver for video is first accessed
     *
     * TODO: Watch out when implementing codecs. If the old
     * offerToReceive option is used this is only called after
     * createOffer/createAnswer. Likely too late to change the codec order
     *
     * @param sender
     * @returns
     */
    SetVideoParams(sender) {
        return __awaiter(this, void 0, void 0, function* () {
            if (!sender || !sender.track)
                return;
            if (this.mMediaConfig.VideoBitrateKbits) {
                try {
                    //Added verbose warnings here because Safari & mobile browsers appear to
                    //return some unusual values
                    const params = sender.getParameters();
                    if (!params) {
                        this.log.LW("Unable to call VideoBitrateKbits. getParameters returned null");
                    }
                    if (!params.encodings) {
                        this.log.LW("encodings was undefined. VideoBitrateKbits ignored.");
                        return;
                    }
                    if (Array.isArray(params.encodings) === false) {
                        this.log.LW("encodings was not an array. Setting VideoBitrateKbits ignored");
                        return;
                    }
                    if (params.encodings.length === 0) {
                        this.log.LW("encodings was empty. Setting VideoBitrateKbits ignored");
                        return;
                    }
                    params.encodings[0].maxBitrate = this.mMediaConfig.VideoBitrateKbits * 1000;
                    yield sender.setParameters(params);
                }
                catch (err) {
                    this.log.LE("Setting VideoBitrateKbits failed with exception:");
                    this.log.LE(err);
                }
            }
        });
    }
    setVideoTransceiver(transceiver) {
        if (this.mMediaConfig && this.mMediaConfig.VideoCodecs && this.mMediaConfig.VideoCodecs.length > 0) {
            const codecList = RTCRtpReceiver.getCapabilities("video").codecs;
            //this.log.L(JSON.stringify(codecList));
            const newCodecList = MediaPeer.ReorderList(codecList, this.mMediaConfig.VideoCodecs);
            if (transceiver.setCodecPreferences) {
                transceiver.setCodecPreferences(newCodecList);
            }
            else {
                this.log.LW("Unable to call setCodecPreferences. Default codecs will be used.");
            }
        }
    }
    CreateOfferImpl() {
        return __awaiter(this, void 0, void 0, function* () {
            if (this.SINGLE_NEGOTIATION) {
                //if we haven't added a transceiver yet we do it here
                //this is roughly the same as this.mPeer.createOffer(offerOptions);
                //except we always set the direction to "sendrecv".
                //This causes the browser to create inactive dummy tracks until we
                //replace them with our own later
                if (this.getTransceiverByKind("audio") == null) {
                    this.log.L("Add transceiver for audio");
                    let atransceiver = this.mPeer.addTransceiver("audio", { direction: "sendrecv" });
                    this.mAudioSender = atransceiver.sender;
                    //this.mAudioSender.setStreams(this.mMediaStream);
                }
                if (this.getTransceiverByKind("video") == null) {
                    this.log.L("Add transceiver for video");
                    let vtransceiver = this.mPeer.addTransceiver("video", { direction: "sendrecv" });
                    this.setVideoTransceiver(vtransceiver);
                    this.mVideoSender = vtransceiver.sender;
                    yield this.SetVideoParams(this.mVideoSender);
                    //this.mVideoSender.setStreams(this.mMediaStream);
                }
                return this.mPeer.createOffer();
            }
            else {
                const useNew = true;
                if (useNew) {
                    //todo: we have to change the direction depending on what local tracks we have
                    let atransceiver = this.getTransceiverByKind("audio");
                    if (atransceiver == null) {
                        this.log.L("Add transceiver for audio");
                        atransceiver = this.mPeer.addTransceiver("audio", { direction: "recvonly" });
                        this.mAudioSender = atransceiver.sender;
                    }
                    let vtransceiver = this.getTransceiverByKind("video");
                    if (vtransceiver == null) {
                        //if we don't have a transceiver yet (not sending video) create one
                        this.log.L("Add transceiver for video");
                        vtransceiver = this.mPeer.addTransceiver("video", { direction: "recvonly" });
                    }
                    this.setVideoTransceiver(vtransceiver);
                    this.mVideoSender = vtransceiver.sender;
                    yield this.SetVideoParams(this.mVideoSender);
                    const offer = yield this.mPeer.createOffer();
                    return offer;
                }
                else {
                    //TODO: This system is weird. We let the receiver decide the priority?
                    //if we uncomment this on the offerer side the answerer will send VP8
                    const vtransceiver = this.getTransceiverByKind("video");
                    if (vtransceiver != null) {
                        this.setVideoTransceiver(vtransceiver);
                    }
                    else {
                        console.warn("No transceiver found. ");
                    }
                    //we keep this backwards compatible for now for simplicity
                    //this should create 2 transceivers if not yet created and set them to reconly
                    //otherwise addTrack created them already and they are set to sendrecv
                    const offerOptions = { "offerToReceiveAudio": true, "offerToReceiveVideo": true };
                    const offer = yield this.mPeer.createOffer(offerOptions);
                    //this.setVideoTransceiver(vtransceiver);
                    if (vtransceiver !== null) {
                        this.mVideoSender = vtransceiver.sender;
                        yield this.SetVideoParams(this.mVideoSender);
                    }
                    return offer;
                }
            }
        });
    }
    CreateAnswerImpl() {
        return __awaiter(this, void 0, void 0, function* () {
            if (this.SINGLE_NEGOTIATION) {
                //if addTrack or replaceTrack were not used to attach a track we must make sure we 
                //manually set the direction to sendrecv & buffer the senders to reuse later. 
                //Otherwise future changes will trigger renegotiation
                const atransceiver = this.getTransceiverByKind("audio");
                if (atransceiver !== null) {
                    atransceiver.direction = "sendrecv";
                    this.mAudioSender = atransceiver.sender;
                }
                const vtransceiver = this.getTransceiverByKind("video");
                if (vtransceiver !== null) {
                    vtransceiver.direction = "sendrecv";
                    this.setVideoTransceiver(vtransceiver);
                    this.mVideoSender = vtransceiver.sender;
                    yield this.SetVideoParams(this.mVideoSender);
                }
            }
            else {
                //get the created sender and apply settings
                const vtransceiver = this.getTransceiverByKind("video");
                if (vtransceiver !== null) {
                    this.setVideoTransceiver(vtransceiver);
                    this.mVideoSender = vtransceiver.sender;
                    yield this.SetVideoParams(this.mVideoSender);
                }
            }
            return this.mPeer.createAnswer();
        });
    }
    OnCleanup() {
        super.OnCleanup();
        if (this.mRemoteStream != null) {
            this.mRemoteStream.Dispose();
            this.mRemoteStream = null;
        }
    }
    OnTrack(ev) {
        //we stop relying on streams altogether now
        this.log.L("ontrack: " + ev.track.kind);
        this.UpdateRemoteStream(ev.track);
    }
    UpdateRemoteStream(track) {
        if (this.mRemoteStream == null) {
            this.mRemoteStream = new _BrowserMediaStream__WEBPACK_IMPORTED_MODULE_1__.BrowserMediaStream(false, this.log);
            //trigger events once the stream has its meta data available
            this.mRemoteStream.InternalStreamAdded = (stream) => {
                if (this.InternalStreamAdded != null) {
                    this.InternalStreamAdded(this, stream);
                }
            };
        }
        if (this.SINGLE_NEGOTIATION === false) {
            //just add the track to the existing stream
            //HTMLVideoElement should be able to handle this
            this.mRemoteStream.UpdateTrack(track);
        }
        else {
            //workaround 1:
            //If we connect a new Peer with video disabled it will already contain a video track
            //If we add this track immediately then HTMLVideoElement does not playback audio until
            //the video is being activated (which might never happen).
            //To ensure audio can play without video we only add tracks once they become active (onunmute) event
            //Workaround 2:
            //Chrome incorrectly (?) treats the inactive video track as "unmuted" at first
            //but triggers the correct "muted" event roughly 1 second later
            //Thus we add the track first, then remove the track again once the mute event triggers,
            //then we have to reset the video element to ensure audio can start playing without the video track
            //Note these workaround currently depend on the audio track arriving first
            //TODO: These workarounds only work on Firefox and Chrome. Safari shows the track as unmuted and never triggers
            //a mute event. Audio is not played back
            this.log.L("delaying track of type: " + track.kind + " muted?" + track.muted + " enabled?" + track.enabled);
            track.onunmute = () => {
                this.log.L("adding unmuted track of type: " + track.kind);
                this.mRemoteStream.UpdateTrack(track);
            };
            track.onmute = () => {
                this.log.L("removing muted track of type: " + track.kind);
                this.mRemoteStream.Stream.removeTrack(track);
                //this resets the HTMLVideoElemt.srcObject and triggers the stream to reload again without
                //the track
                this.mRemoteStream.ResetObject();
            };
        }
    }
    TryGetRemoteFrame() {
        if (this.mRemoteStream == null)
            return null;
        return this.mRemoteStream.TryGetFrame();
    }
    PeekFrame() {
        if (this.mRemoteStream == null)
            return null;
        return this.mRemoteStream.PeekFrame();
    }
    SetLocalStream(stream_container, config) {
        return __awaiter(this, void 0, void 0, function* () {
            this.mMediaConfig = config;
            let atrack = null;
            let vtrack = null;
            if (stream_container !== null) {
                atrack = stream_container.GetAudioTrack();
                vtrack = stream_container.GetVideoTrack();
                if (this.mMediaConfig.VideoContentHint)
                    vtrack.contentHint = this.mMediaConfig.VideoContentHint;
            }
            //TODO: Fully upgrade the transceiver API once firefox supports
            //setStreams .
            if (atrack != null) {
                if (this.mAudioSender != null) {
                    //this.mAudioSender.setStreams(stream_container.Stream);
                    try {
                        yield this.mAudioSender.replaceTrack(atrack);
                        const atransceiver = this.getTransceiverByKind("audio");
                        if (atransceiver) {
                            if (atransceiver.currentDirection != "sendrecv") {
                                atransceiver.direction = "sendrecv";
                            }
                        }
                        else {
                            this.log.LW("Unable to find the audio transceiver to attach a new track. This indicates the peer is incorrectly configured.");
                        }
                    }
                    catch (err) {
                        this.log.LE("Error during replaceTrack: " + err);
                    }
                }
                else {
                    //no sender yet but a track is suppose to be attached -> create one
                    //this does create a transceiver set to "sendrecv"
                    this.log.L("addinging track of type " + atrack.kind);
                    //ensure stream is attached as older builds depend on this
                    this.mAudioSender = this.mPeer.addTrack(atrack, stream_container.Stream);
                }
            }
            else {
                //no audio track. Make sure if we have a sender no tracks are attached
                if (this.mAudioSender != null && this.mAudioSender.track !== null) {
                    this.log.L("setting track of type audio to null");
                    //wait for firefox support of setStreams
                    //this.mAudioSender.setStreams(stream_container.Stream);
                    yield this.mAudioSender.replaceTrack(null);
                }
            }
            if (vtrack != null) {
                if (this.mVideoSender != null) {
                    //this.mVideoSender.setStreams(stream_container.Stream);
                    try {
                        yield this.mVideoSender.replaceTrack(vtrack);
                        const vtransceiver = this.getTransceiverByKind("video");
                        if (vtransceiver) {
                            if (vtransceiver.currentDirection != "sendrecv")
                                vtransceiver.direction = "sendrecv";
                        }
                        else {
                            _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LW("Unable to find the video transceiver to attach a new track. This indicates the peer is incorrectly configured.");
                        }
                    }
                    catch (err) {
                        this.log.LE("Error during replaceTrack: " + err);
                    }
                }
                else {
                    //no sender yet but a track is suppose to be attached -> create one
                    //this does create a transceiver set to "sendrecv"
                    this.log.L("addinging track of type " + vtrack.kind);
                    //ensure stream is attached as older builds depend on this
                    this.mVideoSender = this.mPeer.addTrack(vtrack, stream_container.Stream);
                    if (this.DEBUG_QUALITY) {
                        let codec_mimeType = "unknown";
                        let codecId = "unknown";
                        setInterval(() => __awaiter(this, void 0, void 0, function* () {
                            const all = yield this.mPeer.getStats(null);
                            console.debug("all:");
                            console.debug(Array.from(all.values()));
                            all.forEach((report) => {
                                if (report.kind === "video" && report.type === "outbound-rtp") {
                                    console.log(report.type + " " + report.frameWidth + "x" + report.frameHeight
                                        + " FPS:" + report.framesPerSecond
                                        + " qual:" + JSON.stringify(report.qualityLimitationDurations)
                                        + " codec:" + codec_mimeType
                                        + " offer: " + this.mIsOfferer);
                                    codecId = report.codecId;
                                }
                                else if (report.type === "codec" && report.id == codecId) {
                                    //the actual codec mime type is returned via a different stats event
                                    //store the id for our next regular log output
                                    codec_mimeType = report.mimeType;
                                }
                            });
                        }), 1000);
                    }
                }
            }
            else {
                //no video track. Make sure if we have a sender no old tracks are still attached
                if (this.mVideoSender != null && this.mVideoSender.track !== null) {
                    this.log.L("setting track of type video to null");
                    //wait for firefox support of setStreams
                    //this.mVideoSender.setStreams(stream_container.Stream);
                    yield this.mVideoSender.replaceTrack(null);
                }
            }
            if (this.DEBUG)
                console.warn("SetLocalStream completed: ", this.mPeer.getTransceivers());
        });
    }
    Update() {
        super.Update();
        if (this.mRemoteStream != null) {
            this.mRemoteStream.Update();
        }
    }
    SetVolume(volume) {
        if (this.mRemoteStream != null)
            this.mRemoteStream.SetVolume(volume);
    }
    SetVolumePan(volume, pan) {
        if (this.mRemoteStream != null)
            this.mRemoteStream.SetVolumePan(volume, pan);
    }
    HasAudioTrack() {
        if (this.mRemoteStream != null)
            return this.mRemoteStream.HasAudioTrack();
        return false;
    }
    HasVideoTrack() {
        if (this.mRemoteStream != null)
            return this.mRemoteStream.HasVideoTrack();
        return false;
    }
    //Gives a specific codec priority over the others
    EditCodecs(lines) {
        this.log.LW("sdp munging: prioritizing codec " + this.DEBUG_preferredCodec);
        //index and list of all video codec id's
        //e.g.: m=video 9 UDP/TLS/RTP/SAVPF 96 97 98 99 100 101 102 121 127 120 125 107 108 109 35 36 124 119 123 118 114 115 116
        let vcodecs_line_index;
        let vcodecs_line_split;
        let vcodecs_list;
        for (let i = 0; i < lines.length; i++) {
            let line = lines[i];
            if (line.startsWith("m=video")) {
                vcodecs_line_split = line.split(" ");
                vcodecs_list = vcodecs_line_split.slice(3, vcodecs_line_split.length);
                vcodecs_line_index = i;
                //console.log(vcodecs_list);
                break;
            }
        }
        //list of video codecs positioned based on our priority list
        let vcodecs_list_new = [];
        //start below the the m=video line
        for (let i = vcodecs_line_index + 1; i < lines.length; i++) {
            let line = lines[i];
            let prefix = "a=rtpmap:";
            if (line.startsWith(prefix)) {
                let subline = line.substr(prefix.length);
                let split = subline.split(" ");
                let codecId = split[0];
                let codecDesc = split[1];
                let codecSplit = codecDesc.split("/");
                let codecName = codecSplit[0];
                //sanity check. is this a video codec?
                if (vcodecs_list.includes(codecId)) {
                    if (codecName === this.DEBUG_preferredCodec) {
                        vcodecs_list_new.unshift(codecId);
                    }
                    else {
                        vcodecs_list_new.push(codecId);
                    }
                }
            }
        }
        //first 3 elements remain the same
        let vcodecs_line_new = vcodecs_line_split[0] + " " + vcodecs_line_split[1] + " " + vcodecs_line_split[2];
        //add new codec list after it
        vcodecs_list_new.forEach((x) => { vcodecs_line_new = vcodecs_line_new + " " + x; });
        //replace old line
        lines[vcodecs_line_index] = vcodecs_line_new;
    }
    //Replaces H264 profile levels
    //iOS workaround. Streaming from iOS to browser currently fails without this if
    //resolution is above 720p and h264 is active
    EditProfileLevel(lines) {
        const target_profile_level_id = "2a";
        //TODO: Make sure we only edit H264. There could be other codecs in the future
        //that look identical
        console.warn("sdp munging: replacing h264 profile-level with " + target_profile_level_id);
        let vcodecs_line_index;
        let vcodecs_line_split;
        let vcodecs_list;
        for (let i = 0; i < lines.length; i++) {
            let line = lines[i];
            if (line.startsWith("a=fmtp:")) {
                //looking for profile-level-id=42001f
                //we replace the 1f
                let searchString = "profile-level-id=";
                let sublines = line.split(";");
                let updateLine = false;
                for (let k = 0; k < sublines.length; k++) {
                    let subline = sublines[k];
                    if (subline.startsWith(searchString)) {
                        let len = searchString.length + 4;
                        sublines[k] = sublines[k].substr(0, len) + target_profile_level_id;
                        updateLine = true;
                        break;
                    }
                }
                if (updateLine) {
                    lines[i] = sublines.join(";");
                }
            }
        }
    }
    //for filtering out features. 
    FilterFeatures(lines) {
        lines = lines.filter((value, index) => {
            const res = !value.includes("goog-remb");
            if (res == false) {
                console.log("dropping " + value);
            }
            return res;
        });
        return lines;
    }
    ProcessLocalSdp(desc) {
        if (MediaPeer.MUNGE_SDP === false)
            return desc;
        console.warn("sdp munging active");
        let sdp_in = desc.sdp;
        let sdp_out = "";
        let lines = sdp_in.split("\r\n");
        if (this.DEBUG_preferredCodec)
            this.EditCodecs(lines);
        //this.EditProfileLevel(lines);
        //lines = this.FilterFeatures(lines);
        if (this.DEBUG_IosWorkaround) {
            for (let i = 0; i < lines.length; i++) {
                //browsers always use 1f even if they support higher res
                //iOs breaks if 1f is used but resolution is higher
                lines[i] = lines[i].replace("42e01f", "42e034");
            }
        }
        sdp_out = lines.join("\r\n");
        let desc_out = { type: desc.type, sdp: sdp_out };
        return desc_out;
    }
    ProcessRemoteSdp(desc) {
        if (MediaPeer.MUNGE_SDP === false)
            return desc;
        console.warn("sdp munging active");
        let sdp_in = desc.sdp;
        let sdp_out = "";
        let lines = sdp_in.split("\r\n");
        if (this.DEBUG_preferredCodec)
            this.EditCodecs(lines);
        if (this.DEBUG_IosWorkaround) {
            for (let i = 0; i < lines.length; i++) {
                //browsers always use 1f even if they support higher res
                //iOS breaks if 1f is used but resolution is higher
                lines[i] = lines[i].replace("42e034", "42e01f");
            }
        }
        //lines = this.FilterFeatures(lines);
        sdp_out = lines.join("\r\n");
        let desc_out = { type: desc.type, sdp: sdp_out };
        return desc_out;
    }
}
MediaPeer.MUNGE_SDP = false;


/***/ }),

/***/ "./src/awrtc/media_browser/VideoInput.ts":
/*!***********************************************!*\
  !*** ./src/awrtc/media_browser/VideoInput.ts ***!
  \***********************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   VideoInput: () => (/* binding */ VideoInput),
/* harmony export */   VideoInputType: () => (/* binding */ VideoInputType)
/* harmony export */ });
/**TS version of the C++ / C# side Native VideoInput API
 *
 *
 * In addition it also supports adding a HTMLCanvasElement as a video device. This can be
 * a lot faster in the browser than the C / C++ style UpdateFrame methods that use raw byte arrays
 * or pointers to deliver an image.
 *
 * Note there are currently three distinct ways how this is used:
 * 1.   Using AddCanvasDevice without scaling (wdith = 0, height =0 or the same as the canvas)
 *      In this mode the MediaStream will be returned from the canvas. Drawing calls from the canvas
 *      turn into video frames of the video without any manual UpdateFrame calls
 *
 * 2.   Using AddCanvasDevice with scaling by setting a width / height different from the canvas.
 *      In this mode the user draws to the canvas and every time UpdateFrame is called a scaled frame
 *      is created that will turn into video frames. Lower UpdateFrame calls will reduce the framerate
 *      even if the original canvas us used a higher framerate.
 *      This mode should result in lower data usage.
 *
 * 3.   Using AddDevice and UpdateFrame to deliver raw byte array frames. This is a compatibility mode
 *      that works similar to the C / C++ and C# API. An internal canvas is created and updated based on
 *      the data the user delivers. This mode makes sense if you generate custom data that doesn't have
 *      a canvas as its source.
 *      This mode can be quite slow and inefficient.
 *
 * TODO:
 *  -   Using AddDevice with one resolution & UpdateFrame with another might not support scaling yet but
 *      activating the 2nd canvas for scaling might
 *      reduce the performance even more. Check if there is a better solution and if scaling is even needed.
 *      It could easily be added by calling initScaling but it must be known if scaling is required before
 *      the device is selected by the user. Given that scaling can reduce the performance doing so by default
 *      might cause problems for some users.
 *
 *  -   UpdateFrame rotation and firstRowIsBottom aren't supported yet. Looks like they aren't needed for
 *      WebGL anyway. Looks like frames here always start with the top line and rotation is automatically
 *      handled by the browser.
 *
 */
class VideoInput {
    constructor() {
        this.canvasDevices = {};
    }
    /**Adds a canvas to use as video source for streaming.
     *
     * Make sure canvas.getContext is at least called once before calling this method.
     *
     * @param canvas
     * @param deviceName
     * @param width
     * @param height
     * @param fps
     */
    AddCanvasDevice(canvas, deviceName, width, height, fps) {
        let cdev = CanvasDevice.CreateExternal(canvas, fps);
        if (width != canvas.width || height != canvas.height) {
            //console.warn("testing scaling");
            cdev.initScaling(width, height);
        }
        this.canvasDevices[deviceName] = cdev;
    }
    /**For internal use.
     * Allows to check if the device already exists.
     *
     * @param dev
     */
    HasDevice(dev) {
        return dev in this.canvasDevices;
    }
    /**For internal use.
     * Lists all registered devices.
     *
     */
    GetDeviceNames() {
        return Object.keys(this.canvasDevices);
    }
    /**For internal use.
     * Returns a MediaStream for the given device.
     *
     * @param dev
     */
    GetStream(dev) {
        if (this.HasDevice(dev)) {
            let device = this.canvasDevices[dev];
            //watch out: This can trigger an exception if getContext has never been called before.
            //There doesn't seem to way to detect this beforehand though
            let stream = device.captureStream();
            return stream;
        }
        return null;
    }
    /**C# API: public void AddDevice(string name, int width, int height, int fps);
     *
     * Adds a device that will be accessible via the given name. Width / Height determines
     * the size of the canvas that is used to stream the video.
     *
     *
     * @param name unique name for the canvas
     * @param width width of the canvase used for the stream
     * @param height height of the canvase used for the stream
     * @param fps Expected FPS used by the stream. 0 or undefined to let the browser decide (likely based on actual draw calls)
     */
    AddDevice(name, width, height, fps) {
        let cdev = CanvasDevice.CreateInternal(width, height, fps);
        this.canvasDevices[name] = cdev;
    }
    RemCanvasDevice(deviceName) {
        let cdev = this.canvasDevices[deviceName];
        if (cdev) {
            delete this.canvasDevices[deviceName];
        }
    }
    //C# API: public void RemoveDevice(string name);
    RemoveDevice(name) {
        this.RemCanvasDevice(name);
    }
    /**
     * Use UpdateFrame with name only to trigger a new frame without changing the content (e.g. if AddCanvasDevice was used to add the device and it needs scaling)
     * Use UpdateFrame with image data if you added the device via AddDevice and want to updat its content
     *
     *
     *
     * @param name name of the device
     * @param dataPtr array to the image data
     * @param width must be the exact width of the image in dataPtr
     * @param height must be the exact height of the image in dataPtr
     * @param type must be ARGB at the moment
     * @param rotation not yet supported
     * @param firstRowIsBottom not yet supported
     */
    UpdateFrame(name, dataPtr, width, height, type = VideoInputType.ARGB, rotation = 0, firstRowIsBottom = true) {
        if (this.HasDevice(name)) {
            let device = this.canvasDevices[name];
            if (device.IsExternal() || dataPtr == null) {
                //can't change external images / no data available. just generate a new frame without new data 
                device.UpdateFrame();
            }
            else {
                var data = new ImageData(dataPtr, width, height);
                device.UpdateFrame(data);
            }
            return true;
        }
        return false;
    }
}
/**Wraps around a canvas object to use as a source for MediaStream.
 * It supports streaming via a second canvas that is used to scale the image
 * before streaming. For scaling UpdateFrame needs to be called one a frame.
 * Without scaling the browser will detect changes in the original canvas
 * and automatically update the stream
 *
 */
class CanvasDevice {
    getStreamingCanvas() {
        if (this.scaling_canvas == null)
            return this.canvas;
        return this.scaling_canvas;
    }
    captureStream() {
        if (this.is_capturing == false && this.scaling_canvas) {
            //scaling is active. 
            this.startScaling();
        }
        this.is_capturing = true;
        if (this.fps && this.fps > 0) {
            return this.getStreamingCanvas().captureStream(this.fps);
        }
        return this.getStreamingCanvas().captureStream();
    }
    constructor(c, external_canvas, fps) {
        /**false = we own the canvas and can change its settings e.g. via VideoInput
         * true = externally used canvas. Can't change width / height or any other settings
         */
        this.external_canvas = false;
        /**Canvas element to handle scaling.
         * Remains null if initScaling is never called and width / height is expected to
         * fit the canvas.
         *
         */
        this.scaling_canvas = null;
        //private scaling_interval = -1;
        this.is_capturing = false;
        this.canvas = c;
        this.external_canvas = external_canvas;
        this.fps = fps;
    }
    static CreateInternal(width, height, fps) {
        const c = CanvasDevice.MakeCanvas(width, height);
        return new CanvasDevice(c, false, fps);
    }
    static CreateExternal(c, fps) {
        return new CanvasDevice(c, true, fps);
    }
    /**Adds scaling support to this canvas device.
     *
     * @param width
     * @param height
     */
    initScaling(width, height) {
        this.scaling_canvas = document.createElement("canvas");
        this.scaling_canvas.width = width;
        this.scaling_canvas.height = height;
        this.scaling_canvas.getContext("2d");
    }
    /**Used to update the frame data if the canvas is managed internally.
     * Use without image data to just trigger the scaling / generation of a new frame if the canvas is drawn to externally.
     *
     * If the canvas is managed externally and scaling is not required this method won't do anything. A new frame is instead
     * generated automatically based on the browser & canvas drawing operations.
     */
    UpdateFrame(data) {
        if (data) {
            let ctx = this.canvas.getContext("2d");
            //TODO: This doesn't seem to support scaling out of the box
            //we might need to combien this with the scaling system as well
            //in case users deliver different resolutions than the device is setup for
            ctx.putImageData(data, 0, 0);
        }
        this.scaleNow();
    }
    /**Called the first time we need the scaled image to ensure
     * the buffers are all filled.
     */
    startScaling() {
        this.scaleNow();
    }
    scaleNow() {
        if (this.scaling_canvas != null) {
            let ctx = this.scaling_canvas.getContext("2d");
            //ctx.fillStyle = "#FF0000";
            //ctx.fillRect(0, 0, this.scaling_canvas.width, this.scaling_canvas.height);
            //ctx.clearRect(0, 0, this.scaling_canvas.width, this.scaling_canvas.height)
            ctx.drawImage(this.canvas, 0, 0, this.scaling_canvas.width, this.scaling_canvas.height);
        }
    }
    IsExternal() {
        return this.external_canvas;
    }
    static MakeCanvas(width, height) {
        let canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        let ctx = canvas.getContext("2d");
        //make red for debugging purposes
        ctx.fillStyle = "red";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        return canvas;
    }
}
/** Only one format supported by browsers so far.
 *  Maybe more can be added in the future.
 */
var VideoInputType;
(function (VideoInputType) {
    VideoInputType[VideoInputType["ARGB"] = 0] = "ARGB";
})(VideoInputType || (VideoInputType = {}));


/***/ }),

/***/ "./src/awrtc/media_browser/index.ts":
/*!******************************************!*\
  !*** ./src/awrtc/media_browser/index.ts ***!
  \******************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   AudioProcessor: () => (/* reexport safe */ _AudioProcessor__WEBPACK_IMPORTED_MODULE_7__.AudioProcessor),
/* harmony export */   AutoplayResolver: () => (/* reexport safe */ _AutoplayResolver__WEBPACK_IMPORTED_MODULE_8__.AutoplayResolver),
/* harmony export */   BrowserMediaNetwork: () => (/* reexport safe */ _BrowserMediaNetwork__WEBPACK_IMPORTED_MODULE_0__.BrowserMediaNetwork),
/* harmony export */   BrowserMediaStream: () => (/* reexport safe */ _BrowserMediaStream__WEBPACK_IMPORTED_MODULE_2__.BrowserMediaStream),
/* harmony export */   BrowserWebRtcCall: () => (/* reexport safe */ _BrowserWebRtcCall__WEBPACK_IMPORTED_MODULE_1__.BrowserWebRtcCall),
/* harmony export */   DeviceApi: () => (/* reexport safe */ _DeviceApi__WEBPACK_IMPORTED_MODULE_4__.DeviceApi),
/* harmony export */   Media: () => (/* reexport safe */ _Media__WEBPACK_IMPORTED_MODULE_6__.Media),
/* harmony export */   MediaDevice: () => (/* reexport safe */ _DeviceApi__WEBPACK_IMPORTED_MODULE_4__.MediaDevice),
/* harmony export */   MediaPeer: () => (/* reexport safe */ _MediaPeer__WEBPACK_IMPORTED_MODULE_3__.MediaPeer),
/* harmony export */   VideoInput: () => (/* reexport safe */ _VideoInput__WEBPACK_IMPORTED_MODULE_5__.VideoInput),
/* harmony export */   VideoInputType: () => (/* reexport safe */ _VideoInput__WEBPACK_IMPORTED_MODULE_5__.VideoInputType)
/* harmony export */ });
/* harmony import */ var _BrowserMediaNetwork__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./BrowserMediaNetwork */ "./src/awrtc/media_browser/BrowserMediaNetwork.ts");
/* harmony import */ var _BrowserWebRtcCall__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./BrowserWebRtcCall */ "./src/awrtc/media_browser/BrowserWebRtcCall.ts");
/* harmony import */ var _BrowserMediaStream__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./BrowserMediaStream */ "./src/awrtc/media_browser/BrowserMediaStream.ts");
/* harmony import */ var _MediaPeer__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./MediaPeer */ "./src/awrtc/media_browser/MediaPeer.ts");
/* harmony import */ var _DeviceApi__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! ./DeviceApi */ "./src/awrtc/media_browser/DeviceApi.ts");
/* harmony import */ var _VideoInput__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! ./VideoInput */ "./src/awrtc/media_browser/VideoInput.ts");
/* harmony import */ var _Media__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! ./Media */ "./src/awrtc/media_browser/Media.ts");
/* harmony import */ var _AudioProcessor__WEBPACK_IMPORTED_MODULE_7__ = __webpack_require__(/*! ./AudioProcessor */ "./src/awrtc/media_browser/AudioProcessor.ts");
/* harmony import */ var _AutoplayResolver__WEBPACK_IMPORTED_MODULE_8__ = __webpack_require__(/*! ./AutoplayResolver */ "./src/awrtc/media_browser/AutoplayResolver.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/











/***/ }),

/***/ "./src/awrtc/network/Helper.ts":
/*!*************************************!*\
  !*** ./src/awrtc/network/Helper.ts ***!
  \*************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   Debug: () => (/* binding */ Debug),
/* harmony export */   Encoder: () => (/* binding */ Encoder),
/* harmony export */   Encoding: () => (/* binding */ Encoding),
/* harmony export */   Helper: () => (/* binding */ Helper),
/* harmony export */   List: () => (/* binding */ List),
/* harmony export */   Output: () => (/* binding */ Output),
/* harmony export */   Queue: () => (/* binding */ Queue),
/* harmony export */   Random: () => (/* binding */ Random),
/* harmony export */   SLog: () => (/* binding */ SLog),
/* harmony export */   SLogLevel: () => (/* binding */ SLogLevel),
/* harmony export */   SLogger: () => (/* binding */ SLogger),
/* harmony export */   UTF16Encoding: () => (/* binding */ UTF16Encoding),
/* harmony export */   WebRtcHelper: () => (/* binding */ WebRtcHelper)
/* harmony export */ });
/*
Copyright (c) 2023, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
/**Contains some helper classes to keep the typescript implementation
 * similar to the C# implementation.
 *
 */
class WebRtcHelper {
    //declare function require(moduleName: string)
    static EmitAdapter() {
        if (WebRtcHelper.sAdapterActive == false) {
            console.debug("loading webrtc-adapter");
            let adapter = __webpack_require__(/*! webrtc-adapter */ "./node_modules/webrtc-adapter/src/js/adapter_core.js");
            WebRtcHelper.sAdapterActive = true;
        }
    }
}
WebRtcHelper.sAdapterActive = false;
class Queue {
    constructor() {
        this.mArr = new Array();
    }
    Enqueue(val) {
        this.mArr.push(val);
    }
    TryDequeue(outp) {
        var res = false;
        if (this.mArr.length > 0) {
            outp.val = this.mArr.shift();
            res = true;
        }
        return res;
    }
    Dequeue() {
        if (this.mArr.length > 0) {
            return this.mArr.shift();
        }
        else {
            return null;
        }
    }
    Peek() {
        if (this.mArr.length > 0) {
            return this.mArr[0];
        }
        else {
            return null;
        }
    }
    Count() {
        return this.mArr.length;
    }
    Clear() {
        this.mArr = new Array();
    }
}
class List {
    get Internal() {
        return this.mArr;
    }
    constructor() {
        this.mArr = new Array();
    }
    Add(val) {
        this.mArr.push(val);
    }
    get Count() {
        return this.mArr.length;
    }
}
class Output {
}
class SLogger {
    get Prefix() {
        return this.mPrefix;
    }
    set Prefix(prefix) {
        this.mPrefix = prefix;
    }
    constructor(prefix) {
        this.mPrefix = prefix;
    }
    CreateSub(subPrefix) {
        return new SLogger(this.mPrefix + "." + subPrefix);
    }
    LV(txt) {
        SLog.L(this.mPrefix + ": " + txt);
    }
    L(txt) {
        SLog.L(this.mPrefix + ": " + txt);
    }
    LW(txt) {
        SLog.LW(this.mPrefix + ": " + txt);
    }
    LE(txt) {
        SLog.LE(this.mPrefix + ": " + txt);
    }
}
class Debug {
    static Log(s) {
        SLog.Log(s);
    }
    static LogError(s) {
        SLog.LogError(s);
    }
    static LogWarning(s) {
        SLog.LogWarning(s);
    }
}
class Encoder {
}
class UTF16Encoding extends Encoder {
    constructor() {
        super();
    }
    GetBytes(text) {
        return this.stringToBuffer(text);
    }
    GetString(buffer) {
        return this.bufferToString(buffer);
    }
    bufferToString(buffer) {
        let arr = new Uint16Array(buffer.buffer, buffer.byteOffset, buffer.byteLength / 2);
        return String.fromCharCode.apply(null, arr);
    }
    stringToBuffer(str) {
        let buf = new ArrayBuffer(str.length * 2);
        let bufView = new Uint16Array(buf);
        for (var i = 0, strLen = str.length; i < strLen; i++) {
            bufView[i] = str.charCodeAt(i);
        }
        let result = new Uint8Array(buf);
        return result;
    }
}
class Encoding {
    static get UTF16() {
        return new UTF16Encoding();
    }
    constructor() {
    }
}
class Random {
    //value between min and max (not including max)
    static getRandomInt(min, max) {
        min = Math.ceil(min);
        max = Math.floor(max);
        return Math.floor(Math.random() * (max - min)) + min;
    }
}
class Helper {
    static tryParseInt(value) {
        try {
            if (/^(\-|\+)?([0-9]+)$/.test(value)) {
                let result = Number(value);
                if (isNaN(result) == false)
                    return result;
            }
        }
        catch (e) {
        }
        return null;
    }
}
var SLogLevel;
(function (SLogLevel) {
    SLogLevel[SLogLevel["Verbose"] = 0] = "Verbose";
    SLogLevel[SLogLevel["Info"] = 1] = "Info";
    SLogLevel[SLogLevel["Warnings"] = 2] = "Warnings";
    SLogLevel[SLogLevel["Errors"] = 3] = "Errors";
    SLogLevel[SLogLevel["None"] = 4] = "None";
})(SLogLevel || (SLogLevel = {}));
//Simplified logger
class SLog {
    // Method to set the time prefix feature state
    static SetTimePrefix(enabled) {
        SLog.timePrefixEnabled = enabled;
        if (enabled) {
            SLog.startTime = Date.now(); // Set the start time when enabling
        }
        else {
            SLog.startTime = null; // Reset the start time when disabling
        }
    }
    // Method to format log message with time prefix if enabled
    static formatMessage(msg) {
        if (SLog.timePrefixEnabled) {
            let currentTime = Date.now();
            let timeElapsed = currentTime - SLog.startTime;
            return `[${timeElapsed} ms] ${msg}`;
        }
        else {
            return msg;
        }
    }
    static SetLogLevel(level) {
        SLog.sLogLevel = level;
        SLog.L("Log level set to: " + level);
    }
    static RequestLogLevel(level) {
        if (level > SLog.sLogLevel)
            SLog.sLogLevel = level;
    }
    static L(msg, tag) {
        SLog.Log(msg, tag);
    }
    static LW(msg, tag) {
        SLog.LogWarning(msg, tag);
    }
    static LE(msg, tag) {
        SLog.LogError(msg, tag);
    }
    static Log(msg, tag) {
        if (SLog.sLogLevel <= SLogLevel.Info) {
            msg = SLog.formatMessage(msg);
            if (tag) {
                console.log(msg, tag);
            }
            else {
                console.log(msg);
            }
        }
    }
    static LogWarning(msg, tag) {
        if (!tag)
            tag = "";
        if (SLog.sLogLevel <= SLogLevel.Warnings) {
            msg = SLog.formatMessage(msg);
            if (tag) {
                console.warn(msg, tag);
            }
            else {
                console.warn(msg);
            }
        }
    }
    static LogError(msg, tag) {
        if (SLog.sLogLevel <= SLogLevel.Errors) {
            msg = SLog.formatMessage(msg);
            if (tag) {
                console.error(msg, tag);
            }
            else {
                console.error(msg);
            }
        }
    }
}
SLog.sLogLevel = SLogLevel.Warnings;
SLog.timePrefixEnabled = false;


/***/ }),

/***/ "./src/awrtc/network/INetwork.ts":
/*!***************************************!*\
  !*** ./src/awrtc/network/INetwork.ts ***!
  \***************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   ConnectionId: () => (/* binding */ ConnectionId),
/* harmony export */   NetEventDataType: () => (/* binding */ NetEventDataType),
/* harmony export */   NetEventType: () => (/* binding */ NetEventType),
/* harmony export */   NetworkEvent: () => (/* binding */ NetworkEvent)
/* harmony export */ });
/* harmony import */ var _Helper__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./Helper */ "./src/awrtc/network/Helper.ts");
/*
Copyright (c) 2023, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
/** Abstract interfaces and serialization to keep different
 * versions compatible to each other.
 *
 * Watch out before changing anything in this file. Content is reused
 * between webclient, signaling server and needs to remain compatible to
 * the C# implementation.
 */

var NetEventType;
(function (NetEventType) {
    NetEventType[NetEventType["Invalid"] = 0] = "Invalid";
    NetEventType[NetEventType["UnreliableMessageReceived"] = 1] = "UnreliableMessageReceived";
    NetEventType[NetEventType["ReliableMessageReceived"] = 2] = "ReliableMessageReceived";
    NetEventType[NetEventType["ServerInitialized"] = 3] = "ServerInitialized";
    NetEventType[NetEventType["ServerInitFailed"] = 4] = "ServerInitFailed";
    NetEventType[NetEventType["ServerClosed"] = 5] = "ServerClosed";
    NetEventType[NetEventType["NewConnection"] = 6] = "NewConnection";
    NetEventType[NetEventType["ConnectionFailed"] = 7] = "ConnectionFailed";
    NetEventType[NetEventType["Disconnected"] = 8] = "Disconnected";
    NetEventType[NetEventType["FatalError"] = 100] = "FatalError";
    NetEventType[NetEventType["Warning"] = 101] = "Warning";
    NetEventType[NetEventType["Log"] = 102] = "Log";
    /// <summary>
    /// This value and higher are reserved for other uses. 
    /// Should never get to the user and should be filtered out.
    /// </summary>
    NetEventType[NetEventType["ReservedStart"] = 200] = "ReservedStart";
    /// <summary>
    /// Reserved.
    /// Used by protocols that forward NetworkEvents
    /// </summary>
    NetEventType[NetEventType["MetaVersion"] = 201] = "MetaVersion";
    /// <summary>
    /// Reserved.
    /// Used by protocols that forward NetworkEvents.
    /// </summary>
    NetEventType[NetEventType["MetaHeartbeat"] = 202] = "MetaHeartbeat";
})(NetEventType || (NetEventType = {}));
var NetEventDataType;
(function (NetEventDataType) {
    NetEventDataType[NetEventDataType["Null"] = 0] = "Null";
    NetEventDataType[NetEventDataType["ByteArray"] = 1] = "ByteArray";
    NetEventDataType[NetEventDataType["UTF16String"] = 2] = "UTF16String";
})(NetEventDataType || (NetEventDataType = {}));
class NetworkEvent {
    constructor(t, conId, data) {
        this.type = t;
        this.connectionId = conId;
        this.data = data;
    }
    get RawData() {
        return this.data;
    }
    get MessageData() {
        if (typeof this.data != "string")
            return this.data;
        return null;
    }
    get Info() {
        if (typeof this.data == "string")
            return this.data;
        return null;
    }
    get Type() {
        return this.type;
    }
    get ConnectionId() {
        return this.connectionId;
    }
    //for debugging only
    toString() {
        let output = "NetworkEvent[";
        output += "NetEventType: (";
        output += NetEventType[this.type];
        output += "), id: (";
        output += this.connectionId.id;
        output += "), Data: (";
        if (typeof this.data == "string") {
            output += this.data;
        }
        output += ")]";
        return output;
    }
    static parseFromString(str) {
        let values = JSON.parse(str);
        let data;
        if (values.data == null) {
            data = null;
        }
        else if (typeof values.data == "string") {
            data = values.data;
        }
        else if (typeof values.data == "object") {
            //json represents the array as an object containing each index and the
            //value as string number ... improve that later
            let arrayAsObject = values.data;
            var length = 0;
            for (var prop in arrayAsObject) {
                //if (arrayAsObject.hasOwnProperty(prop)) { //shouldnt be needed
                length++;
                //}
            }
            let buffer = new Uint8Array(Object.keys(arrayAsObject).length);
            for (let i = 0; i < buffer.length; i++)
                buffer[i] = arrayAsObject[i];
            data = buffer;
        }
        else {
            _Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.LogError("network event can't be parsed: " + str);
        }
        var evt = new NetworkEvent(values.type, values.connectionId, data);
        return evt;
    }
    static toString(evt) {
        return JSON.stringify(evt);
    }
    static fromByteArray(arrin) {
        //old node js versions seem to not return proper Uint8Arrays but
        //buffers -> make sure it is a Uint8Array
        let arr = new Uint8Array(arrin);
        let type = arr[0]; //byte
        let dataType = arr[1]; //byte
        let id = new Int16Array(arr.buffer, arr.byteOffset + 2, 1)[0]; //short
        let data = null;
        if (dataType == NetEventDataType.ByteArray) {
            let length = new Uint32Array(arr.buffer, arr.byteOffset + 4, 1)[0]; //uint
            let byteArray = new Uint8Array(arr.buffer, arr.byteOffset + 8, length);
            data = byteArray;
        }
        else if (dataType == NetEventDataType.UTF16String) {
            let length = new Uint32Array(arr.buffer, arr.byteOffset + 4, 1)[0]; //uint
            let uint16Arr = new Uint16Array(arr.buffer, arr.byteOffset + 8, length);
            let str = "";
            for (let i = 0; i < uint16Arr.length; i++) {
                str += String.fromCharCode(uint16Arr[i]);
            }
            data = str;
        }
        else if (dataType == NetEventDataType.Null) {
            //message has no data
        }
        else {
            throw new Error('Message has an invalid data type flag: ' + dataType);
        }
        let conId = new ConnectionId(id);
        let result = new NetworkEvent(type, conId, data);
        return result;
    }
    static toByteArray(evt) {
        let dataType;
        let length = 4; //4 bytes are always needed
        //getting type and length
        if (evt.data == null) {
            dataType = NetEventDataType.Null;
        }
        else if (typeof evt.data == "string") {
            dataType = NetEventDataType.UTF16String;
            let str = evt.data;
            length += str.length * 2 + 4;
        }
        else {
            dataType = NetEventDataType.ByteArray;
            let byteArray = evt.data;
            length += 4 + byteArray.length;
        }
        //creating the byte array
        let result = new Uint8Array(length);
        result[0] = evt.type;
        ;
        result[1] = dataType;
        let conIdField = new Int16Array(result.buffer, result.byteOffset + 2, 1);
        conIdField[0] = evt.connectionId.id;
        if (dataType == NetEventDataType.ByteArray) {
            let byteArray = evt.data;
            let lengthField = new Uint32Array(result.buffer, result.byteOffset + 4, 1);
            lengthField[0] = byteArray.length;
            for (let i = 0; i < byteArray.length; i++) {
                result[8 + i] = byteArray[i];
            }
        }
        else if (dataType == NetEventDataType.UTF16String) {
            let str = evt.data;
            let lengthField = new Uint32Array(result.buffer, result.byteOffset + 4, 1);
            lengthField[0] = str.length;
            let dataField = new Uint16Array(result.buffer, result.byteOffset + 8, str.length);
            for (let i = 0; i < dataField.length; i++) {
                dataField[i] = str.charCodeAt(i);
            }
        }
        return result;
    }
}
class ConnectionId {
    constructor(nid) {
        this.id = nid;
    }
}
ConnectionId.INVALID = new ConnectionId(-1);


/***/ }),

/***/ "./src/awrtc/network/IWebRtcNetwork.ts":
/*!*********************************************!*\
  !*** ./src/awrtc/network/IWebRtcNetwork.ts ***!
  \*********************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   RtcEvent: () => (/* binding */ RtcEvent),
/* harmony export */   RtcEventType: () => (/* binding */ RtcEventType),
/* harmony export */   StatsEvent: () => (/* binding */ StatsEvent)
/* harmony export */ });
var RtcEventType;
(function (RtcEventType) {
    RtcEventType[RtcEventType["Invalid"] = 0] = "Invalid";
    //do not change. must match C# side.
    RtcEventType[RtcEventType["Stats"] = 10] = "Stats";
    //1000 and below can be used for java script side features. will be ignored by C#
    RtcEventType[RtcEventType["StreamAdded"] = 1000] = "StreamAdded";
})(RtcEventType || (RtcEventType = {}));
/**Used to expose WebRTC specific events.
 * Unlike NetworkEvent these are less standardized and contain browser specific events.
 */
class RtcEvent {
    get EventType() {
        return this.mType;
    }
    get ConnectionId() {
        return this.mId;
    }
    constructor(tp, id) {
        this.mType = tp;
        this.mId = id;
    }
}
class StatsEvent extends RtcEvent {
    get Reports() {
        return this.mReports;
    }
    constructor(id, reports) {
        super(RtcEventType.Stats, id);
        this.mReports = reports;
    }
}
//export {NetEventType, NetworkEvent, ConnectionId, INetwork, IBasicNetwork};


/***/ }),

/***/ "./src/awrtc/network/LocalNetwork.ts":
/*!*******************************************!*\
  !*** ./src/awrtc/network/LocalNetwork.ts ***!
  \*******************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   LocalNetwork: () => (/* binding */ LocalNetwork)
/* harmony export */ });
/* harmony import */ var _index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./index */ "./src/awrtc/network/index.ts");
/* harmony import */ var _Helper__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./Helper */ "./src/awrtc/network/Helper.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/


/**Helper to simulate the WebsocketNetwork or WebRtcNetwork
 * within a local application without
 * any actual network components.
 *
 * This implementation might lack some features.
 */
class LocalNetwork {
    constructor() {
        this.mNextNetworkId = new _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(1);
        this.mServerAddress = null;
        this.mEvents = new _Helper__WEBPACK_IMPORTED_MODULE_1__.Queue();
        this.mConnectionNetwork = {};
        this.mIsDisposed = false;
        this.mId = LocalNetwork.sNextId;
        LocalNetwork.sNextId++;
    }
    get IsServer() {
        return this.mServerAddress != null;
    }
    StartServer(serverAddress = null) {
        if (serverAddress == null)
            serverAddress = "" + this.mId;
        if (serverAddress in LocalNetwork.mServers) {
            this.Enqueue(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitFailed, _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, serverAddress);
            return;
        }
        LocalNetwork.mServers[serverAddress] = this;
        this.mServerAddress = serverAddress;
        this.Enqueue(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitialized, _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, serverAddress);
    }
    StopServer() {
        if (this.IsServer) {
            this.Enqueue(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerClosed, _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, this.mServerAddress);
            delete LocalNetwork.mServers[this.mServerAddress];
            this.mServerAddress = null;
        }
    }
    Connect(address) {
        var connectionId = this.NextConnectionId();
        var sucessful = false;
        if (address in LocalNetwork.mServers) {
            let server = LocalNetwork.mServers[address];
            if (server != null) {
                server.ConnectClient(this);
                //add the server as local connection
                this.mConnectionNetwork[connectionId.id] = LocalNetwork.mServers[address];
                this.Enqueue(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection, connectionId, null);
                sucessful = true;
            }
        }
        if (sucessful == false) {
            this.Enqueue(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ConnectionFailed, connectionId, "Couldn't connect to the given server with id " + address);
        }
        return connectionId;
    }
    Shutdown() {
        for (var id in this.mConnectionNetwork) //can be changed while looping?
         {
            this.Disconnect(new _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(+id));
        }
        //this.mConnectionNetwork.Clear();
        this.StopServer();
    }
    Dispose() {
        if (this.mIsDisposed == false) {
            this.Shutdown();
        }
    }
    SendData(userId, data, reliable) {
        if (userId.id in this.mConnectionNetwork) {
            let net = this.mConnectionNetwork[userId.id];
            net.ReceiveData(this, data, reliable);
            return true;
        }
        return false;
    }
    Update() {
        //work around for the GarbageCollection bug
        //usually weak references are removed during garbage collection but that
        //fails sometimes as others weak references get null to even though
        //the objects still exist!
        this.CleanupWreakReferences();
    }
    Dequeue() {
        return this.mEvents.Dequeue();
    }
    Peek() {
        return this.mEvents.Peek();
    }
    Flush() {
    }
    Disconnect(id) {
        if (id.id in this.mConnectionNetwork) {
            let other = this.mConnectionNetwork[id.id];
            if (other != null) {
                other.InternalDisconnectNetwork(this);
                this.InternalDisconnect(id);
            }
            else {
                //this is suppose to never happen but it does
                //if a server is destroyed by the garbage collector the client
                //weak reference appears to be NULL even though it still exists
                //bug?
                this.CleanupWreakReferences();
            }
        }
    }
    FindConnectionId(network) {
        for (var kvp in this.mConnectionNetwork) {
            let network = this.mConnectionNetwork[kvp];
            if (network != null) {
                return new _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(+kvp);
            }
        }
        return _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID;
    }
    NextConnectionId() {
        let res = this.mNextNetworkId;
        this.mNextNetworkId = new _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(res.id + 1);
        return res;
    }
    ConnectClient(client) {
        //if (this.IsServer == false)
        //    throw new InvalidOperationException();
        let nextId = this.NextConnectionId();
        //server side only
        this.mConnectionNetwork[nextId.id] = client;
        this.Enqueue(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection, nextId, null);
    }
    Enqueue(type, id, data) {
        let ev = new _index__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(type, id, data);
        this.mEvents.Enqueue(ev);
    }
    ReceiveData(network, data, reliable) {
        let userId = this.FindConnectionId(network);
        let buffer = new Uint8Array(data.length);
        for (let i = 0; i < buffer.length; i++) {
            buffer[i] = data[i];
        }
        let type = _index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.UnreliableMessageReceived;
        if (reliable)
            type = _index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ReliableMessageReceived;
        this.Enqueue(type, userId, buffer);
    }
    InternalDisconnect(id) {
        if (id.id in this.mConnectionNetwork) {
            this.Enqueue(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.Disconnected, id, null);
            delete this.mConnectionNetwork[id.id];
        }
    }
    InternalDisconnectNetwork(ln) {
        //if it can't be found it will return invalid which is ignored in internal disconnect
        this.InternalDisconnect(this.FindConnectionId(ln));
    }
    CleanupWreakReferences() {
        //foreach(var kvp in mConnectionNetwork.Keys.ToList())
        //{
        //    var val = mConnectionNetwork[kvp];
        //    if (val.Get() == null) {
        //        InternalDisconnect(kvp);
        //    }
        //}
    }
}
LocalNetwork.sNextId = 1;
LocalNetwork.mServers = {};


/***/ }),

/***/ "./src/awrtc/network/NetworkConfig.ts":
/*!********************************************!*\
  !*** ./src/awrtc/network/NetworkConfig.ts ***!
  \********************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   NetworkConfig: () => (/* binding */ NetworkConfig)
/* harmony export */ });
/* harmony import */ var _index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./index */ "./src/awrtc/network/index.ts");
/*
Copyright (c) 2022, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

class NetworkConfig {
    constructor() {
        this.mIceServers = new Array();
        this.mSignalingUrl = null;
        this.mIsConference = false;
        this.mMaxIceRestart = 0;
        this.mKeepSignalingAlive = false;
        this.mSignalingNetwork = null;
    }
    get IceServers() {
        return this.mIceServers;
    }
    set IceServers(value) {
        this.mIceServers = value;
    }
    get SignalingUrl() {
        return this.mSignalingUrl;
    }
    set SignalingUrl(value) {
        this.mSignalingUrl = value;
    }
    get IsConference() {
        return this.mIsConference;
    }
    set IsConference(value) {
        this.mIsConference = value;
    }
    get MaxIceRestart() {
        if (this.mKeepSignalingAlive == false)
            return 0;
        return this.mMaxIceRestart;
    }
    set MaxIceRestart(value) {
        this.mMaxIceRestart = value;
    }
    get KeepSignalingAlive() {
        return this.mKeepSignalingAlive;
    }
    set KeepSignalingAlive(value) {
        this.mKeepSignalingAlive = value;
    }
    get SignalingNetwork() {
        return this.mSignalingNetwork;
    }
    set SignalingNetwork(value) {
        this.mSignalingNetwork = value;
    }
    //Either returns the signaling network set by the user
    //or creates a new one based on the set URL
    //TODO: Move this class to a factory
    GetOrCreateSignalingNetwork() {
        //create or reuse existing network instance
        if (this.mSignalingNetwork)
            return this.mSignalingNetwork;
        let res = null;
        if (this.mSignalingUrl == null || this.mSignalingUrl == "") {
            res = new _index__WEBPACK_IMPORTED_MODULE_0__.LocalNetwork();
        }
        else {
            res = new _index__WEBPACK_IMPORTED_MODULE_0__.WebsocketNetwork(this.mSignalingUrl);
        }
        return res;
    }
    BuildRtcConfig() {
        let rtcConfig = { iceServers: this.IceServers };
        return rtcConfig;
    }
    Clone() {
        const res = new NetworkConfig();
        return this.CloneTo(res);
    }
    CloneTo(res) {
        res.mIceServers = [].concat(this.mIceServers);
        res.mIsConference = this.mIsConference;
        res.mKeepSignalingAlive = this.mKeepSignalingAlive;
        res.mMaxIceRestart = this.mMaxIceRestart;
        res.mSignalingUrl = this.mSignalingUrl;
        //watch out this is the only component that can not be properly deep copied
        res.mSignalingNetwork = this.mSignalingNetwork;
        return res;
    }
    FromJson(json) {
        const jsobj = JSON.parse(json);
        Object.assign(this, jsobj);
    }
    IsEqual(other) {
        if (!other)
            return false;
        const ownj = JSON.stringify(this);
        const otherj = JSON.stringify(other);
        if (ownj != otherj)
            return false;
        return true;
    }
    ToString() {
        return JSON.stringify(this);
    }
}


/***/ }),

/***/ "./src/awrtc/network/WebRtcNetwork.ts":
/*!********************************************!*\
  !*** ./src/awrtc/network/WebRtcNetwork.ts ***!
  \********************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   WebRtcNetwork: () => (/* binding */ WebRtcNetwork),
/* harmony export */   WebRtcNetworkServerState: () => (/* binding */ WebRtcNetworkServerState)
/* harmony export */ });
/* harmony import */ var _index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./index */ "./src/awrtc/network/index.ts");
/* harmony import */ var _Helper__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./Helper */ "./src/awrtc/network/Helper.ts");
/* harmony import */ var _WebRtcPeer__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./WebRtcPeer */ "./src/awrtc/network/WebRtcPeer.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
//import {ConnectionId, NetworkEvent, NetEventType, IBasicNetwork} from './INetwork'



var WebRtcNetworkServerState;
(function (WebRtcNetworkServerState) {
    WebRtcNetworkServerState[WebRtcNetworkServerState["Invalid"] = 0] = "Invalid";
    WebRtcNetworkServerState[WebRtcNetworkServerState["Offline"] = 1] = "Offline";
    WebRtcNetworkServerState[WebRtcNetworkServerState["Starting"] = 2] = "Starting";
    WebRtcNetworkServerState[WebRtcNetworkServerState["Online"] = 3] = "Online";
})(WebRtcNetworkServerState || (WebRtcNetworkServerState = {}));
/// <summary>
/// Native version of WebRtc
/// 
/// Make sure to use Shutdown before unity quits! (unity will probably get stuck without it)
/// 
/// 
/// </summary>
class WebRtcNetwork {
    get IdToConnection() {
        return this.mIdToConnection;
    }
    //only for internal use
    GetConnections() {
        return this.mConnectionIds;
    }
    get NetworkConfig() {
        return this.mNetConfig.Clone();
    }
    //public
    constructor(config) {
        this.mTimeout = 60000;
        this.mInSignaling = {};
        //private mNextId: ConnectionId = new ConnectionId(1);
        //Buffer for network events (those that conform the cross platform IBasicNetwork api)
        this.mEvents = new _Helper__WEBPACK_IMPORTED_MODULE_1__.Queue();
        //Buffer for WebRTC specific events
        this.mRtcEvents = new _Helper__WEBPACK_IMPORTED_MODULE_1__.Queue();
        this.mIdToConnection = {};
        //must be the same as the hashmap and later returned read only (avoids copies)
        this.mConnectionIds = new Array();
        this.mServerState = WebRtcNetworkServerState.Offline;
        this.mIsDisposed = false;
        this.mId = WebRtcNetwork.sNextId++;
        this.log = new _Helper__WEBPACK_IMPORTED_MODULE_1__.SLogger("WebRtcNetwork" + this.mId);
        this.mNetConfig = config.Clone();
        this.log.L("Creating using NetworkConfig: " + this.mNetConfig.ToString());
        this.mSignalingNetwork = this.mNetConfig.GetOrCreateSignalingNetwork();
    }
    StartServer(address) {
        this.StartServerInternal(address);
    }
    StartServerInternal(address) {
        this.mServerState = WebRtcNetworkServerState.Starting;
        this.mSignalingNetwork.StartServer(address);
    }
    StopServer() {
        if (this.mServerState == WebRtcNetworkServerState.Starting) {
            this.mSignalingNetwork.StopServer();
            //removed. the underlaying sygnaling network should set those values
            //this.mServerState = WebRtcNetworkServerState.Offline;
            //this.mEvents.Enqueue(new NetworkEvent(NetEventType.ServerInitFailed, ConnectionId.INVALID, null));
        }
        else if (this.mServerState == WebRtcNetworkServerState.Online) {
            //dont wait for confirmation
            this.mSignalingNetwork.StopServer();
            //removed. the underlaying sygnaling network should set those values
            //this.mServerState = WebRtcNetworkServerState.Offline;
            //this.mEvents.Enqueue(new NetworkEvent(NetEventType.ServerClosed, ConnectionId.INVALID, null));
        }
    }
    Connect(address) {
        return this.AddOutgoingConnection(address);
    }
    Update() {
        this.CheckSignalingState();
        this.UpdateSignalingNetwork();
        this.UpdatePeers();
    }
    Dequeue() {
        if (this.mEvents.Count() > 0)
            return this.mEvents.Dequeue();
        return null;
    }
    Peek() {
        if (this.mEvents.Count() > 0)
            return this.mEvents.Peek();
        return null;
    }
    Flush() {
        this.mSignalingNetwork.Flush();
        this.mRtcEvents.Clear();
    }
    SendData(id, data /*, offset : number, length : number*/, reliable) {
        if (id == null || data == null || data.length == 0)
            return;
        let peer = this.mIdToConnection[id.id];
        if (peer) {
            return peer.SendData(data, /* offset, length,*/ reliable);
        }
        else {
            this.log.LW("unknown connection id");
            return false;
        }
    }
    GetBufferedAmount(id, reliable) {
        let peer = this.mIdToConnection[id.id];
        if (peer) {
            return peer.GetBufferedAmount(reliable);
        }
        else {
            this.log.LW("unknown connection id");
            return -1;
        }
    }
    Disconnect(id) {
        let peer = this.mIdToConnection[id.id];
        if (peer) {
            this.HandleDisconnect(id);
        }
    }
    Shutdown() {
        //bugfix. Make copy before the loop as Disconnect changes the original mConnectionIds array
        let ids = this.mConnectionIds.slice();
        for (var id of ids) {
            this.Disconnect(id);
        }
        this.StopServer();
        this.mSignalingNetwork.Shutdown();
    }
    DisposeInternal() {
        if (this.mIsDisposed == false) {
            this.Shutdown();
            this.mIsDisposed = true;
        }
    }
    Dispose() {
        this.DisposeInternal();
    }
    //protected
    CreatePeer(peerId) {
        const peerConfig = new _WebRtcPeer__WEBPACK_IMPORTED_MODULE_2__.PeerConfig(this.mNetConfig);
        let peer = new _index__WEBPACK_IMPORTED_MODULE_0__.WebRtcDataPeer(peerId, peerConfig, this.log);
        return peer;
    }
    //private
    CheckSignalingState() {
        let connected = new Array();
        let failed = new Array();
        //update the signaling channels
        for (let key in this.mInSignaling) {
            let peer = this.mInSignaling[key];
            peer.Update();
            let timeAlive = peer.SignalingInfo.GetCreationTimeMs();
            let msg = new _Helper__WEBPACK_IMPORTED_MODULE_1__.Output();
            while (peer.DequeueSignalingMessage(msg)) {
                let buffer = this.StringToBuffer(msg.val);
                this.mSignalingNetwork.SendData(new _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(+key), buffer, true);
            }
            //trigger new connection event if the peer newly connected
            //watch out: peers now can remain in signaling while already being connected
            if (peer.GetState() == _index__WEBPACK_IMPORTED_MODULE_0__.WebRtcPeerState.Connected && !(peer.SignalingInfo.ConnectionId.id in this.mIdToConnection)) {
                connected.push(peer.SignalingInfo.ConnectionId);
            }
            else if (peer.GetState() == _index__WEBPACK_IMPORTED_MODULE_0__.WebRtcPeerState.SignalingFailed) {
                failed.push(peer.SignalingInfo.ConnectionId);
            }
            else if (timeAlive > this.mTimeout && peer.GetState() == _index__WEBPACK_IMPORTED_MODULE_0__.WebRtcPeerState.Signaling) {
                //stuck in signaling forever -> timeout
                failed.push(peer.SignalingInfo.ConnectionId);
            }
        }
        for (var v of connected) {
            this.ConnectionEstablished(v);
        }
        for (var v of failed) {
            this.SignalingFailed(v);
        }
    }
    UpdateSignalingNetwork() {
        //update the signaling system
        this.mSignalingNetwork.Update();
        let evt;
        while ((evt = this.mSignalingNetwork.Dequeue()) != null) {
            if (evt.Type == _index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitialized) {
                this.mServerState = WebRtcNetworkServerState.Online;
                this.mEvents.Enqueue(new _index__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitialized, _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, evt.RawData));
            }
            else if (evt.Type == _index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitFailed) {
                this.mServerState = WebRtcNetworkServerState.Offline;
                this.mEvents.Enqueue(new _index__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitFailed, _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, evt.RawData));
            }
            else if (evt.Type == _index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerClosed) {
                this.mServerState = WebRtcNetworkServerState.Offline;
                this.mEvents.Enqueue(new _index__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerClosed, _index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, evt.RawData));
            }
            else if (evt.Type == _index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection) {
                //check if new incoming connection or an outgoing was established
                let peer = this.mInSignaling[evt.ConnectionId.id];
                if (peer) {
                    peer.StartSignaling();
                }
                else {
                    this.AddIncomingConnection(evt.ConnectionId);
                }
            }
            else if (evt.Type == _index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ConnectionFailed) {
                //Outgoing connection failed
                this.SignalingFailed(evt.ConnectionId);
            }
            else if (evt.Type == _index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.Disconnected) {
                let peer = this.mInSignaling[evt.ConnectionId.id];
                if (peer) {
                    peer.SignalingInfo.SignalingDisconnected();
                    //if mKeepSignaling we treat signaling as part of the connection
                    //so if signaling fails & there was an active connection we 
                    //report this as a complete disconnect and cleanup the peer
                    if (this.mNetConfig.KeepSignalingAlive) {
                        let connectedPeer = this.mIdToConnection[evt.ConnectionId.id];
                        if (connectedPeer)
                            this.HandleDisconnect(evt.ConnectionId);
                    }
                }
                //if signaling was completed this isn't a problem
                //SignalingDisconnected(evt.ConnectionId);
                //do nothing. either webrtc has enough information to connect already
                //or it will wait forever for the information -> after 30 sec we give up
            }
            else if (evt.Type == _index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ReliableMessageReceived) {
                //TODO: merge mInSignaling & mIdToConnection. Peers can receive signaling messages
                //even if they are already connected
                let peer = this.mInSignaling[evt.ConnectionId.id] || this.mIdToConnection[evt.ConnectionId.id];
                if (peer) {
                    let msg = this.BufferToString(evt.MessageData);
                    peer.AddSignalingMessage(msg);
                }
                else {
                    this.log.L("No peer found for id " + evt.ConnectionId.id + ". Dropped signaling message.");
                }
            }
        }
    }
    UpdatePeers() {
        //every peer has a queue storing incoming messages to avoid multi threading problems -> handle it now
        let disconnected = new Array();
        for (var key in this.mIdToConnection) {
            var peer = this.mIdToConnection[key];
            peer.Update();
            let ev = new _Helper__WEBPACK_IMPORTED_MODULE_1__.Output();
            while (peer.DequeueEvent(/*out*/ ev)) {
                this.mEvents.Enqueue(ev.val);
            }
            if (peer.GetState() == _index__WEBPACK_IMPORTED_MODULE_0__.WebRtcPeerState.Closed) {
                disconnected.push(peer.ConnectionId);
            }
        }
        for (let key of disconnected) {
            this.HandleDisconnect(key);
        }
    }
    AddOutgoingConnection(address) {
        let signalingConId = this.mSignalingNetwork.Connect(address);
        this.log.L("new outgoing connection");
        let info = new _index__WEBPACK_IMPORTED_MODULE_0__.SignalingInfo(signalingConId, false, Date.now());
        let peer = this.CreatePeer(signalingConId);
        peer.SetSignalingInfo(info);
        this.mInSignaling[signalingConId.id] = peer;
        return peer.ConnectionId;
    }
    AddIncomingConnection(signalingConId) {
        this.log.L("new incoming connection");
        let info = new _index__WEBPACK_IMPORTED_MODULE_0__.SignalingInfo(signalingConId, true, Date.now());
        let peer = this.CreatePeer(signalingConId);
        peer.SetSignalingInfo(info);
        this.mInSignaling[signalingConId.id] = peer;
        //passive way of starting signaling -> send out random number. if the other one does the same
        //the one with the highest number starts signaling
        peer.NegotiateSignaling();
        return peer.ConnectionId;
    }
    ConnectionEstablished(signalingConId) {
        let peer = this.mInSignaling[signalingConId.id];
        //delete this.mInSignaling[signalingConId.id];
        //this.mSignalingNetwork.Disconnect(signalingConId);
        if (this.mNetConfig.KeepSignalingAlive === false)
            this.RemoveSignalingConnection(signalingConId);
        this.mConnectionIds.push(peer.ConnectionId);
        this.mIdToConnection[peer.ConnectionId.id] = peer;
        this.mEvents.Enqueue(new _index__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection, peer.ConnectionId, null));
    }
    //removes and disconnects the signaling connection associated with the given id
    //if the id exists. if not continues without error
    RemoveSignalingConnection(signalingConId) {
        let peer = this.mInSignaling[signalingConId.id];
        if (peer) {
            //connection was still believed to be in signaling -> notify the user of the event
            delete this.mInSignaling[signalingConId.id];
            if (peer.SignalingInfo.IsSignalingConnected()) {
                this.mSignalingNetwork.Disconnect(signalingConId);
            }
        }
    }
    SignalingFailed(signalingConId) {
        let peer = this.mInSignaling[signalingConId.id];
        if (peer) {
            this.RemoveSignalingConnection(signalingConId);
            this.mEvents.Enqueue(new _index__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ConnectionFailed, peer.ConnectionId, null));
            peer.Dispose();
        }
    }
    HandleDisconnect(id) {
        let peer = this.mIdToConnection[id.id];
        if (peer) {
            peer.Dispose();
        }
        //search for the index to remove the id (user might provide a different object with the same id
        //don't use indexOf!
        let index = this.mConnectionIds.findIndex(e => e.id == id.id);
        if (index != -1) {
            this.mConnectionIds.splice(index, 1);
            delete this.mIdToConnection[id.id];
        }
        this.RemoveSignalingConnection(id);
        let ev = new _index__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.Disconnected, id, null);
        this.mEvents.Enqueue(ev);
    }
    /*
    Removed. Peers now use the same id as signaling
    private NextConnectionId(): ConnectionId {
        let id = new ConnectionId(this.mNextId.id);
        this.mNextId.id++;
        return id;
    }
    */
    StringToBuffer(str) {
        let buf = new ArrayBuffer(str.length * 2);
        let bufView = new Uint16Array(buf);
        for (var i = 0, strLen = str.length; i < strLen; i++) {
            bufView[i] = str.charCodeAt(i);
        }
        let result = new Uint8Array(buf);
        return result;
    }
    BufferToString(buffer) {
        let arr = new Uint16Array(buffer.buffer, buffer.byteOffset, buffer.byteLength / 2);
        return String.fromCharCode.apply(null, arr);
    }
    RequestStats() {
        for (var key in this.mIdToConnection) {
            var peer = this.mIdToConnection[key];
            peer.RequestStats();
        }
    }
    EnqueueRtcEvent(evt) {
        this.mRtcEvents.Enqueue(evt);
    }
    DequeueRtcEvent() {
        if (this.mRtcEvents.Count() > 0)
            return this.mRtcEvents.Dequeue();
        //check the peers for their events
        //TODO: add to buffered
        for (var key in this.mIdToConnection) {
            var peer = this.mIdToConnection[key];
            const v = peer.DequeueRtcEvent();
            if (v)
                return v;
        }
        return null;
    }
}
WebRtcNetwork.sNextId = 0;


/***/ }),

/***/ "./src/awrtc/network/WebRtcPeer.ts":
/*!*****************************************!*\
  !*** ./src/awrtc/network/WebRtcPeer.ts ***!
  \*****************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   AWebRtcPeer: () => (/* binding */ AWebRtcPeer),
/* harmony export */   PeerConfig: () => (/* binding */ PeerConfig),
/* harmony export */   SignalingInfo: () => (/* binding */ SignalingInfo),
/* harmony export */   WebRtcDataPeer: () => (/* binding */ WebRtcDataPeer),
/* harmony export */   WebRtcInternalState: () => (/* binding */ WebRtcInternalState),
/* harmony export */   WebRtcPeerState: () => (/* binding */ WebRtcPeerState)
/* harmony export */ });
/* harmony import */ var _index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./index */ "./src/awrtc/network/index.ts");
/* harmony import */ var _Helper__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./Helper */ "./src/awrtc/network/Helper.ts");
/* harmony import */ var _IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./IWebRtcNetwork */ "./src/awrtc/network/IWebRtcNetwork.ts");
var __awaiter = (undefined && undefined.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/



class SignalingInfo {
    IsSignalingConnected() {
        return this.mSignalingConnected;
    }
    get ConnectionId() {
        return this.mConnectionId;
    }
    IsIncoming() {
        return this.mIsIncoming;
    }
    GetCreationTimeMs() {
        return Date.now() - this.mCreationTime;
    }
    constructor(id, isIncoming, timeStamp) {
        this.mConnectionId = id;
        this.mIsIncoming = isIncoming;
        this.mCreationTime = timeStamp;
        this.mSignalingConnected = true;
    }
    SignalingDisconnected() {
        this.mSignalingConnected = false;
    }
}
var WebRtcPeerState;
(function (WebRtcPeerState) {
    WebRtcPeerState[WebRtcPeerState["Invalid"] = 0] = "Invalid";
    WebRtcPeerState[WebRtcPeerState["Created"] = 1] = "Created";
    WebRtcPeerState[WebRtcPeerState["Signaling"] = 2] = "Signaling";
    WebRtcPeerState[WebRtcPeerState["SignalingFailed"] = 3] = "SignalingFailed";
    WebRtcPeerState[WebRtcPeerState["Connected"] = 4] = "Connected";
    WebRtcPeerState[WebRtcPeerState["Closing"] = 5] = "Closing";
    WebRtcPeerState[WebRtcPeerState["Closed"] = 6] = "Closed"; //either Closed call finished or closed remotely or Cleanup/Dispose finished -> peer connection is destroyed and all resources are released
})(WebRtcPeerState || (WebRtcPeerState = {}));
var WebRtcInternalState;
(function (WebRtcInternalState) {
    WebRtcInternalState[WebRtcInternalState["None"] = 0] = "None";
    WebRtcInternalState[WebRtcInternalState["Signaling"] = 1] = "Signaling";
    WebRtcInternalState[WebRtcInternalState["SignalingFailed"] = 2] = "SignalingFailed";
    WebRtcInternalState[WebRtcInternalState["Connected"] = 3] = "Connected";
    WebRtcInternalState[WebRtcInternalState["Closed"] = 4] = "Closed"; //at least one channel was closed
})(WebRtcInternalState || (WebRtcInternalState = {}));
class PeerConfig {
    constructor(netConfig) {
        this.MaxIceRestart = netConfig.MaxIceRestart;
        this.RtcConfig = netConfig.BuildRtcConfig();
    }
}
class AWebRtcPeer {
    GetState() {
        return this.mState;
    }
    constructor(peerConfig, baseLogger) {
        //activates additional log messages
        this.DEBUG = false;
        this.LOG_SIGNALING = true;
        //TODO: Check for better handling of ice restart & data channels on firefox
        //on chrome data channels remain open during ice failed and continue to work after ice restart
        //on firefox they (sometimes) permanently close and never recover
        //for now we simply ignore the closed event which would usually trigger a complete closure of this peer
        //Only active if MAX_RETRIES > 0
        //This is off by default for now meaning ice restart won't work reliably if firefox is involved
        this.USE_ICE_RESTART_DC_WORKAROUND = false;
        this.MAX_RETRIES = 2;
        this.mRetries = 0;
        //private mReconnectInterval?: ReturnType<typeof setTimeout> = null;
        this.mState = WebRtcPeerState.Invalid;
        //private mReconnectInterval?: ReturnType<typeof setTimeout> = null;
        //only written during webrtc callbacks
        this.mRtcInternalState = WebRtcInternalState.None;
        this.mIncomingSignalingQueue = new _Helper__WEBPACK_IMPORTED_MODULE_1__.Queue();
        this.mOutgoingSignalingQueue = new _Helper__WEBPACK_IMPORTED_MODULE_1__.Queue();
        /**new experimental peer configuration: It tries to avoid negotiation to allow cutting the signaling connection
         * For this transceivers are always created with sendrecv even if no track is available yet.
         * Later replaceTrack is used to attach / detach any tracks
         * This causes a bug in browsers though: If an audio track is active but no video track yet audio playback does not start
         */
        this.SINGLE_NEGOTIATION = false;
        //Decides how offer/answer roles are treated if renegotiation is triggered after the first connection succeeded
        //true = offer/answer role is decided using random numbers to avoid collisions
        //false = keeps offer/answer role the same - this is faster but can cause an error if both sides trigger renegotiation at the same time
        this.RENEGOTATE_ROLES = true;
        //true - We are in the process of sending random numbers back & forth between the peers to decide
        //who will be offerer / answerer
        this.mInActiveRoleNegotiation = false;
        //true - by default we attempt to negotiate roles and fall back if we receive an offer/answer instead of a random number
        //
        //This is automatically set to false once StartSignaling is called and results in this peer ignoring random numbers until signaling is completed
        this.mUseRoleNegotiation = true;
        this.mRandomNumberSent = 0;
        this.mReadyForIce = false;
        this.mBufferedIceCandidates = [];
        //True means this peer will permanently have the offerer role
        this.mIsOfferer = false;
        this.OnIceCandidate = (ev) => {
            if (ev && ev.candidate) {
                let candidate = ev.candidate;
                let msg = JSON.stringify(candidate);
                this.EnqueueOutgoing(msg);
            }
        };
        /*
        private OnIceConnectionStateChange = (ev: Event): void =>
        {
            this.log.LW("oniceconnectionstatechange: " + this.mPeer.iceConnectionState);
            //Chrome stopped emitting "failed" events. We have to react to disconnected events now
            if (this.mPeer.iceConnectionState == "failed" || this.mPeer.iceConnectionState == "disconnected")
            {
                if(this.mState == WebRtcPeerState.Signaling)
                {
                    this.RtcSetSignalingFailed();
                }else if(this.mState == WebRtcPeerState.Connected)
                {
                    this.RtcSetClosed("ice connection state changed to " + this.mPeer.iceConnectionState);
                }
            }
        }
        */
        //this replaces IceConnectionStateChange
        //It works the same between chrome and firefox if adapter.js is used 
        this.OnConnectionStateChange = (ev) => {
            if (this.DEBUG)
                this.log.LW("onconnectionstatechange: " + this.mPeer.connectionState);
            if (this.mPeer.connectionState === 'failed') {
                if (this.USE_ICE_RESTART && this.mRetries < this.MAX_RETRIES) {
                    //retry to connect
                    this.mRetries++;
                    if (this.mIsOfferer) {
                        if (this.DEBUG)
                            this.log.LW("Try to reconnect. Attempt " + (this.mRetries));
                        this.RestartIce();
                    }
                    else {
                        if (this.DEBUG)
                            this.log.LW("Wait for reconnect"); // Attempt " + (this.mRetries));
                    }
                }
                else {
                    if (this.mRetries >= this.MAX_RETRIES) {
                        this.log.LW("Shutting down peer. IceRestart failed " + (this.mRetries) + "times");
                    }
                    if (this.mState == WebRtcPeerState.Signaling) {
                        //never had a connection established
                        this.RtcSetSignalingFailed("connectionState switched to failed");
                    }
                    else if (this.mState == WebRtcPeerState.Connected) {
                        //connection was established and failed
                        this.RtcSetClosed("ice connection state changed to " + this.mPeer.iceConnectionState);
                    }
                }
            }
            else if (this.mPeer.connectionState === "connected") {
                this.mRetries = 0;
                if (this.RENEGOTATE_ROLES) {
                    //switch mUseRoleNegotiation back on for the next negotiation attempt
                    this.mUseRoleNegotiation = true;
                }
            }
        };
        this.OnIceGatheringStateChange = (ev) => {
            if (this.DEBUG)
                this.log.L("onicegatheringstatechange: " + this.mPeer.iceGatheringState);
        };
        this.OnNegotiationNeeded = (ev) => {
            //we ignore OnNegotiationNeeded during the first trigger when the peer isn't connected yet
            //(handled separately)
            if (this.mState == WebRtcPeerState.Connected) {
                //if the peer is configured for single negotiation we skip this event
                //it likely indicates an error as this should never happen
                if (this.SINGLE_NEGOTIATION) {
                    this.log.LW("OnNegotiationNeeded: ignored because the peer is configured for single negotiation."
                        + " This can indicate the peer is configured incorrectly and media will not be sent.");
                }
                else if (this.RENEGOTATE_ROLES) {
                    this.log.L("OnNegotiationNeeded: renegotiating signaling roles");
                    this.NegotiateSignaling();
                }
                else {
                    //user triggered Configure
                    this.log.L("starting signaling due to OnNegotiationNeeded and RENEGOTATE_ROLES=false");
                    this.StartSignalingInternal();
                }
            }
        };
        //broken in chrome. won't switch to closed anymore
        this.OnSignalingChange = (ev) => {
            if (this.DEBUG)
                this.log.LW("onsignalingstatechange:" + this.mPeer.signalingState);
            //obsolete
            if (this.mPeer.signalingState == "closed") {
                this.RtcSetClosed("signaling state changed to " + this.mPeer.signalingState);
            }
        };
        this.mId = AWebRtcPeer.sNextId++;
        this.log = baseLogger.CreateSub("Peer" + this.mId);
        this.USE_ICE_RESTART = peerConfig.MaxIceRestart > 0;
        this.MAX_RETRIES = peerConfig.MaxIceRestart;
        this.SetupPeer(peerConfig.RtcConfig);
        //remove this. it will trigger this call before the subclasses
        //are initialized
        this.OnSetup();
        this.mState = WebRtcPeerState.Created;
        if (this.DEBUG)
            window["peer" + this.mId] = this;
    }
    SetupPeer(rtcConfig) {
        this.mPeer = new RTCPeerConnection(rtcConfig);
        this.mPeer.onicecandidate = this.OnIceCandidate;
        //this.mPeer.oniceconnectionstatechange =  this.OnIceConnectionStateChange; 
        this.mPeer.onconnectionstatechange = this.OnConnectionStateChange;
        this.mPeer.onicegatheringstatechange = this.OnIceGatheringStateChange;
        this.mPeer.onnegotiationneeded = this.OnNegotiationNeeded;
        this.mPeer.onsignalingstatechange = this.OnSignalingChange;
    }
    DisposeInternal() {
        this.Cleanup("Dispose was called");
    }
    Dispose() {
        if (this.mPeer != null) {
            this.DisposeInternal();
        }
    }
    Cleanup(reason) {
        //closing webrtc could cause old events to flush out -> make sure we don't call cleanup
        //recursively
        if (this.mState == WebRtcPeerState.Closed || this.mState == WebRtcPeerState.Closing) {
            return;
        }
        this.mState = WebRtcPeerState.Closing;
        this.log.L("Peer is closing down. reason: " + reason);
        this.OnCleanup();
        if (this.mPeer != null)
            this.mPeer.close();
        //js version still receives callbacks after this. would make it
        //impossible to get the state
        //this.mReliableDataChannel = null;
        //this.mUnreliableDataChannel = null;
        //this.mPeer = null;
        this.mState = WebRtcPeerState.Closed;
    }
    Update() {
        if (this.mState != WebRtcPeerState.Closed && this.mState != WebRtcPeerState.Closing && this.mState != WebRtcPeerState.SignalingFailed)
            this.UpdateState();
        //if (this.mState == WebRtcPeerState.Signaling || this.mState == WebRtcPeerState.Created)
        this.HandleIncomingSignaling();
    }
    UpdateState() {
        //will only be entered if the current state isn't already one of the ending states (closed, closing, signalingfailed)
        if (this.mRtcInternalState == WebRtcInternalState.Closed) {
            //if webrtc switched to the closed state -> make sure everything is destroyed.
            //webrtc closed the connection. update internal state + destroy the references
            //to webrtc
            this.Cleanup("WebRTC triggered an event to initiate shutdown (see log above).");
            //mState will be Closed now as well
        }
        else if (this.mRtcInternalState == WebRtcInternalState.SignalingFailed) {
            //if webrtc switched to a state indicating the signaling process failed ->  set the whole state to failed
            //this step will be ignored if the peers are destroyed already to not jump back from closed state to failed
            this.mState = WebRtcPeerState.SignalingFailed;
        }
        else if (this.mRtcInternalState == WebRtcInternalState.Connected) {
            this.mState = WebRtcPeerState.Connected;
        }
    }
    BufferIceCandidate(ice) {
        this.mBufferedIceCandidates.push(ice);
    }
    /**Called after setRemoteDescription succeeded.
     * After this call we accept ice candidates and add all buffered ice candidates we received
     * until then.
     *
     * This is a workaround for problems between Safari & Firefox. Safari sometimes sends ice candidates before
     * it sends an answer causing an error in firefox.
     */
    StartIce() {
        if (this.DEBUG)
            this.log.L("accepting ice candidates. buffered " + this.mBufferedIceCandidates.length);
        this.mReadyForIce = true;
        if (this.mBufferedIceCandidates.length > 0) {
            if (this.DEBUG)
                this.log.L("adding locally buffered ice candidates");
            //signaling active. Forward ice candidates we received so far
            const candidates = this.mBufferedIceCandidates;
            this.mBufferedIceCandidates = [];
            for (var candidate of candidates) {
                this.AddIceCandidate(candidate);
            }
        }
    }
    AddIceCandidate(ice) {
        //based on the shim internals there is a risk it triggers errors outside of the promise
        try {
            let promise = this.mPeer.addIceCandidate(ice);
            promise.then(() => { });
            promise.catch((error) => {
                this.log.LW("Error during promise addIceCandidate: " + error + "! ice candidate ignored: " + JSON.stringify(ice));
            });
        }
        catch (error) {
            this.log.LW("Error during call to addIceCandidate: " + error + "! ice candidate ignored: " + JSON.stringify(ice));
        }
    }
    HandleIncomingSignaling() {
        //handle the incoming messages all at once
        while (this.mIncomingSignalingQueue.Count() > 0) {
            let msgString = this.mIncomingSignalingQueue.Dequeue();
            let randomNumber = _Helper__WEBPACK_IMPORTED_MODULE_1__.Helper.tryParseInt(msgString);
            if (randomNumber != null) {
                if (this.mUseRoleNegotiation) {
                    //We use random numbers as tie breaker in several situations:
                    // 1. The signaling connects two peers without one of the peers starting the connection (shared_address mode)
                    //   in which case both sides get a new "incoming" connection and will send out random numbers immediately
                    // 2. The server uses custom code and sends a random number to force the client into offer or answerer role 
                    // 3. Renegotiation was triggered
                    if (this.mInActiveRoleNegotiation === false) {
                        //the other side triggered a role negotiation without us knowing -> also trigger our side first
                        this.log.L("Remote side requested renegotiation.");
                        this.NegotiateSignaling();
                    }
                    if (randomNumber < this.mRandomNumberSent) {
                        //own diced number was bigger -> start signaling
                        this.log.L("Role negotiation complete. Starting signaling.");
                        this.StartSignalingInternal();
                    }
                    else if (randomNumber == this.mRandomNumberSent) {
                        //same numbers. restart the process
                        this.log.L("Retrying role negotiation");
                        this.NegotiateSignaling();
                    }
                    else {
                        //wait for other peer to start signaling
                        this.log.L("Role negotiation complete. Waiting for signaling.");
                    }
                }
                else {
                    //ignore random number from the other side. This peer has deactivated it
                    this.log.L("Other side attempted role negotiation but this is inactive. Ignored " + randomNumber);
                }
            }
            else {
                //must be a webrtc signaling message using default json formatting
                let msg = JSON.parse(msgString);
                if (msg.sdp) {
                    let sdp = new RTCSessionDescription(msg);
                    if (sdp.type == 'offer') {
                        this.CreateAnswer(sdp);
                        //setTimeout(() => {  }, 5000);
                    }
                    else {
                        //setTimeout(() => { }, 5000);
                        this.RecAnswer(sdp);
                    }
                }
                else {
                    let ice = new RTCIceCandidate(msg);
                    if (ice != null) {
                        if (this.mReadyForIce) {
                            //expected normal behaviour
                            this.AddIceCandidate(ice);
                        }
                        else {
                            //Safari sometimes sends ice candidates before the answer message
                            //causing firefox to trigger an error
                            //buffer and reemit once setRemoteCandidate has been called
                            this.BufferIceCandidate(ice);
                        }
                    }
                }
            }
        }
    }
    AddSignalingMessage(msg) {
        if (this.LOG_SIGNALING)
            this.log.L("incoming Signaling message " + msg);
        this.mIncomingSignalingQueue.Enqueue(msg);
    }
    DequeueSignalingMessage(/*out*/ msg) {
        //lock might be not the best way to deal with this
        //lock(mOutgoingSignalingQueue)
        {
            if (this.mOutgoingSignalingQueue.Count() > 0) {
                msg.val = this.mOutgoingSignalingQueue.Dequeue();
                return true;
            }
            else {
                msg.val = null;
                return false;
            }
        }
    }
    EnqueueOutgoing(msg) {
        //lock(mOutgoingSignalingQueue)
        {
            if (this.LOG_SIGNALING)
                this.log.L("Outgoing Signaling message " + msg);
            this.mOutgoingSignalingQueue.Enqueue(msg);
        }
    }
    //Starts signaling. This forces this peer to create an offer. The other side must automatically
    //switch to answer role
    StartSignaling() {
        //user triggers signaling. For backwards compatibility we turn off
        //role negotiation which always means this peer creates the offer
        _Helper__WEBPACK_IMPORTED_MODULE_1__.SLog.L("StartSignaling signaling by forcing offer role");
        this.mUseRoleNegotiation = false;
        this.StartSignalingInternal();
    }
    //Triggers data channel setup if needed and then creates & sends an offer
    StartSignalingInternal() {
        this.OnStartSignaling();
        this.CreateOffer();
    }
    //Similar to StartSignaling but this will first send out a random number
    //the higher number starts signaling and creates an offer
    NegotiateSignaling() {
        //0 - reserved to force a remote peer into answer mode
        //2147483647 - reserved to force a remote peer into offer mode
        let nb = _Helper__WEBPACK_IMPORTED_MODULE_1__.Random.getRandomInt(1, 2147483647);
        this.mRandomNumberSent = nb;
        this.mInActiveRoleNegotiation = true;
        _Helper__WEBPACK_IMPORTED_MODULE_1__.SLog.L("Attempting to negotiate signaling using number " + this.mRandomNumberSent);
        this.EnqueueOutgoing("" + nb);
    }
    /**Triggers the actual createOffer method on the Peer
     * Can be overridden to ensure specific transceiver configurations are performed
     * before the actual answer is created.
     * @returns Promise returned by createOffer
     */
    CreateOfferImpl() {
        return __awaiter(this, void 0, void 0, function* () {
            const mOfferOptions = { "offerToReceiveAudio": false, "offerToReceiveVideo": false };
            return this.mPeer.createOffer(mOfferOptions);
        });
    }
    CreateOffer() {
        this.mIsOfferer = true;
        this.mReadyForIce = false;
        this.mInActiveRoleNegotiation = false;
        this.log.L("CreateOffer");
        let createOfferPromise = this.CreateOfferImpl();
        createOfferPromise.then((desc_in) => {
            let desc_out = this.ProcessLocalSdp(desc_in);
            let msg = JSON.stringify(desc_out);
            let setDescPromise = this.mPeer.setLocalDescription(desc_in);
            setDescPromise.then(() => __awaiter(this, void 0, void 0, function* () {
                this.RtcSetSignalingStarted();
                this.EnqueueOutgoing(msg);
            }));
            setDescPromise.catch((error) => {
                this.log.LE(error);
                this.log.LE("Error during setLocalDescription with sdp: " + JSON.stringify(desc_in));
                this.RtcSetSignalingFailed("Failed to set the offer as local description.");
            });
        });
        createOfferPromise.catch((error) => {
            this.log.LE(error);
            this.RtcSetSignalingFailed("Failed to create an offer.");
        });
    }
    ProcessLocalSdp(desc) {
        return desc;
    }
    ProcessRemoteSdp(desc) {
        return desc;
    }
    /**Triggers the actual createAnswer method on the Peer
     * Can be overridden to ensure specific transceiver configurations are performed
     * before the actual answer is created.
     * @returns Promise returned by createAnswer
     */
    CreateAnswerImpl() {
        return __awaiter(this, void 0, void 0, function* () {
            return this.mPeer.createAnswer();
        });
    }
    CreateAnswer(offer) {
        this.log.L("CreateAnswer");
        this.mInActiveRoleNegotiation = false;
        this.mReadyForIce = false;
        offer = this.ProcessRemoteSdp(offer);
        let remoteDescPromise = this.mPeer.setRemoteDescription(offer);
        remoteDescPromise.then(() => {
            this.StartIce();
            let createAnswerPromise = this.CreateAnswerImpl();
            createAnswerPromise.then((desc_in) => {
                let desc_out = this.ProcessLocalSdp(desc_in);
                let msg = JSON.stringify(desc_out);
                let localDescPromise = this.mPeer.setLocalDescription(desc_in);
                localDescPromise.then(() => {
                    this.RtcSetSignalingStarted();
                    this.EnqueueOutgoing(msg);
                });
                localDescPromise.catch((error) => {
                    this.log.LE(error);
                    this.RtcSetSignalingFailed("Failed to set the answer as local description.");
                });
            });
            createAnswerPromise.catch((error) => {
                this.log.LE(error);
                this.RtcSetSignalingFailed("Failed to create an answer.");
            });
        });
        remoteDescPromise.catch((error) => {
            this.log.LE(error);
            this.RtcSetSignalingFailed("Failed to set the offer as remote description.");
        });
    }
    RecAnswer(answer) {
        if (this.DEBUG)
            this.log.LW("RecAnswer");
        answer = this.ProcessRemoteSdp(answer);
        let remoteDescPromise = this.mPeer.setRemoteDescription(answer);
        remoteDescPromise.then(() => {
            //all done
            this.StartIce();
        });
        remoteDescPromise.catch((error) => {
            this.log.LE(error);
            this.RtcSetSignalingFailed("Failed to set the answer as remote description.");
        });
    }
    RtcSetSignalingStarted() {
        if (this.mRtcInternalState == WebRtcInternalState.None) {
            this.mRtcInternalState = WebRtcInternalState.Signaling;
        }
    }
    RtcSetSignalingFailed(reason) {
        this.log.L("Signaling failed: " + reason);
        this.mRtcInternalState = WebRtcInternalState.SignalingFailed;
    }
    RtcSetConnected() {
        if (this.mRtcInternalState == WebRtcInternalState.Signaling)
            this.mRtcInternalState = WebRtcInternalState.Connected;
    }
    /**Called if a WebRTC side event leads to this peer closing
     * e.g. ice failed, data channel suddenly closed
     * This must not be an error. It might just be the remote side ending the call
     * which will usually result in the data channels closing.
     * @param reason Additional information for logging
     */
    RtcSetClosed(reason) {
        if (this.mRtcInternalState == WebRtcInternalState.Connected) {
            if (this.mState !== WebRtcPeerState.Closed
                && this.mState !== WebRtcPeerState.Closing) {
                this.log.L("WebRTC side event triggered closure. Reason: " + reason);
            }
            this.mRtcInternalState = WebRtcInternalState.Closed;
        }
    }
    TriggerRestartIce() {
        this.mPeer.restartIce();
        this.log.LW("restartIce + starting signaling");
        this.StartSignalingInternal();
    }
    RestartIce() {
        this.TriggerRestartIce();
        /*
        if (this.mReconnectInterval === null) {
            //retry immediately on the first call
            this.TriggerRestartIce();
            //then repeat every 15 sec (and ignore any other calls to RestartIce)
            this.log.LW("starting reconnect interval.");
            this.mReconnectInterval = setInterval(() => {
                if (this.mPeer.connectionState === "connected") {
                    this.log.LW("reconnect worked.");
                    clearInterval(this.mReconnectInterval);
                    this.mReconnectInterval = null;
                } else {
                    this.log.LW("restart ice has not reconnected the peers yet. Retrying ...");
                    this.TriggerRestartIce();
                }
            }, 15000);
        }
            */
    }
}
AWebRtcPeer.sNextId = 0;
class WebRtcDataPeer extends AWebRtcPeer {
    get ConnectionId() {
        return this.mConnectionId;
    }
    get SignalingInfo() {
        return this.mInfo;
    }
    SetSignalingInfo(info) {
        this.mInfo = info;
    }
    constructor(id, peerConfig, baseLogger) {
        super(peerConfig, baseLogger);
        this.mInfo = null;
        this.mEvents = new _Helper__WEBPACK_IMPORTED_MODULE_1__.Queue();
        this.mRtcEvents = new _Helper__WEBPACK_IMPORTED_MODULE_1__.Queue();
        this.mReliableDataChannelReady = false;
        this.mUnreliableDataChannelReady = false;
        this.mReliableDataChannel = null;
        this.mUnreliableDataChannel = null;
        this.mConnectionId = id;
    }
    OnSetup() {
        this.mPeer.ondatachannel = (ev) => { this.OnDataChannel(ev.channel); };
    }
    //Triggers if this peer starts the signaling. Not triggered on answer side
    //TODO: better renamed to OnBeforeOffer?
    OnStartSignaling() {
        if (this.mReliableDataChannel === null) {
            let configReliable = {};
            this.mReliableDataChannel = this.mPeer.createDataChannel(WebRtcDataPeer.sLabelReliable, configReliable);
            this.RegisterObserverReliable();
        }
        if (this.mUnreliableDataChannel === null) {
            let configUnreliable = {};
            configUnreliable.maxRetransmits = 0;
            configUnreliable.ordered = false;
            this.mUnreliableDataChannel = this.mPeer.createDataChannel(WebRtcDataPeer.sLabelUnreliable, configUnreliable);
            this.RegisterObserverUnreliable();
        }
    }
    OnCleanup() {
        if (this.mReliableDataChannel != null)
            this.mReliableDataChannel.close();
        if (this.mUnreliableDataChannel != null)
            this.mUnreliableDataChannel.close();
        //dont set to null. handlers will be called later
    }
    RegisterObserverReliable() {
        this.mReliableDataChannel.onmessage = (event) => { this.ReliableDataChannel_OnMessage(event); };
        this.mReliableDataChannel.onopen = (event) => { this.ReliableDataChannel_OnOpen(); };
        this.mReliableDataChannel.onclose = (event) => { this.ReliableDataChannel_OnClose(); };
        this.mReliableDataChannel.onerror = (event) => { this.ReliableDataChannel_OnError(""); }; //should the event just be a string?
    }
    RegisterObserverUnreliable() {
        this.mUnreliableDataChannel.onmessage = (event) => { this.UnreliableDataChannel_OnMessage(event); };
        this.mUnreliableDataChannel.onopen = (event) => { this.UnreliableDataChannel_OnOpen(); };
        this.mUnreliableDataChannel.onclose = (event) => { this.UnreliableDataChannel_OnClose(); };
        this.mUnreliableDataChannel.onerror = (event) => { this.UnreliableDataChannel_OnError(""); }; //should the event just be a string?
    }
    SendData(data, /* offset : number, length : number,*/ reliable) {
        //let buffer: ArrayBufferView = data.subarray(offset, offset + length) as ArrayBufferView;
        let buffer = data;
        let MAX_SEND_BUFFER = 1024 * 1024;
        //chrome bug: If the channels is closed remotely trough disconnect
        //then the local channel can appear open but will throw an exception
        //if send is called
        let sentSuccessfully = false;
        try {
            if (reliable) {
                if (this.mReliableDataChannel.readyState === "open") {
                    //bugfix: WebRTC seems to simply close the data channel if we send
                    //too much at once. avoid this from now on by returning false
                    //if the buffer gets too full
                    if ((this.mReliableDataChannel.bufferedAmount + buffer.byteLength) < MAX_SEND_BUFFER) {
                        this.mReliableDataChannel.send(buffer);
                        sentSuccessfully = true;
                    }
                }
            }
            else {
                if (this.mUnreliableDataChannel.readyState === "open") {
                    if ((this.mUnreliableDataChannel.bufferedAmount + buffer.byteLength) < MAX_SEND_BUFFER) {
                        this.mUnreliableDataChannel.send(buffer);
                        sentSuccessfully = true;
                    }
                }
            }
        }
        catch (e) {
            this.log.LE("Exception while trying to send: " + e);
        }
        return sentSuccessfully;
    }
    GetBufferedAmount(reliable) {
        let result = -1;
        try {
            if (reliable) {
                if (this.mReliableDataChannel.readyState === "open") {
                    result = this.mReliableDataChannel.bufferedAmount;
                }
            }
            else {
                if (this.mUnreliableDataChannel.readyState === "open") {
                    result = this.mUnreliableDataChannel.bufferedAmount;
                }
            }
        }
        catch (e) {
            this.log.LE("Exception while trying to access GetBufferedAmount: " + e);
        }
        return result;
    }
    DequeueEvent(/*out*/ ev) {
        //lock(mEvents)
        {
            if (this.mEvents.Count() > 0) {
                ev.val = this.mEvents.Dequeue();
                return true;
            }
        }
        return false;
    }
    Enqueue(ev) {
        //lock(mEvents)
        {
            this.mEvents.Enqueue(ev);
        }
    }
    OnDataChannel(data_channel) {
        let newChannel = data_channel;
        if (newChannel.label == WebRtcDataPeer.sLabelReliable) {
            this.mReliableDataChannel = newChannel;
            this.RegisterObserverReliable();
        }
        else if (newChannel.label == WebRtcDataPeer.sLabelUnreliable) {
            this.mUnreliableDataChannel = newChannel;
            this.RegisterObserverUnreliable();
        }
        else {
            this.log.LE("Datachannel with unexpected label " + newChannel.label);
        }
    }
    RtcOnMessageReceived(event, reliable) {
        let eventType = _index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.UnreliableMessageReceived;
        if (reliable) {
            eventType = _index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ReliableMessageReceived;
        }
        //async conversion to blob/arraybuffer here
        if (event.data instanceof ArrayBuffer) {
            let buffer = new Uint8Array(event.data);
            this.Enqueue(new _index__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(eventType, this.mConnectionId, buffer));
        }
        else if (event.data instanceof Blob) {
            var connectionId = this.mConnectionId;
            var fileReader = new FileReader();
            var self = this;
            fileReader.onload = function () {
                //need to use function as this pointer is needed to reference to the data
                let data = this.result;
                let buffer = new Uint8Array(data);
                self.Enqueue(new _index__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(eventType, self.mConnectionId, buffer));
            };
            fileReader.readAsArrayBuffer(event.data);
        }
        else {
            this.log.LE("Invalid message type. Only blob and arraybuffer supported: " + event.data);
        }
    }
    ReliableDataChannel_OnMessage(event) {
        this.log.L("ReliableDataChannel_OnMessage ");
        this.RtcOnMessageReceived(event, true);
    }
    ReliableDataChannel_OnOpen() {
        if (this.DEBUG)
            this.log.LW("mReliableDataChannelReady");
        this.mReliableDataChannelReady = true;
        if (this.IsRtcConnected()) {
            this.RtcSetConnected();
            this.log.L("Fully connected");
        }
    }
    ReliableDataChannel_OnClose() {
        let msg = "reliable data channel closed";
        if (this.USE_ICE_RESTART && this.USE_ICE_RESTART_DC_WORKAROUND && this.MAX_RETRIES > 0
            && this.GetState() == WebRtcPeerState.Connected) {
            //note this warning can often be ignored: If this is a planned shutdown whole peer
            //will be closed and the shutdown is correct processed even when ignoring this event.
            //only if ice restart is attempted this warning indicates that while the connection
            //recovers out data channels did not (only happens with firefox)
            this.log.LW("ICE_RESTART_DC_WORKAROUND: Ignoring data channel closure. " + msg);
            return;
        }
        this.RtcSetClosed(msg);
    }
    ReliableDataChannel_OnError(error) {
        let err = "reliable data channel error: " + JSON.stringify(error);
        this.log.LE(err);
        this.RtcSetClosed(err);
    }
    UnreliableDataChannel_OnMessage(event) {
        this.log.L("UnreliableDataChannel_OnMessage ");
        this.RtcOnMessageReceived(event, false);
    }
    UnreliableDataChannel_OnOpen() {
        if (this.DEBUG)
            this.log.LW("mUnreliableDataChannelReady");
        this.mUnreliableDataChannelReady = true;
        if (this.IsRtcConnected()) {
            this.RtcSetConnected();
            this.log.L("Fully connected");
        }
    }
    UnreliableDataChannel_OnClose() {
        let msg = "unreliable data channel closed";
        if (this.USE_ICE_RESTART && this.USE_ICE_RESTART_DC_WORKAROUND && this.MAX_RETRIES > 0
            && this.GetState() == WebRtcPeerState.Connected) {
            //note this warning can often be ignored: If this is a planned shutdown whole peer
            //will be closed and the shutdown is correct processed even when ignoring this event.
            //only if ice restart is attempted this warning indicates that while the connection
            //recovers out data channels did not (only happens with firefox)
            this.log.LW("ICE_RESTART_DC_WORKAROUND: Ignoring data channel closure. " + msg);
            return;
        }
        this.RtcSetClosed(msg);
    }
    UnreliableDataChannel_OnError(error) {
        let err = "reliable data channel error: " + JSON.stringify(error);
        this.log.LE(err);
        this.RtcSetClosed(err);
    }
    IsRtcConnected() {
        return this.mReliableDataChannelReady && this.mUnreliableDataChannelReady;
    }
    RequestStats() {
        setTimeout(() => __awaiter(this, void 0, void 0, function* () {
            const all = yield this.mPeer.getStats(null);
            const reports = Array.from(all.values());
            var evt = new _IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_2__.StatsEvent(this.mConnectionId, reports);
            this.mRtcEvents.Enqueue(evt);
        }), 0);
    }
    DequeueRtcEvent() {
        return this.mRtcEvents.Dequeue();
    }
}
WebRtcDataPeer.sLabelReliable = "reliable";
WebRtcDataPeer.sLabelUnreliable = "unreliable";


/***/ }),

/***/ "./src/awrtc/network/WebsocketNetwork.ts":
/*!***********************************************!*\
  !*** ./src/awrtc/network/WebsocketNetwork.ts ***!
  \***********************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   WebsocketConnectionStatus: () => (/* binding */ WebsocketConnectionStatus),
/* harmony export */   WebsocketNetwork: () => (/* binding */ WebsocketNetwork),
/* harmony export */   WebsocketServerStatus: () => (/* binding */ WebsocketServerStatus)
/* harmony export */ });
/* harmony import */ var _INetwork__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./INetwork */ "./src/awrtc/network/INetwork.ts");
/* harmony import */ var _Helper__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./Helper */ "./src/awrtc/network/Helper.ts");
/*
Copyright (c) 2021, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/


var WebsocketConnectionStatus;
(function (WebsocketConnectionStatus) {
    WebsocketConnectionStatus[WebsocketConnectionStatus["Uninitialized"] = 0] = "Uninitialized";
    WebsocketConnectionStatus[WebsocketConnectionStatus["NotConnected"] = 1] = "NotConnected";
    WebsocketConnectionStatus[WebsocketConnectionStatus["Connecting"] = 2] = "Connecting";
    WebsocketConnectionStatus[WebsocketConnectionStatus["Connected"] = 3] = "Connected";
    WebsocketConnectionStatus[WebsocketConnectionStatus["Disconnecting"] = 4] = "Disconnecting"; //server will shut down, all clients disconnect, ...
})(WebsocketConnectionStatus || (WebsocketConnectionStatus = {}));
var WebsocketServerStatus;
(function (WebsocketServerStatus) {
    WebsocketServerStatus[WebsocketServerStatus["Offline"] = 0] = "Offline";
    WebsocketServerStatus[WebsocketServerStatus["Starting"] = 1] = "Starting";
    WebsocketServerStatus[WebsocketServerStatus["Online"] = 2] = "Online";
    WebsocketServerStatus[WebsocketServerStatus["ShuttingDown"] = 3] = "ShuttingDown";
})(WebsocketServerStatus || (WebsocketServerStatus = {}));
//TODO: handle errors if the socket connection failed
//+ send back failed events for connected / serverstart events that are buffered
class WebsocketNetwork {
    getStatus() { return this.mStatus; }
    ;
    constructor(url, configuration) {
        //currents status. will be updated based on update call
        this.mStatus = WebsocketConnectionStatus.Uninitialized;
        //queue to hold buffered outgoing messages
        this.mOutgoingQueue = new Array();
        //buffer for incoming messages
        this.mIncomingQueue = new Array();
        //Status of the server for incoming connections
        this.mServerStatus = WebsocketServerStatus.Offline;
        //outgoing connections (just need to be stored to allow to send out a failed message
        //if the whole signaling connection fails
        this.mConnecting = new Array();
        this.mConnections = new Array();
        //next free connection id
        this.mNextOutgoingConnectionId = new _INetwork__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(1);
        /// <summary>
        /// Assume 1 until message received
        /// </summary>
        this.mRemoteProtocolVersion = 1;
        this.mUrl = null;
        this.mHeartbeatReceived = true;
        this.mIsDisposed = false;
        this.mUrl = url;
        this.mStatus = WebsocketConnectionStatus.NotConnected;
        this.mConfig = configuration;
        if (!this.mConfig)
            this.mConfig = new WebsocketNetwork.Configuration();
        this.mConfig.Lock();
    }
    WebsocketConnect() {
        this.mStatus = WebsocketConnectionStatus.Connecting;
        this.mSocket = new WebSocket(this.mUrl);
        this.mSocket.binaryType = "arraybuffer";
        this.mSocket.onopen = () => { this.OnWebsocketOnOpen(); };
        this.mSocket.onerror = (error) => { this.OnWebsocketOnError(error); };
        this.mSocket.onmessage = (e) => { this.OnWebsocketOnMessage(e); };
        this.mSocket.onclose = (e) => { this.OnWebsocketOnClose(e); };
    }
    WebsocketCleanup() {
        this.mSocket.onopen = null;
        this.mSocket.onerror = null;
        this.mSocket.onmessage = null;
        this.mSocket.onclose = null;
        if (this.mSocket.readyState == this.mSocket.OPEN
            || this.mSocket.readyState == this.mSocket.CONNECTING) {
            _Helper__WEBPACK_IMPORTED_MODULE_1__.SLog.L("closing websockets");
            this.mSocket.close();
        }
        this.mSocket = null;
    }
    EnsureServerConnection() {
        if (this.mStatus == WebsocketConnectionStatus.NotConnected) {
            //no server
            //no connection about to be established
            //no current connections
            //-> disconnect the server connection
            this.WebsocketConnect();
        }
    }
    UpdateHeartbeat() {
        if (this.mStatus == WebsocketConnectionStatus.Connected && this.mConfig.Heartbeat > 0) {
            let diff = Date.now() - this.mLastHeartbeat;
            if (diff > (this.mConfig.Heartbeat * 1000)) {
                //We trigger heatbeat timeouts only for protocol V2
                //protocol 1 can receive the heatbeats but 
                //won't send a reply
                //(still helpful to trigger TCP ACK timeout)
                if (this.mRemoteProtocolVersion > 1
                    && this.mHeartbeatReceived == false) {
                    this.TriggerHeartbeatTimeout();
                    return;
                }
                this.mLastHeartbeat = Date.now();
                this.mHeartbeatReceived = false;
                this.SendHeartbeat();
            }
        }
    }
    TriggerHeartbeatTimeout() {
        _Helper__WEBPACK_IMPORTED_MODULE_1__.SLog.L("Closing due to heartbeat timeout. Server didn't respond in time.", WebsocketNetwork.LOGTAG);
        this.Cleanup();
    }
    CheckSleep() {
        if (this.mStatus == WebsocketConnectionStatus.Connected
            && this.mServerStatus == WebsocketServerStatus.Offline
            && this.mConnecting.length == 0
            && this.mConnections.length == 0) {
            //no server
            //no connection about to be established
            //no current connections
            //-> disconnect the server connection
            this.Cleanup();
        }
    }
    OnWebsocketOnOpen() {
        _Helper__WEBPACK_IMPORTED_MODULE_1__.SLog.L('onWebsocketOnOpen', WebsocketNetwork.LOGTAG);
        this.mStatus = WebsocketConnectionStatus.Connected;
        this.mLastHeartbeat = Date.now();
        this.SendVersion();
    }
    OnWebsocketOnClose(event) {
        _Helper__WEBPACK_IMPORTED_MODULE_1__.SLog.L('Closed: ' + JSON.stringify(event), WebsocketNetwork.LOGTAG);
        if (event.code != 1000) {
            _Helper__WEBPACK_IMPORTED_MODULE_1__.SLog.LE("Websocket closed with code: " + event.code + " " + event.reason);
        }
        //ignore closed event if it was caused due to a shutdown (as that means we cleaned up already)
        if (this.mStatus == WebsocketConnectionStatus.Disconnecting
            || this.mStatus == WebsocketConnectionStatus.NotConnected)
            return;
        this.Cleanup();
        this.mStatus = WebsocketConnectionStatus.NotConnected;
    }
    OnWebsocketOnMessage(event) {
        if (this.mStatus == WebsocketConnectionStatus.Disconnecting
            || this.mStatus == WebsocketConnectionStatus.NotConnected)
            return;
        //browsers will have ArrayBuffer in event.data -> change to byte array
        let msg = new Uint8Array(event.data);
        this.ParseMessage(msg);
    }
    OnWebsocketOnError(error) {
        //the error event doesn't seem to have any useful information?
        //browser is expected to call OnClose after this
        console.error("Websocket triggered onerror: ", error);
        _Helper__WEBPACK_IMPORTED_MODULE_1__.SLog.LE('WebSocket Error. See browser log for more information. ' + JSON.stringify(error));
    }
    /// <summary>
    /// called during Disconnecting state either trough server connection failed or due to Shutdown
    /// 
    /// Also used to switch to sleeping mode. In this case there connection isn't used as
    /// server and doesn't have any connections (established or connecting) thus
    /// only WebsocketCleanup is in effect.
    /// 
    /// WebsocketNetwork has to be still usable after this call like a newly
    /// created connections (except with events in the message queue)
    /// </summary>
    Cleanup() {
        //check if this was done already (or we are in the process of cleanup already)
        if (this.mStatus == WebsocketConnectionStatus.Disconnecting
            || this.mStatus == WebsocketConnectionStatus.NotConnected)
            return;
        this.mStatus = WebsocketConnectionStatus.Disconnecting;
        //throw connection failed events for each connection in mConnecting
        for (let conId of this.mConnecting) {
            //all connection it tries to establish right now fail due to shutdown
            this.EnqueueIncoming(new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ConnectionFailed, new _INetwork__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(conId), null));
        }
        this.mConnecting = new Array();
        //throw disconnect events for all NewConnection events in the outgoing queue
        //ignore messages and everything else
        for (let conId of this.mConnections) {
            //all connection it tries to establish right now fail due to shutdown
            this.EnqueueIncoming(new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.Disconnected, new _INetwork__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(conId), null));
        }
        this.mConnections = new Array();
        if (this.mServerStatus == WebsocketServerStatus.Starting) {
            //if server was Starting -> throw failed event
            this.EnqueueIncoming(new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitFailed, _INetwork__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, null));
        }
        else if (this.mServerStatus == WebsocketServerStatus.Online) {
            //if server was Online -> throw close event
            this.EnqueueIncoming(new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerClosed, _INetwork__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, null));
        }
        else if (this.mServerStatus == WebsocketServerStatus.ShuttingDown) {
            //if server was ShuttingDown -> throw close event (don't think this can happen)
            this.EnqueueIncoming(new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerClosed, _INetwork__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, null));
        }
        this.mServerStatus = WebsocketServerStatus.Offline;
        this.mOutgoingQueue = new Array();
        this.WebsocketCleanup();
        this.mStatus = WebsocketConnectionStatus.NotConnected;
    }
    EnqueueOutgoing(evt) {
        this.mOutgoingQueue.push(evt);
    }
    EnqueueIncoming(evt) {
        this.mIncomingQueue.push(evt);
    }
    TryRemoveConnecting(id) {
        var index = this.mConnecting.indexOf(id.id);
        if (index != -1) {
            this.mConnecting.splice(index, 1);
        }
    }
    TryRemoveConnection(id) {
        var index = this.mConnections.indexOf(id.id);
        if (index != -1) {
            this.mConnections.splice(index, 1);
        }
    }
    ParseMessage(msg) {
        if (msg.length == 0) {
        }
        else if (msg[0] == _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.MetaVersion) {
            if (msg.length > 1) {
                this.mRemoteProtocolVersion = msg[1];
            }
            else {
                _Helper__WEBPACK_IMPORTED_MODULE_1__.SLog.LW("Received an invalid MetaVersion header without content.");
            }
        }
        else if (msg[0] == _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.MetaHeartbeat) {
            this.mHeartbeatReceived = true;
        }
        else {
            let evt = _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent.fromByteArray(msg);
            this.HandleIncomingEvent(evt);
        }
    }
    HandleIncomingEvent(evt) {
        if (evt.Type == _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection) {
            //removing connecting info
            this.TryRemoveConnecting(evt.ConnectionId);
            //add connection
            this.mConnections.push(evt.ConnectionId.id);
        }
        else if (evt.Type == _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ConnectionFailed) {
            //remove connecting info
            this.TryRemoveConnecting(evt.ConnectionId);
        }
        else if (evt.Type == _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.Disconnected) {
            //remove from connections
            this.TryRemoveConnection(evt.ConnectionId);
        }
        else if (evt.Type == _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitialized) {
            this.mServerStatus = WebsocketServerStatus.Online;
        }
        else if (evt.Type == _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitFailed) {
            this.mServerStatus = WebsocketServerStatus.Offline;
        }
        else if (evt.Type == _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerClosed) {
            this.mServerStatus = WebsocketServerStatus.ShuttingDown;
            //any cleaning up to do?
            this.mServerStatus = WebsocketServerStatus.Offline;
        }
        this.EnqueueIncoming(evt);
    }
    HandleOutgoingEvents() {
        while (this.mOutgoingQueue.length > 0) {
            var evt = this.mOutgoingQueue.shift();
            this.SendNetworkEvent(evt);
        }
    }
    SendHeartbeat() {
        let msg = new Uint8Array(1);
        msg[0] = _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.MetaHeartbeat;
        this.InternalSend(msg);
    }
    SendVersion() {
        let msg = new Uint8Array(2);
        msg[0] = _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.MetaVersion;
        msg[1] = WebsocketNetwork.PROTOCOL_VERSION;
        this.InternalSend(msg);
    }
    SendNetworkEvent(evt) {
        var msg = _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent.toByteArray(evt);
        this.InternalSend(msg);
    }
    InternalSend(msg) {
        this.mSocket.send(msg);
    }
    NextConnectionId() {
        var result = this.mNextOutgoingConnectionId;
        this.mNextOutgoingConnectionId = new _INetwork__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(this.mNextOutgoingConnectionId.id + 1);
        return result;
    }
    GetRandomKey() {
        var result = "";
        for (var i = 0; i < 7; i++) {
            result += String.fromCharCode(65 + Math.round(Math.random() * 25));
        }
        return result;
    }
    //interface implementation
    Dequeue() {
        if (this.mIncomingQueue.length > 0)
            return this.mIncomingQueue.shift();
        return null;
    }
    Peek() {
        if (this.mIncomingQueue.length > 0)
            return this.mIncomingQueue[0];
        return null;
    }
    Update() {
        this.UpdateHeartbeat();
        this.CheckSleep();
    }
    Flush() {
        //ideally we buffer everything and then flush when it is connected as
        //websockets aren't suppose to be used for realtime communication anyway
        if (this.mStatus == WebsocketConnectionStatus.Connected)
            this.HandleOutgoingEvents();
    }
    SendData(id, data, /*offset: number, length: number,*/ reliable) {
        if (id == null || id.id == _INetwork__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID.id) {
            _Helper__WEBPACK_IMPORTED_MODULE_1__.SLog.LW("Ignored message. Invalid connection id.");
            return;
        }
        if (data == null || data.length == 0) {
            _Helper__WEBPACK_IMPORTED_MODULE_1__.SLog.LW("Ignored message. Invalid data.");
            return;
        }
        var evt;
        if (reliable) {
            evt = new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ReliableMessageReceived, id, data);
        }
        else {
            evt = new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.UnreliableMessageReceived, id, data);
        }
        this.EnqueueOutgoing(evt);
        return true;
    }
    Disconnect(id) {
        var evt = new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.Disconnected, id, null);
        this.EnqueueOutgoing(evt);
    }
    Shutdown() {
        this.Cleanup();
        this.mStatus = WebsocketConnectionStatus.NotConnected;
    }
    Dispose() {
        if (this.mIsDisposed == false) {
            this.Shutdown();
            this.mIsDisposed = true;
        }
    }
    StartServer(address) {
        if (address == null) {
            address = "" + this.GetRandomKey();
        }
        if (this.mServerStatus == WebsocketServerStatus.Offline) {
            this.EnsureServerConnection();
            this.mServerStatus = WebsocketServerStatus.Starting;
            //TODO: address is a string but ubytearray is defined. will fail if binary
            this.EnqueueOutgoing(new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitialized, _INetwork__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, address));
        }
        else {
            this.EnqueueIncoming(new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitFailed, _INetwork__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, address));
        }
    }
    StopServer() {
        this.EnqueueOutgoing(new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerClosed, _INetwork__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID, null));
    }
    Connect(address) {
        this.EnsureServerConnection();
        var newConId = this.NextConnectionId();
        this.mConnecting.push(newConId.id);
        var evt = new _INetwork__WEBPACK_IMPORTED_MODULE_0__.NetworkEvent(_INetwork__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection, newConId, address);
        this.EnqueueOutgoing(evt);
        return newConId;
    }
}
WebsocketNetwork.LOGTAG = "WebsocketNetwork";
/// <summary>
/// Version of the protocol implemented here
/// </summary>
WebsocketNetwork.PROTOCOL_VERSION = 2;
/// <summary>
/// Minimal protocol version that is still supported.
/// V 1 servers won't understand heartbeat and version
/// messages but would just log an unknown message and
/// continue normally.
/// </summary>
WebsocketNetwork.PROTOCOL_VERSION_MIN = 1;
(function (WebsocketNetwork) {
    class Configuration {
        constructor() {
            this.mHeartbeat = 30;
            this.mLocked = false;
        }
        get Heartbeat() {
            return this.mHeartbeat;
        }
        set Heartbeat(value) {
            if (this.mLocked) {
                throw new Error("Can't change configuration once used.");
            }
            this.mHeartbeat = value;
        }
        Lock() {
            this.mLocked = true;
        }
    }
    WebsocketNetwork.Configuration = Configuration;
})(WebsocketNetwork || (WebsocketNetwork = {}));
//Below tests only. Move out later
function bufferToString(buffer) {
    let arr = new Uint16Array(buffer.buffer, buffer.byteOffset, buffer.byteLength / 2);
    return String.fromCharCode.apply(null, arr);
}
function stringToBuffer(str) {
    let buf = new ArrayBuffer(str.length * 2);
    let bufView = new Uint16Array(buf);
    for (var i = 0, strLen = str.length; i < strLen; i++) {
        bufView[i] = str.charCodeAt(i);
    }
    let result = new Uint8Array(buf);
    return result;
}


/***/ }),

/***/ "./src/awrtc/network/index.ts":
/*!************************************!*\
  !*** ./src/awrtc/network/index.ts ***!
  \************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   AWebRtcPeer: () => (/* reexport safe */ _WebRtcPeer__WEBPACK_IMPORTED_MODULE_3__.AWebRtcPeer),
/* harmony export */   ConnectionId: () => (/* reexport safe */ _INetwork__WEBPACK_IMPORTED_MODULE_1__.ConnectionId),
/* harmony export */   Debug: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.Debug),
/* harmony export */   Encoder: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.Encoder),
/* harmony export */   Encoding: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.Encoding),
/* harmony export */   Helper: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.Helper),
/* harmony export */   List: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.List),
/* harmony export */   LocalNetwork: () => (/* reexport safe */ _LocalNetwork__WEBPACK_IMPORTED_MODULE_7__.LocalNetwork),
/* harmony export */   NetEventDataType: () => (/* reexport safe */ _INetwork__WEBPACK_IMPORTED_MODULE_1__.NetEventDataType),
/* harmony export */   NetEventType: () => (/* reexport safe */ _INetwork__WEBPACK_IMPORTED_MODULE_1__.NetEventType),
/* harmony export */   NetworkConfig: () => (/* reexport safe */ _NetworkConfig__WEBPACK_IMPORTED_MODULE_0__.NetworkConfig),
/* harmony export */   NetworkEvent: () => (/* reexport safe */ _INetwork__WEBPACK_IMPORTED_MODULE_1__.NetworkEvent),
/* harmony export */   Output: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.Output),
/* harmony export */   PeerConfig: () => (/* reexport safe */ _WebRtcPeer__WEBPACK_IMPORTED_MODULE_3__.PeerConfig),
/* harmony export */   Queue: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.Queue),
/* harmony export */   Random: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.Random),
/* harmony export */   RtcEvent: () => (/* reexport safe */ _IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_4__.RtcEvent),
/* harmony export */   RtcEventType: () => (/* reexport safe */ _IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_4__.RtcEventType),
/* harmony export */   SLog: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.SLog),
/* harmony export */   SLogLevel: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.SLogLevel),
/* harmony export */   SLogger: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.SLogger),
/* harmony export */   SignalingInfo: () => (/* reexport safe */ _WebRtcPeer__WEBPACK_IMPORTED_MODULE_3__.SignalingInfo),
/* harmony export */   StatsEvent: () => (/* reexport safe */ _IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_4__.StatsEvent),
/* harmony export */   UTF16Encoding: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.UTF16Encoding),
/* harmony export */   WebRtcDataPeer: () => (/* reexport safe */ _WebRtcPeer__WEBPACK_IMPORTED_MODULE_3__.WebRtcDataPeer),
/* harmony export */   WebRtcHelper: () => (/* reexport safe */ _Helper__WEBPACK_IMPORTED_MODULE_2__.WebRtcHelper),
/* harmony export */   WebRtcInternalState: () => (/* reexport safe */ _WebRtcPeer__WEBPACK_IMPORTED_MODULE_3__.WebRtcInternalState),
/* harmony export */   WebRtcNetwork: () => (/* reexport safe */ _WebRtcNetwork__WEBPACK_IMPORTED_MODULE_5__.WebRtcNetwork),
/* harmony export */   WebRtcNetworkServerState: () => (/* reexport safe */ _WebRtcNetwork__WEBPACK_IMPORTED_MODULE_5__.WebRtcNetworkServerState),
/* harmony export */   WebRtcPeerState: () => (/* reexport safe */ _WebRtcPeer__WEBPACK_IMPORTED_MODULE_3__.WebRtcPeerState),
/* harmony export */   WebsocketConnectionStatus: () => (/* reexport safe */ _WebsocketNetwork__WEBPACK_IMPORTED_MODULE_6__.WebsocketConnectionStatus),
/* harmony export */   WebsocketNetwork: () => (/* reexport safe */ _WebsocketNetwork__WEBPACK_IMPORTED_MODULE_6__.WebsocketNetwork),
/* harmony export */   WebsocketServerStatus: () => (/* reexport safe */ _WebsocketNetwork__WEBPACK_IMPORTED_MODULE_6__.WebsocketServerStatus)
/* harmony export */ });
/* harmony import */ var _NetworkConfig__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./NetworkConfig */ "./src/awrtc/network/NetworkConfig.ts");
/* harmony import */ var _INetwork__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./INetwork */ "./src/awrtc/network/INetwork.ts");
/* harmony import */ var _Helper__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./Helper */ "./src/awrtc/network/Helper.ts");
/* harmony import */ var _WebRtcPeer__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./WebRtcPeer */ "./src/awrtc/network/WebRtcPeer.ts");
/* harmony import */ var _IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! ./IWebRtcNetwork */ "./src/awrtc/network/IWebRtcNetwork.ts");
/* harmony import */ var _WebRtcNetwork__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! ./WebRtcNetwork */ "./src/awrtc/network/WebRtcNetwork.ts");
/* harmony import */ var _WebsocketNetwork__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! ./WebsocketNetwork */ "./src/awrtc/network/WebsocketNetwork.ts");
/* harmony import */ var _LocalNetwork__WEBPACK_IMPORTED_MODULE_7__ = __webpack_require__(/*! ./LocalNetwork */ "./src/awrtc/network/LocalNetwork.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/










/***/ }),

/***/ "./src/awrtc/unity/CAPI.ts":
/*!*********************************!*\
  !*** ./src/awrtc/unity/CAPI.ts ***!
  \*********************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   CAPI_DeviceApi_LastUpdate: () => (/* binding */ CAPI_DeviceApi_LastUpdate),
/* harmony export */   CAPI_DeviceApi_RequestUpdate: () => (/* binding */ CAPI_DeviceApi_RequestUpdate),
/* harmony export */   CAPI_DeviceApi_Update: () => (/* binding */ CAPI_DeviceApi_Update),
/* harmony export */   CAPI_InitAsync: () => (/* binding */ CAPI_InitAsync),
/* harmony export */   CAPI_MediaNetwork_Configure: () => (/* binding */ CAPI_MediaNetwork_Configure),
/* harmony export */   CAPI_MediaNetwork_Create: () => (/* binding */ CAPI_MediaNetwork_Create),
/* harmony export */   CAPI_MediaNetwork_GetConfigurationError: () => (/* binding */ CAPI_MediaNetwork_GetConfigurationError),
/* harmony export */   CAPI_MediaNetwork_GetConfigurationError_Length: () => (/* binding */ CAPI_MediaNetwork_GetConfigurationError_Length),
/* harmony export */   CAPI_MediaNetwork_GetConfigurationState: () => (/* binding */ CAPI_MediaNetwork_GetConfigurationState),
/* harmony export */   CAPI_MediaNetwork_HasAudioTrack: () => (/* binding */ CAPI_MediaNetwork_HasAudioTrack),
/* harmony export */   CAPI_MediaNetwork_HasUserMedia: () => (/* binding */ CAPI_MediaNetwork_HasUserMedia),
/* harmony export */   CAPI_MediaNetwork_HasVideoTrack: () => (/* binding */ CAPI_MediaNetwork_HasVideoTrack),
/* harmony export */   CAPI_MediaNetwork_IsAvailable: () => (/* binding */ CAPI_MediaNetwork_IsAvailable),
/* harmony export */   CAPI_MediaNetwork_IsMute: () => (/* binding */ CAPI_MediaNetwork_IsMute),
/* harmony export */   CAPI_MediaNetwork_ResetConfiguration: () => (/* binding */ CAPI_MediaNetwork_ResetConfiguration),
/* harmony export */   CAPI_MediaNetwork_SetMute: () => (/* binding */ CAPI_MediaNetwork_SetMute),
/* harmony export */   CAPI_MediaNetwork_SetVolume: () => (/* binding */ CAPI_MediaNetwork_SetVolume),
/* harmony export */   CAPI_MediaNetwork_SetVolumePan: () => (/* binding */ CAPI_MediaNetwork_SetVolumePan),
/* harmony export */   CAPI_MediaNetwork_TryGetFrame: () => (/* binding */ CAPI_MediaNetwork_TryGetFrame),
/* harmony export */   CAPI_MediaNetwork_TryGetFrameDataLength: () => (/* binding */ CAPI_MediaNetwork_TryGetFrameDataLength),
/* harmony export */   CAPI_MediaNetwork_TryGetFrame_Resolution: () => (/* binding */ CAPI_MediaNetwork_TryGetFrame_Resolution),
/* harmony export */   CAPI_MediaNetwork_TryGetFrame_ToTexture: () => (/* binding */ CAPI_MediaNetwork_TryGetFrame_ToTexture),
/* harmony export */   CAPI_Media_EnableScreenCapture: () => (/* binding */ CAPI_Media_EnableScreenCapture),
/* harmony export */   CAPI_Media_GetAudioInputDevices: () => (/* binding */ CAPI_Media_GetAudioInputDevices),
/* harmony export */   CAPI_Media_GetAudioInputDevices_Length: () => (/* binding */ CAPI_Media_GetAudioInputDevices_Length),
/* harmony export */   CAPI_Media_GetVideoDevices: () => (/* binding */ CAPI_Media_GetVideoDevices),
/* harmony export */   CAPI_Media_GetVideoDevices_Length: () => (/* binding */ CAPI_Media_GetVideoDevices_Length),
/* harmony export */   CAPI_PollInitState: () => (/* binding */ CAPI_PollInitState),
/* harmony export */   CAPI_SLog_SetLogLevel: () => (/* binding */ CAPI_SLog_SetLogLevel),
/* harmony export */   CAPI_VideoInput_AddCanvasDevice: () => (/* binding */ CAPI_VideoInput_AddCanvasDevice),
/* harmony export */   CAPI_VideoInput_AddDevice: () => (/* binding */ CAPI_VideoInput_AddDevice),
/* harmony export */   CAPI_VideoInput_RemoveDevice: () => (/* binding */ CAPI_VideoInput_RemoveDevice),
/* harmony export */   CAPI_VideoInput_UpdateFrame: () => (/* binding */ CAPI_VideoInput_UpdateFrame),
/* harmony export */   CAPI_WebRtcNetwork_CheckEventLength: () => (/* binding */ CAPI_WebRtcNetwork_CheckEventLength),
/* harmony export */   CAPI_WebRtcNetwork_Connect: () => (/* binding */ CAPI_WebRtcNetwork_Connect),
/* harmony export */   CAPI_WebRtcNetwork_Create: () => (/* binding */ CAPI_WebRtcNetwork_Create),
/* harmony export */   CAPI_WebRtcNetwork_Dequeue: () => (/* binding */ CAPI_WebRtcNetwork_Dequeue),
/* harmony export */   CAPI_WebRtcNetwork_DequeueEm: () => (/* binding */ CAPI_WebRtcNetwork_DequeueEm),
/* harmony export */   CAPI_WebRtcNetwork_DequeueRtcEvent: () => (/* binding */ CAPI_WebRtcNetwork_DequeueRtcEvent),
/* harmony export */   CAPI_WebRtcNetwork_Disconnect: () => (/* binding */ CAPI_WebRtcNetwork_Disconnect),
/* harmony export */   CAPI_WebRtcNetwork_EventDataToUint8Array: () => (/* binding */ CAPI_WebRtcNetwork_EventDataToUint8Array),
/* harmony export */   CAPI_WebRtcNetwork_Flush: () => (/* binding */ CAPI_WebRtcNetwork_Flush),
/* harmony export */   CAPI_WebRtcNetwork_GetBufferedAmount: () => (/* binding */ CAPI_WebRtcNetwork_GetBufferedAmount),
/* harmony export */   CAPI_WebRtcNetwork_IsAvailable: () => (/* binding */ CAPI_WebRtcNetwork_IsAvailable),
/* harmony export */   CAPI_WebRtcNetwork_IsBrowserSupported: () => (/* binding */ CAPI_WebRtcNetwork_IsBrowserSupported),
/* harmony export */   CAPI_WebRtcNetwork_Peek: () => (/* binding */ CAPI_WebRtcNetwork_Peek),
/* harmony export */   CAPI_WebRtcNetwork_PeekEm: () => (/* binding */ CAPI_WebRtcNetwork_PeekEm),
/* harmony export */   CAPI_WebRtcNetwork_PeekEventDataLength: () => (/* binding */ CAPI_WebRtcNetwork_PeekEventDataLength),
/* harmony export */   CAPI_WebRtcNetwork_Release: () => (/* binding */ CAPI_WebRtcNetwork_Release),
/* harmony export */   CAPI_WebRtcNetwork_RequestStats: () => (/* binding */ CAPI_WebRtcNetwork_RequestStats),
/* harmony export */   CAPI_WebRtcNetwork_SendData: () => (/* binding */ CAPI_WebRtcNetwork_SendData),
/* harmony export */   CAPI_WebRtcNetwork_SendDataEm: () => (/* binding */ CAPI_WebRtcNetwork_SendDataEm),
/* harmony export */   CAPI_WebRtcNetwork_Shutdown: () => (/* binding */ CAPI_WebRtcNetwork_Shutdown),
/* harmony export */   CAPI_WebRtcNetwork_StartServer: () => (/* binding */ CAPI_WebRtcNetwork_StartServer),
/* harmony export */   CAPI_WebRtcNetwork_StopServer: () => (/* binding */ CAPI_WebRtcNetwork_StopServer),
/* harmony export */   CAPI_WebRtcNetwork_Update: () => (/* binding */ CAPI_WebRtcNetwork_Update),
/* harmony export */   GetUnityCanvas: () => (/* binding */ GetUnityCanvas),
/* harmony export */   GetUnityContext: () => (/* binding */ GetUnityContext),
/* harmony export */   gCAPI_WebRtcNetwork_Instances: () => (/* binding */ gCAPI_WebRtcNetwork_Instances),
/* harmony export */   gCAPI_WebRtcNetwork_InstancesNextIndex: () => (/* binding */ gCAPI_WebRtcNetwork_InstancesNextIndex)
/* harmony export */ });
/* harmony import */ var _network_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../network/index */ "./src/awrtc/network/index.ts");
/* harmony import */ var _media_index__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../media/index */ "./src/awrtc/media/index.ts");
/* harmony import */ var _media_browser_index__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ../media_browser/index */ "./src/awrtc/media_browser/index.ts");
/* harmony import */ var _network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ../network/IWebRtcNetwork */ "./src/awrtc/network/IWebRtcNetwork.ts");
/*
Copyright (c) 2024, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
/**This file contains the mapping between the awrtc_browser library and
 * Unitys WebGL support. Not needed for regular use.
 */




var CAPI_InitMode = {
    //Original mode. Devices will be unknown after startup
    Default: 0,
    //Waits for the desvice info to come in
    //names might be missing though (browser security thing)
    WaitForDevices: 1,
    //Asks the user for camera / audio access to be able to
    //get accurate device information
    RequestAccess: 2
};
var CAPI_InitState = {
    Uninitialized: 0,
    Initializing: 1,
    Initialized: 2,
    Failed: 3
};
var gCAPI_InitState = CAPI_InitState.Uninitialized;
var gCAPI_Canvas = null;
function CAPI_InitAsync(initmode, glctx, useAdapter) {
    console.debug("CAPI_InitAsync mode: " + initmode);
    gCAPI_InitState = CAPI_InitState.Initializing;
    //if (typeof GLctx !== 'undefined' && GLctx.canvas) {
    if (glctx && glctx.canvas) {
        gCAPI_Canvas = glctx.canvas;
    }
    if (useAdapter)
        _network_index__WEBPACK_IMPORTED_MODULE_0__.WebRtcHelper.EmitAdapter();
    InitAutoplayWorkaround();
    let hasDevApi = _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.DeviceApi.IsApiAvailable();
    if (hasDevApi && initmode == CAPI_InitMode.WaitForDevices) {
        _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.DeviceApi.Update();
    }
    else if (hasDevApi && initmode == CAPI_InitMode.RequestAccess) {
        _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.DeviceApi.RequestUpdate();
    }
    else {
        //either no device access available or not requested. Switch
        //to init state immediately without device info
        gCAPI_InitState = CAPI_InitState.Initialized;
        if (hasDevApi == false) {
            console.debug("Initialized without accessible DeviceAPI");
        }
    }
}
function InitAutoplayWorkaround() {
    if (gCAPI_Canvas == null) {
        _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LW("Autoplay workaround inactive. No canvas object known to register click & touch event handlers.");
        return;
    }
    let listener = null;
    listener = () => {
        //called during user input event
        _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.AutoplayResolver.Resolve();
        if (_media_browser_index__WEBPACK_IMPORTED_MODULE_2__.AutoplayResolver.HasCompleted() === true) {
            gCAPI_Canvas.removeEventListener("click", listener, false);
            gCAPI_Canvas.removeEventListener("touchstart", listener, false);
        }
    };
    //If a stream runs into autoplay issues we add a listener for the next on click / touchstart event
    //and resolve it on the next incoming event
    _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.AutoplayResolver.onautoplayblocked = () => {
        _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("The browser blocked playback of a video stream. Trying to resolve this the next time the user interacts with the canvas");
        gCAPI_Canvas.addEventListener("click", listener, false);
        //mobile devices don't appear to get the click event if unity is running. 
        //iOS ignores the first touchstart event but continues autoplayback after the second event
        gCAPI_Canvas.addEventListener("touchstart", listener, false);
    };
}
function CAPI_PollInitState() {
    //keep checking if the DeviceApi left pending state
    //Once completed init is finished.
    //Later we might do more here
    if (_media_browser_index__WEBPACK_IMPORTED_MODULE_2__.DeviceApi.IsPending == false && gCAPI_InitState == CAPI_InitState.Initializing) {
        gCAPI_InitState = CAPI_InitState.Initialized;
        console.debug("Init completed.");
    }
    return gCAPI_InitState;
}
/**
 *
 * @param loglevel
 */
function CAPI_SLog_SetLogLevel(loglevel) {
    if (loglevel < 0 || loglevel > 4) {
        _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LogError("Invalid log level " + loglevel);
        return;
    }
    _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.SetLogLevel(loglevel);
}
let gCAPI_WebRtcNetwork_Instances = {};
let gCAPI_WebRtcNetwork_InstancesNextIndex = 1;
function CAPI_WebRtcNetwork_IsAvailable() {
    //used by C# component to check if this plugin is loaded.
    //can only go wrong due to programming error / packaging
    if (_network_index__WEBPACK_IMPORTED_MODULE_0__.WebRtcNetwork && _network_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketNetwork)
        return true;
    return false;
}
function CAPI_WebRtcNetwork_IsBrowserSupported() {
    if (RTCPeerConnection && RTCDataChannel)
        return true;
    return false;
}
function CAPI_WebRtcNetwork_Create(lConfiguration) {
    var lIndex = gCAPI_WebRtcNetwork_InstancesNextIndex;
    gCAPI_WebRtcNetwork_InstancesNextIndex++;
    if (lConfiguration == null || typeof lConfiguration !== 'string' || lConfiguration.length === 0) {
        _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LogError("invalid configuration. Returning -1! Config: " + lConfiguration);
        return -1;
    }
    else {
        const config = new _network_index__WEBPACK_IMPORTED_MODULE_0__.NetworkConfig();
        config.FromJson(lConfiguration);
        gCAPI_WebRtcNetwork_Instances[lIndex] = new _network_index__WEBPACK_IMPORTED_MODULE_0__.WebRtcNetwork(config);
    }
    return lIndex;
}
function CAPI_WebRtcNetwork_Release(lIndex) {
    if (lIndex in gCAPI_WebRtcNetwork_Instances) {
        gCAPI_WebRtcNetwork_Instances[lIndex].Dispose();
        delete gCAPI_WebRtcNetwork_Instances[lIndex];
    }
}
function CAPI_WebRtcNetwork_Connect(lIndex, lRoom) {
    return gCAPI_WebRtcNetwork_Instances[lIndex].Connect(lRoom);
}
function CAPI_WebRtcNetwork_StartServer(lIndex, lRoom) {
    gCAPI_WebRtcNetwork_Instances[lIndex].StartServer(lRoom);
}
function CAPI_WebRtcNetwork_StopServer(lIndex) {
    gCAPI_WebRtcNetwork_Instances[lIndex].StopServer();
}
function CAPI_WebRtcNetwork_Disconnect(lIndex, lConnectionId) {
    gCAPI_WebRtcNetwork_Instances[lIndex].Disconnect(new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(lConnectionId));
}
function CAPI_WebRtcNetwork_Shutdown(lIndex) {
    gCAPI_WebRtcNetwork_Instances[lIndex].Shutdown();
}
function CAPI_WebRtcNetwork_Update(lIndex) {
    gCAPI_WebRtcNetwork_Instances[lIndex].Update();
}
function CAPI_WebRtcNetwork_Flush(lIndex) {
    gCAPI_WebRtcNetwork_Instances[lIndex].Flush();
}
function CAPI_WebRtcNetwork_SendData(lIndex, lConnectionId, lUint8ArrayData, lReliable) {
    gCAPI_WebRtcNetwork_Instances[lIndex].SendData(new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(lConnectionId), lUint8ArrayData, lReliable);
}
//helper for emscripten
function CAPI_WebRtcNetwork_SendDataEm(lIndex, lConnectionId, lUint8ArrayData, lUint8ArrayDataOffset, lUint8ArrayDataLength, lReliable) {
    //console.debug("SendDataEm: " + lReliable + " length " + lUint8ArrayDataLength + " to " + lConnectionId);
    var arrayBuffer = new Uint8Array(lUint8ArrayData.buffer, lUint8ArrayDataOffset, lUint8ArrayDataLength);
    return gCAPI_WebRtcNetwork_Instances[lIndex].SendData(new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(lConnectionId), arrayBuffer, lReliable);
}
function CAPI_WebRtcNetwork_GetBufferedAmount(lIndex, lConnectionId, lReliable) {
    return gCAPI_WebRtcNetwork_Instances[lIndex].GetBufferedAmount(new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(lConnectionId), lReliable);
}
function CAPI_WebRtcNetwork_Dequeue(lIndex) {
    return gCAPI_WebRtcNetwork_Instances[lIndex].Dequeue();
}
function CAPI_WebRtcNetwork_Peek(lIndex) {
    return gCAPI_WebRtcNetwork_Instances[lIndex].Peek();
}
/**Allows to peek into the next event to figure out its length and allocate
 * the memory needed to store it before calling
 *      CAPI_WebRtcNetwork_DequeueEm
 *
 * @param {type} lIndex
 * @returns {Number}
 */
function CAPI_WebRtcNetwork_PeekEventDataLength(lIndex) {
    var lNetEvent = gCAPI_WebRtcNetwork_Instances[lIndex].Peek();
    return CAPI_WebRtcNetwork_CheckEventLength(lNetEvent);
}
//helper
function CAPI_WebRtcNetwork_CheckEventLength(lNetEvent) {
    if (lNetEvent == null) {
        //invalid event
        return -1;
    }
    else if (lNetEvent.RawData == null) {
        //no data
        return 0;
    }
    else if (typeof lNetEvent.RawData === "string") {
        //no user strings are allowed thus we get away with counting the characters
        //(ASCII only!)
        return lNetEvent.RawData.length;
    }
    else //message event types 1 and 2 only? check for it?
     {
        //its not null and not a string. can only be a Uint8Array if we didn't
        //mess something up in the implementation
        return lNetEvent.RawData.length;
    }
}
function CAPI_WebRtcNetwork_EventDataToUint8Array(data, dataUint8Array, dataOffset, dataLength) {
    //data can be null, string or Uint8Array
    //return value will be the length of data we used
    if (data == null) {
        return 0;
    }
    else if ((typeof data) === "string") {
        //in case we don't get a large enough array we need to cut off the string
        var i = 0;
        for (i = 0; i < data.length && i < dataLength; i++) {
            dataUint8Array[dataOffset + i] = data.charCodeAt(i);
        }
        return i;
    }
    else {
        var i = 0;
        //in case we don't get a large enough array we need to cut off the string
        for (i = 0; i < data.length && i < dataLength; i++) {
            dataUint8Array[dataOffset + i] = data[i];
        }
        return i;
    }
}
//Version for emscripten or anything that doesn't have a garbage collector.
// The memory for everything needs to be allocated before the call.
function CAPI_WebRtcNetwork_DequeueEm(lIndex, lTypeIntArray, lTypeIntIndex, lConidIntArray, lConidIndex, lDataUint8Array, lDataOffset, lDataLength, lDataLenIntArray, lDataLenIntIndex) {
    var nEvt = CAPI_WebRtcNetwork_Dequeue(lIndex);
    if (nEvt == null)
        return false;
    lTypeIntArray[lTypeIntIndex] = nEvt.Type;
    lConidIntArray[lConidIndex] = nEvt.ConnectionId.id;
    //console.debug("event" + nEvt.netEventType);
    var length = CAPI_WebRtcNetwork_EventDataToUint8Array(nEvt.RawData, lDataUint8Array, lDataOffset, lDataLength);
    lDataLenIntArray[lDataLenIntIndex] = length; //return the length if so the user knows how much of the given array is used
    return true;
}
function CAPI_WebRtcNetwork_PeekEm(lIndex, lTypeIntArray, lTypeIntIndex, lConidIntArray, lConidIndex, lDataUint8Array, lDataOffset, lDataLength, lDataLenIntArray, lDataLenIntIndex) {
    var nEvt = CAPI_WebRtcNetwork_Peek(lIndex);
    if (nEvt == null)
        return false;
    lTypeIntArray[lTypeIntIndex] = nEvt.Type;
    lConidIntArray[lConidIndex] = nEvt.ConnectionId.id;
    //console.debug("event" + nEvt.netEventType);
    var length = CAPI_WebRtcNetwork_EventDataToUint8Array(nEvt.RawData, lDataUint8Array, lDataOffset, lDataLength);
    lDataLenIntArray[lDataLenIntIndex] = length; //return the length if so the user knows how much of the given array is used
    return true;
}
function CAPI_WebRtcNetwork_RequestStats(lIndex) {
    gCAPI_WebRtcNetwork_Instances[lIndex].RequestStats();
}
function CAPI_WebRtcNetwork_DequeueRtcEvent(lIndex, lTypeIntArray, lTypeIntIndex, lConidIntArray, lConIntIndex) {
    const evt = gCAPI_WebRtcNetwork_Instances[lIndex].DequeueRtcEvent();
    if (evt && evt.EventType == _network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_3__.RtcEventType.Stats) {
        const stats = evt;
        lTypeIntArray[lTypeIntIndex] = stats.EventType;
        lConidIntArray[lConIntIndex] = stats.ConnectionId.id;
        const res = JSON.stringify(stats.Reports);
        return res;
    }
    else {
        return null;
    }
}
function CAPI_MediaNetwork_IsAvailable() {
    if (_media_browser_index__WEBPACK_IMPORTED_MODULE_2__.BrowserMediaNetwork && _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.BrowserWebRtcCall)
        return true;
    return false;
}
function CAPI_MediaNetwork_HasUserMedia() {
    if (navigator && navigator.mediaDevices)
        return true;
    return false;
}
function CAPI_MediaNetwork_Create(lJsonConfiguration) {
    let config = new _network_index__WEBPACK_IMPORTED_MODULE_0__.NetworkConfig();
    config.FromJson(lJsonConfiguration);
    let mediaNetwork = new _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.BrowserMediaNetwork(config);
    var lIndex = gCAPI_WebRtcNetwork_InstancesNextIndex;
    gCAPI_WebRtcNetwork_InstancesNextIndex++;
    gCAPI_WebRtcNetwork_Instances[lIndex] = mediaNetwork;
    return lIndex;
}
//Configure(config: MediaConfig): void;
function CAPI_MediaNetwork_Configure(lIndex, audio, video, minWidth, minHeight, maxWidth, maxHeight, idealWidth, idealHeight, minFps, maxFps, idealFps, deviceName = "", videoCodecs = [], videoBitrateKbits = -1, videoContentHint = "", audioInputDevice = "") {
    let config = new _media_index__WEBPACK_IMPORTED_MODULE_1__.MediaConfig();
    config.Audio = audio;
    config.Video = video;
    config.MinWidth = minWidth;
    config.MinHeight = minHeight;
    config.MaxWidth = maxWidth;
    config.MaxHeight = maxHeight;
    config.IdealWidth = idealWidth;
    config.IdealHeight = idealHeight;
    config.MinFps = minFps;
    config.MaxFps = maxFps;
    config.IdealFps = idealFps;
    config.VideoDeviceName = deviceName;
    if (videoCodecs && videoCodecs.length > 0)
        config.VideoCodecs = videoCodecs;
    //the API between C# and JS does not support null or optional values
    //avoid setting any invalid values and use the default java script values instead
    if (videoBitrateKbits > 0)
        config.VideoBitrateKbits = videoBitrateKbits;
    //Will keep the value at null if the C api sets ""
    if (videoContentHint)
        config.VideoContentHint = videoContentHint;
    if (audioInputDevice)
        config.AudioInputDevice = audioInputDevice;
    config.FrameUpdates = true;
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    mediaNetwork.Configure(config);
}
//GetConfigurationState(): MediaConfigurationState;
function CAPI_MediaNetwork_GetConfigurationState(lIndex) {
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    return mediaNetwork.GetConfigurationState();
}
function CAPI_MediaNetwork_GetConfigurationError_Length(lIndex) {
    const mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    const err = mediaNetwork.GetConfigurationError();
    if (err == null) {
        return 0;
    }
    return err.length;
}
//Note: not yet glued to the C# version!
//GetConfigurationError(): string;
function CAPI_MediaNetwork_GetConfigurationError(lIndex) {
    const mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    const err = mediaNetwork.GetConfigurationError();
    if (err == null)
        return "";
    return err;
}
//ResetConfiguration(): void;
function CAPI_MediaNetwork_ResetConfiguration(lIndex) {
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    return mediaNetwork.ResetConfiguration();
}
//TryGetFrame(id: ConnectionId): RawFrame;
function CAPI_MediaNetwork_TryGetFrame(lIndex, lConnectionId, lWidthInt32Array, lWidthIntArrayIndex, lHeightInt32Array, lHeightIntArrayIndex, lBufferUint8Array, lBufferUint8ArrayOffset, lBufferUint8ArrayLength) {
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    let frame = mediaNetwork.TryGetFrame(new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(lConnectionId));
    if (frame == null || frame.Buffer == null) {
        return false;
    }
    else {
        lWidthInt32Array[lWidthIntArrayIndex] = frame.Width;
        lHeightInt32Array[lHeightIntArrayIndex] = frame.Height;
        for (let i = 0; i < lBufferUint8ArrayLength && i < frame.Buffer.length; i++) {
            lBufferUint8Array[lBufferUint8ArrayOffset + i] = frame.Buffer[i];
        }
        return true;
    }
}
function CAPI_MediaNetwork_TryGetFrame_ToTexture(lIndex, lConnectionId, lWidth, lHeight, gl, texture) {
    //console.log("CAPI_MediaNetwork_TryGetFrame_ToTexture");
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    let frame = mediaNetwork.TryGetFrame(new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(lConnectionId));
    if (frame == null) {
        return false;
    }
    else if (frame.Width != lWidth || frame.Height != lHeight) {
        _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LW("CAPI_MediaNetwork_TryGetFrame_ToTexture failed. Width height expected: " + frame.Width + "x" + frame.Height + " but received " + lWidth + "x" + lHeight);
        return false;
    }
    else {
        frame.ToTexture(gl, texture);
        return true;
    }
}
/*
export function CAPI_MediaNetwork_TryGetFrame_ToTexture2(lIndex: number, lConnectionId: number,
    lWidthInt32Array: Int32Array, lWidthIntArrayIndex: number,
    lHeightInt32Array: Int32Array, lHeightIntArrayIndex: number,
    gl:WebGL2RenderingContext): WebGLTexture
{
    //console.log("CAPI_MediaNetwork_TryGetFrame_ToTexture");
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex] as BrowserMediaNetwork;
    let frame = mediaNetwork.TryGetFrame(new ConnectionId(lConnectionId));

    if (frame == null) {
        return false;
    } else {
        lWidthInt32Array[lWidthIntArrayIndex] = frame.Width;
        lHeightInt32Array[lHeightIntArrayIndex] = frame.Height;
        let texture  = frame.ToTexture2(gl);
        return texture;
    }
}
*/
function CAPI_MediaNetwork_TryGetFrame_Resolution(lIndex, lConnectionId, lWidthInt32Array, lWidthIntArrayIndex, lHeightInt32Array, lHeightIntArrayIndex) {
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    let frame = mediaNetwork.PeekFrame(new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(lConnectionId));
    if (frame == null) {
        return false;
    }
    else {
        lWidthInt32Array[lWidthIntArrayIndex] = frame.Width;
        lHeightInt32Array[lHeightIntArrayIndex] = frame.Height;
        return true;
    }
}
//Returns the frame buffer size or -1 if no frame is available
function CAPI_MediaNetwork_TryGetFrameDataLength(lIndex, connectionId) {
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    let frame = mediaNetwork.PeekFrame(new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(connectionId));
    let length = -1;
    //added frame.Buffer != null as the frame might be a LazyFrame just creating a copy of the html video element
    //in the moment frame.Buffer is called. if this fails for any reasion it might return null despite
    //the frame object itself being available
    if (frame != null && frame.Buffer != null) {
        length = frame.Buffer.length;
    }
    //SLog.L("data length:" + length);
    return length;
}
function CAPI_MediaNetwork_SetVolume(lIndex, volume, connectionId) {
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    mediaNetwork.SetVolume(volume, new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(connectionId));
}
function CAPI_MediaNetwork_SetVolumePan(lIndex, volume, pan, connectionId) {
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    mediaNetwork.SetVolumePan(volume, pan, new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(connectionId));
}
function CAPI_MediaNetwork_HasAudioTrack(lIndex, connectionId) {
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    return mediaNetwork.HasAudioTrack(new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(connectionId));
}
function CAPI_MediaNetwork_HasVideoTrack(lIndex, connectionId) {
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    return mediaNetwork.HasVideoTrack(new _network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId(connectionId));
}
function CAPI_MediaNetwork_SetMute(lIndex, value) {
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    mediaNetwork.SetMute(value);
}
function CAPI_MediaNetwork_IsMute(lIndex) {
    let mediaNetwork = gCAPI_WebRtcNetwork_Instances[lIndex];
    return mediaNetwork.IsMute();
}
function CAPI_DeviceApi_Update() {
    _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.DeviceApi.Update();
}
function CAPI_DeviceApi_RequestUpdate() {
    _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.DeviceApi.RequestUpdate();
}
function CAPI_DeviceApi_LastUpdate() {
    return _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.DeviceApi.LastUpdate;
}
function CAPI_Media_GetVideoDevices_Length() {
    return _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.Media.SharedInstance.GetVideoDevices().length;
}
function CAPI_Media_GetVideoDevices(index) {
    const devs = _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.Media.SharedInstance.GetVideoDevices();
    if (devs.length > index) {
        return devs[index];
    }
    else {
        _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LE("Requested video device with index " + index + " does not exist.");
        //it needs to be "" to behave the same to the C++ API. std::string can't be null
        return "";
    }
}
function CAPI_Media_GetAudioInputDevices_Length() {
    return _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.Media.SharedInstance.GetAudioInputDevices().length;
}
function CAPI_Media_GetAudioInputDevices(index) {
    const devs = _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.Media.SharedInstance.GetAudioInputDevices();
    if (devs.length > index) {
        return JSON.stringify(devs[index]);
    }
    else {
        _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LE("Requested audio input device with index " + index + " does not exist.");
        //it needs to be "" to behave the same to the C++ API. std::string can't be null
        return "";
    }
}
function CAPI_VideoInput_AddCanvasDevice(query, name, width, height, fps) {
    let canvas = document.querySelector(query);
    if (canvas) {
        console.debug("CAPI_VideoInput_AddCanvasDevice", { query, name, width, height, fps });
        if (width <= 0 || height <= 0) {
            width = canvas.width;
            height = canvas.height;
        }
        _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.Media.SharedInstance.VideoInput.AddCanvasDevice(canvas, name, width, height, fps); //, width, height, fps);
        return true;
    }
    return false;
}
function CAPI_VideoInput_AddDevice(name, width, height, fps) {
    _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.Media.SharedInstance.VideoInput.AddDevice(name, width, height, fps);
}
function CAPI_VideoInput_RemoveDevice(name) {
    _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.Media.SharedInstance.VideoInput.RemoveDevice(name);
}
function CAPI_VideoInput_UpdateFrame(name, lBufferUint8Array, lBufferUint8ArrayOffset, lBufferUint8ArrayLength, width, height, rotation, firstRowIsBottom) {
    let dataPtrClamped = null;
    if (lBufferUint8Array && lBufferUint8ArrayLength > 0) {
        dataPtrClamped = new Uint8ClampedArray(lBufferUint8Array.buffer, lBufferUint8ArrayOffset, lBufferUint8ArrayLength);
    }
    return _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.Media.SharedInstance.VideoInput.UpdateFrame(name, dataPtrClamped, width, height, _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.VideoInputType.ARGB, rotation, firstRowIsBottom);
}
function CAPI_Media_EnableScreenCapture(name, captureAudio) {
    return _media_browser_index__WEBPACK_IMPORTED_MODULE_2__.Media.SharedInstance.EnableScreenCapture(name, captureAudio);
}
function GetUnityCanvas() {
    if (gCAPI_Canvas !== null)
        return gCAPI_Canvas;
    _network_index__WEBPACK_IMPORTED_MODULE_0__.SLog.LogWarning("Using GetUnityCanvas without a known cavans reference.");
    return document.querySelector("canvas");
}
function GetUnityContext() {
    return GetUnityCanvas().getContext("webgl2");
}


/***/ }),

/***/ "./src/awrtc/unity/index.ts":
/*!**********************************!*\
  !*** ./src/awrtc/unity/index.ts ***!
  \**********************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   CAPI_DeviceApi_LastUpdate: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_DeviceApi_LastUpdate),
/* harmony export */   CAPI_DeviceApi_RequestUpdate: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_DeviceApi_RequestUpdate),
/* harmony export */   CAPI_DeviceApi_Update: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_DeviceApi_Update),
/* harmony export */   CAPI_InitAsync: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_InitAsync),
/* harmony export */   CAPI_MediaNetwork_Configure: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_Configure),
/* harmony export */   CAPI_MediaNetwork_Create: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_Create),
/* harmony export */   CAPI_MediaNetwork_GetConfigurationError: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_GetConfigurationError),
/* harmony export */   CAPI_MediaNetwork_GetConfigurationError_Length: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_GetConfigurationError_Length),
/* harmony export */   CAPI_MediaNetwork_GetConfigurationState: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_GetConfigurationState),
/* harmony export */   CAPI_MediaNetwork_HasAudioTrack: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_HasAudioTrack),
/* harmony export */   CAPI_MediaNetwork_HasUserMedia: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_HasUserMedia),
/* harmony export */   CAPI_MediaNetwork_HasVideoTrack: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_HasVideoTrack),
/* harmony export */   CAPI_MediaNetwork_IsAvailable: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_IsAvailable),
/* harmony export */   CAPI_MediaNetwork_IsMute: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_IsMute),
/* harmony export */   CAPI_MediaNetwork_ResetConfiguration: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_ResetConfiguration),
/* harmony export */   CAPI_MediaNetwork_SetMute: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_SetMute),
/* harmony export */   CAPI_MediaNetwork_SetVolume: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_SetVolume),
/* harmony export */   CAPI_MediaNetwork_SetVolumePan: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_SetVolumePan),
/* harmony export */   CAPI_MediaNetwork_TryGetFrame: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_TryGetFrame),
/* harmony export */   CAPI_MediaNetwork_TryGetFrameDataLength: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_TryGetFrameDataLength),
/* harmony export */   CAPI_MediaNetwork_TryGetFrame_Resolution: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_TryGetFrame_Resolution),
/* harmony export */   CAPI_MediaNetwork_TryGetFrame_ToTexture: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_TryGetFrame_ToTexture),
/* harmony export */   CAPI_Media_EnableScreenCapture: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_EnableScreenCapture),
/* harmony export */   CAPI_Media_GetAudioInputDevices: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_GetAudioInputDevices),
/* harmony export */   CAPI_Media_GetAudioInputDevices_Length: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_GetAudioInputDevices_Length),
/* harmony export */   CAPI_Media_GetVideoDevices: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_GetVideoDevices),
/* harmony export */   CAPI_Media_GetVideoDevices_Length: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_GetVideoDevices_Length),
/* harmony export */   CAPI_PollInitState: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_PollInitState),
/* harmony export */   CAPI_SLog_SetLogLevel: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_SLog_SetLogLevel),
/* harmony export */   CAPI_VideoInput_AddCanvasDevice: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_VideoInput_AddCanvasDevice),
/* harmony export */   CAPI_VideoInput_AddDevice: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_VideoInput_AddDevice),
/* harmony export */   CAPI_VideoInput_RemoveDevice: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_VideoInput_RemoveDevice),
/* harmony export */   CAPI_VideoInput_UpdateFrame: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_VideoInput_UpdateFrame),
/* harmony export */   CAPI_WebRtcNetwork_CheckEventLength: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_CheckEventLength),
/* harmony export */   CAPI_WebRtcNetwork_Connect: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Connect),
/* harmony export */   CAPI_WebRtcNetwork_Create: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Create),
/* harmony export */   CAPI_WebRtcNetwork_Dequeue: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Dequeue),
/* harmony export */   CAPI_WebRtcNetwork_DequeueEm: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_DequeueEm),
/* harmony export */   CAPI_WebRtcNetwork_DequeueRtcEvent: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_DequeueRtcEvent),
/* harmony export */   CAPI_WebRtcNetwork_Disconnect: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Disconnect),
/* harmony export */   CAPI_WebRtcNetwork_EventDataToUint8Array: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_EventDataToUint8Array),
/* harmony export */   CAPI_WebRtcNetwork_Flush: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Flush),
/* harmony export */   CAPI_WebRtcNetwork_GetBufferedAmount: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_GetBufferedAmount),
/* harmony export */   CAPI_WebRtcNetwork_IsAvailable: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_IsAvailable),
/* harmony export */   CAPI_WebRtcNetwork_IsBrowserSupported: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_IsBrowserSupported),
/* harmony export */   CAPI_WebRtcNetwork_Peek: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Peek),
/* harmony export */   CAPI_WebRtcNetwork_PeekEm: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_PeekEm),
/* harmony export */   CAPI_WebRtcNetwork_PeekEventDataLength: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_PeekEventDataLength),
/* harmony export */   CAPI_WebRtcNetwork_Release: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Release),
/* harmony export */   CAPI_WebRtcNetwork_RequestStats: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_RequestStats),
/* harmony export */   CAPI_WebRtcNetwork_SendData: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_SendData),
/* harmony export */   CAPI_WebRtcNetwork_SendDataEm: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_SendDataEm),
/* harmony export */   CAPI_WebRtcNetwork_Shutdown: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Shutdown),
/* harmony export */   CAPI_WebRtcNetwork_StartServer: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_StartServer),
/* harmony export */   CAPI_WebRtcNetwork_StopServer: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_StopServer),
/* harmony export */   CAPI_WebRtcNetwork_Update: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Update),
/* harmony export */   GetUnityCanvas: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.GetUnityCanvas),
/* harmony export */   GetUnityContext: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.GetUnityContext),
/* harmony export */   gCAPI_WebRtcNetwork_Instances: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.gCAPI_WebRtcNetwork_Instances),
/* harmony export */   gCAPI_WebRtcNetwork_InstancesNextIndex: () => (/* reexport safe */ _CAPI__WEBPACK_IMPORTED_MODULE_0__.gCAPI_WebRtcNetwork_InstancesNextIndex)
/* harmony export */ });
/* harmony import */ var _CAPI__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./CAPI */ "./src/awrtc/unity/CAPI.ts");



/***/ }),

/***/ "./src/test/BrowserApiTest.ts":
/*!************************************!*\
  !*** ./src/test/BrowserApiTest.ts ***!
  \************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   some_random_export_1: () => (/* binding */ some_random_export_1)
/* harmony export */ });
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
//current setup needs to load everything as a module
function some_random_export_1() {
}
describe("BrowserApiTest_MediaStreamApi", () => {
    beforeEach(() => {
        jasmine.DEFAULT_TIMEOUT_INTERVAL = 10000;
    });
    it("devices", (done) => {
        navigator.mediaDevices.enumerateDevices()
            .then(function (devices) {
            expect(devices).not.toBeNull();
            devices.forEach(function (device) {
                console.log(device.kind + ": " + device.label +
                    " id = " + device.deviceId);
            });
            done();
        })
            .catch(function (err) {
            console.log(err.name + ": " + err.message);
            fail();
        });
    });
    it("devices2", (done) => {
        let gStream;
        let constraints = { video: { deviceId: undefined }, audio: { deviceId: undefined } };
        navigator.mediaDevices.getUserMedia(constraints)
            .then((stream) => {
            //if this stream stops the access to labels disapears again after
            //a few ms (tested in firefox)
            gStream = stream;
            navigator.mediaDevices.enumerateDevices()
                .then(function (devices) {
                expect(devices).not.toBeNull();
                devices.forEach(function (device) {
                    expect(device.label).not.toBeNull();
                    expect(device.label).not.toBe("");
                    console.log(device.kind + ": " + device.label +
                        " id = " + device.deviceId);
                });
                gStream.getTracks().forEach(t => {
                    t.stop();
                });
                done();
            })
                .catch(function (err) {
                console.log(err.name + ": " + err.message);
                fail();
            });
        })
            .catch((err) => {
            console.log(err.name + ": " + err.message);
            fail();
        });
    });
    it("devices3", (done) => {
        let gStream;
        let constraints = { video: true, audio: false };
        navigator.mediaDevices.getUserMedia(constraints)
            .then((stream) => {
            //if this stream stops the access to labels disapears again after
            //a few ms (tested in firefox)
            gStream = stream;
            navigator.mediaDevices.enumerateDevices()
                .then(function (devices) {
                expect(devices).not.toBeNull();
                devices.forEach(function (device) {
                    expect(device.label).not.toBeNull();
                    expect(device.label).not.toBe("");
                    console.log(device.kind + ": " + device.label +
                        " id = " + device.deviceId);
                });
                gStream.getTracks().forEach(t => {
                    t.stop();
                });
                done();
            })
                .catch(function (err) {
                console.log(err.name + ": " + err.message);
                fail();
            });
        })
            .catch((err) => {
            console.log(err.name + ": " + err.message);
            fail();
        });
    });
});


/***/ }),

/***/ "./src/test/CAPITest.ts":
/*!******************************!*\
  !*** ./src/test/CAPITest.ts ***!
  \******************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   CAPITest_export: () => (/* binding */ CAPITest_export)
/* harmony export */ });
/* harmony import */ var _awrtc_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../awrtc/index */ "./src/awrtc/index.ts");

function CAPITest_export() {
}
describe("CAPITest", () => {
    beforeEach((done) => {
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.ResetSharedInstance();
        done();
    });
    it("CAPI_WebRtcNetwork_Create", () => {
        const in_json = '{"IceServers":[{"urls":["turn.y-not.app:12345"],"credential":"testpass","username":"testuser"}],"SignalingUrl":"ws://s.y-not.app","IsConference":true,"MaxIceRestart":2,"KeepSignalingAlive":true}';
        let lIndex = (0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Create)(in_json);
        expect(lIndex).toBeGreaterThan(-1);
        const created_network = _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.gCAPI_WebRtcNetwork_Instances[lIndex];
        expect(created_network).toBeTruthy();
        const out_config = created_network.NetworkConfig;
        //we expect the config to be equal but ignore SignalingNetwork as this might be changed on runtime
        out_config.SignalingNetwork = null;
        //compare the two NetworkConfig as the json formatting will be different
        const in_config = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetworkConfig();
        in_config.FromJson(in_json);
        const isEqual = in_config.IsEqual(out_config);
        expect(isEqual).toBe(true);
        (0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Release)(lIndex);
        expect(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.gCAPI_WebRtcNetwork_Instances[lIndex]).not.toBeTruthy();
    });
    it("CAPI_MediaNetwork_Create", () => {
        const in_json = '{"IceServers":[{"urls":["turn.y-not.app:12345"],"credential":"testpass","username":"testuser"}],"SignalingUrl":"ws://s.y-not.app","IsConference":true,"MaxIceRestart":2,"KeepSignalingAlive":true}';
        let lIndex = (0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_MediaNetwork_Create)(in_json);
        expect(lIndex).toBeGreaterThan(-1);
        const created_network = _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.gCAPI_WebRtcNetwork_Instances[lIndex];
        expect(created_network).toBeTruthy();
        const out_config = created_network.NetworkConfig;
        //we expect the config to be equal but ignore SignalingNetwork as this might be changed on runtime
        out_config.SignalingNetwork = null;
        //compare the two NetworkConfig as the json formatting will be different
        const in_config = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetworkConfig();
        in_config.FromJson(in_json);
        const isEqual = in_config.IsEqual(out_config);
        expect(isEqual).toBe(true);
        (0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_WebRtcNetwork_Release)(lIndex);
        expect(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.gCAPI_WebRtcNetwork_Instances[lIndex]).not.toBeTruthy();
    });
});


/***/ }),

/***/ "./src/test/CallTest.ts":
/*!******************************!*\
  !*** ./src/test/CallTest.ts ***!
  \******************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   CallTestHelper: () => (/* binding */ CallTestHelper)
/* harmony export */ });
/* harmony import */ var _awrtc_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../awrtc/index */ "./src/awrtc/index.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

class CallTestHelper {
    static CreateCall(video, audio) {
        var nconfig = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetworkConfig();
        nconfig.SignalingUrl = "wss://s.y-not.app:443/test";
        var call = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserWebRtcCall(nconfig);
        return call;
    }
}
describe("CallTest", () => {
    var originalTimeout;
    beforeEach(() => {
        originalTimeout = jasmine.DEFAULT_TIMEOUT_INTERVAL;
        jasmine.DEFAULT_TIMEOUT_INTERVAL = 20000;
    });
    afterEach(() => {
        jasmine.DEFAULT_TIMEOUT_INTERVAL = originalTimeout;
    });
    it("CallTest normal", () => {
        expect(true).toBe(true);
    });
    it("CallTest async", (done) => {
        setTimeout(() => {
            expect(true).toBe(true);
            done();
        }, 1000);
    });
    it("Send test", (done) => {
        var call1 = null;
        var call2 = null;
        let call1ToCall2;
        let call2ToCall1;
        var address = "webunittest";
        var teststring1 = "teststring1";
        var teststring2 = "teststring2";
        var testdata1 = new Uint8Array([1, 2]);
        var testdata2 = new Uint8Array([3, 4]);
        call1 = CallTestHelper.CreateCall(false, false);
        expect(call1).not.toBeNull();
        call2 = CallTestHelper.CreateCall(false, false);
        expect(call2).not.toBeNull();
        expect(true).toBe(true);
        var mconfig = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        mconfig.Audio = false;
        mconfig.Video = false;
        call1.addEventListener((sender, args) => {
            if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                console.debug("call1 ConfigurationComplete");
                call2.Configure(mconfig);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.WaitForIncomingCall) {
                console.debug("call1 WaitForIncomingCall");
                call2.Call(address);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.CallAccepted) {
                let ar = args;
                call1ToCall2 = ar.ConnectionId;
                //wait for message
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.Message) {
                console.debug("call1 Message");
                var margs = args;
                expect(margs.Content).toBe(teststring1);
                expect(margs.Reliable).toBe(true);
                call1.Send(teststring2, false, call1ToCall2);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.DataMessage) {
                console.debug("call1 DataMessage");
                var dargs = args;
                expect(dargs.Reliable).toBe(true);
                var recdata = dargs.Content;
                expect(testdata1[0]).toBe(recdata[0]);
                expect(testdata1[1]).toBe(recdata[1]);
                console.debug("call1 send DataMessage");
                call1.SendData(testdata2, false, call1ToCall2);
            }
            else {
                console.error("unexpected event: " + args.Type);
                expect(true).toBe(false);
            }
        });
        call2.addEventListener((sender, args) => {
            if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                console.debug("call2 ConfigurationComplete");
                call1.Listen(address);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.CallAccepted) {
                let ar = args;
                call2ToCall1 = ar.ConnectionId;
                expect(call2ToCall1).toBeDefined();
                call2.Send(teststring1);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.Message) {
                console.debug("call2 Message");
                var margs = args;
                expect(margs.Content).toBe(teststring2);
                expect(margs.Reliable).toBe(false);
                console.debug("call2 send DataMessage " + call2ToCall1.id);
                call2.SendData(testdata1, true, call2ToCall1);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.DataMessage) {
                console.debug("call2 DataMessage");
                var dargs = args;
                expect(dargs.Reliable).toBe(false);
                var recdata = dargs.Content;
                expect(testdata2[0]).toBe(recdata[0]);
                expect(testdata2[1]).toBe(recdata[1]);
                done();
            }
            else {
                console.error("unexpected event: " + args.Type);
                expect(true).toBe(false);
            }
        });
        setInterval(() => {
            call1.Update();
            call2.Update();
        }, 50);
        call1.Configure(mconfig);
    });
    it("Send test", (done) => {
        var call1 = null;
        var call2 = null;
        let call1ToCall2;
        let call2ToCall1;
        var address = "webunittest";
        var teststring1 = "teststring1";
        var teststring2 = "teststring2";
        var testdata1 = new Uint8Array([1, 2]);
        var testdata2 = new Uint8Array([3, 4]);
        call1 = CallTestHelper.CreateCall(false, false);
        expect(call1).not.toBeNull();
        call2 = CallTestHelper.CreateCall(false, false);
        expect(call2).not.toBeNull();
        expect(true).toBe(true);
        var mconfig = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        mconfig.Audio = false;
        mconfig.Video = false;
        call1.addEventListener((sender, args) => {
            if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                console.debug("call1 ConfigurationComplete");
                call2.Configure(mconfig);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.WaitForIncomingCall) {
                console.debug("call1 WaitForIncomingCall");
                call2.Call(address);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.CallAccepted) {
                let ar = args;
                call1ToCall2 = ar.ConnectionId;
                //wait for message
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.Message) {
                console.debug("call1 Message");
                var margs = args;
                expect(margs.Content).toBe(teststring1);
                expect(margs.Reliable).toBe(true);
                call1.Send(teststring2, false, call1ToCall2);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.DataMessage) {
                console.debug("call1 DataMessage");
                var dargs = args;
                expect(dargs.Reliable).toBe(true);
                var recdata = dargs.Content;
                expect(testdata1[0]).toBe(recdata[0]);
                expect(testdata1[1]).toBe(recdata[1]);
                console.debug("call1 send DataMessage");
                call1.SendData(testdata2, false, call1ToCall2);
            }
            else {
                console.error("unexpected event: " + args.Type);
                expect(true).toBe(false);
            }
        });
        call2.addEventListener((sender, args) => {
            if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                console.debug("call2 ConfigurationComplete");
                call1.Listen(address);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.CallAccepted) {
                let ar = args;
                call2ToCall1 = ar.ConnectionId;
                expect(call2ToCall1).toBeDefined();
                call2.Send(teststring1);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.Message) {
                console.debug("call2 Message");
                var margs = args;
                expect(margs.Content).toBe(teststring2);
                expect(margs.Reliable).toBe(false);
                console.debug("call2 send DataMessage " + call2ToCall1.id);
                call2.SendData(testdata1, true, call2ToCall1);
            }
            else if (args.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.DataMessage) {
                console.debug("call2 DataMessage");
                var dargs = args;
                expect(dargs.Reliable).toBe(false);
                var recdata = dargs.Content;
                expect(testdata2[0]).toBe(recdata[0]);
                expect(testdata2[1]).toBe(recdata[1]);
                done();
            }
            else {
                console.error("unexpected event: " + args.Type);
                expect(true).toBe(false);
            }
        });
        setInterval(() => {
            call1.Update();
            call2.Update();
        }, 50);
        call1.Configure(mconfig);
    });
    it("Video frame receive test", (done) => {
        var call1 = null;
        var call2 = null;
        let call1ToCall2;
        let call2ToCall1;
        var address = "webunittest";
        var remoteFrameCount = 0;
        var localFrameCount = 0;
        var maxFrames = 10;
        call1 = CallTestHelper.CreateCall(false, false);
        expect(call1).not.toBeNull();
        call2 = CallTestHelper.CreateCall(false, false);
        expect(call2).not.toBeNull();
        var configSend = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        configSend.Audio = false;
        configSend.Video = true;
        configSend.FrameUpdates = true;
        var configRec = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        configRec.Audio = false;
        configRec.Video = false;
        configRec.FrameUpdates = true;
        let localMediaUpdateReceived = null;
        let remoteMediaUpdateReceived = null;
        call1.addEventListener((sender, args) => {
            if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                console.debug("call1 ConfigurationComplete");
                call2.Configure(configRec);
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.WaitForIncomingCall) {
                console.debug("call1 WaitForIncomingCall");
                call2.Call(address);
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.CallAccepted) {
                let ar = args;
                call1ToCall2 = ar.ConnectionId;
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.FrameUpdate) {
                console.debug("call1 FrameUpdate");
                localFrameCount++;
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.MediaUpdate) {
                console.debug("call1 MediaUpdate");
                localMediaUpdateReceived = args;
                expect(localMediaUpdateReceived).toBeDefined();
                expect(localMediaUpdateReceived).not.toBeNull();
                expect(localMediaUpdateReceived.ConnectionId.id).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID.id);
                expect(localMediaUpdateReceived.IsRemote).toBe(false);
                expect(localMediaUpdateReceived.VideoElement).toBeDefined();
                expect(localMediaUpdateReceived.VideoElement).not.toBeNull();
            }
        });
        call2.addEventListener((sender, args) => {
            if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                console.debug("call2 ConfigurationComplete");
                call1.Listen(address);
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.CallAccepted) {
                let ar = args;
                call2ToCall1 = ar.ConnectionId;
                expect(call2.HasAudioTrack(call2ToCall1)).toBeFalse();
                expect(call2.HasVideoTrack(call2ToCall1)).toBeTrue();
                console.debug("call2 CallAcceptedEventArgs");
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.MediaUpdate) {
                console.debug("call2 MediaUpdate");
                remoteMediaUpdateReceived = args;
                expect(remoteMediaUpdateReceived).toBeDefined();
                expect(remoteMediaUpdateReceived).not.toBeNull();
                expect(remoteMediaUpdateReceived.ConnectionId.id).toBe(call2ToCall1.id);
                expect(remoteMediaUpdateReceived.IsRemote).toBe(true);
                expect(remoteMediaUpdateReceived.VideoElement).toBeDefined();
                expect(remoteMediaUpdateReceived.VideoElement).not.toBeNull();
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.FrameUpdate) {
                console.debug("call2 FrameUpdate");
                remoteFrameCount++;
            }
            if (remoteFrameCount >= maxFrames) {
                expect(localFrameCount).toBeGreaterThanOrEqual(maxFrames);
                call1.Dispose();
                call2.Dispose();
                done();
            }
        });
        setInterval(() => {
            call1.Update();
            call2.Update();
        }, 50);
        call1.Configure(configSend);
    });
    /** Test starts with video off for both sides, connects two peers and
     * then turns on video.
     *
     */
    it("CallReconfiguration_video_off_to_on", (done) => {
        let call1ToCall2;
        let call2ToCall1;
        var address = "webunittest";
        var frameCount = 0;
        var maxFrames = 10;
        var nconfig = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetworkConfig();
        nconfig.SignalingUrl = "wss://s.y-not.app:443/test";
        nconfig.KeepSignalingAlive = true;
        var call1 = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserWebRtcCall(nconfig);
        var call2 = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserWebRtcCall(nconfig);
        var configSend = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        configSend.Audio = false;
        configSend.Video = false;
        configSend.FrameUpdates = true;
        var configRec = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        configRec.Audio = false;
        configRec.Video = false;
        configRec.FrameUpdates = true;
        let mediaUpdateReceived = null;
        let phrase2;
        const call1Listener = (sender, args) => {
            if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                console.debug("call1 ConfigurationComplete");
                call2.Configure(configRec);
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.WaitForIncomingCall) {
                console.debug("call1 WaitForIncomingCall");
                call2.Call(address);
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.CallAccepted) {
                let ar = args;
                call1ToCall2 = ar.ConnectionId;
            }
        };
        call1.addEventListener(call1Listener);
        const call2Listener = (sender, args) => {
            if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                console.debug("call2 ConfigurationComplete");
                call1.Listen(address);
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.CallAccepted) {
                let ar = args;
                call2ToCall1 = ar.ConnectionId;
                expect(call2.HasAudioTrack(call2ToCall1)).toBeFalse();
                expect(call2.HasVideoTrack(call2ToCall1)).toBeFalse();
                console.debug("call2 CallAcceptedEventArgs");
                setTimeout(() => {
                    phrase2();
                }, 500);
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.MediaUpdate) {
                console.debug("call2 MediaUpdate");
                mediaUpdateReceived = args;
                fail("MediaUpdate triggered without video");
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.FrameUpdate) {
                console.debug("call2 FrameUpdate");
                fail("FrameUpdate triggered without video");
            }
        };
        call2.addEventListener(call2Listener);
        //we trigger this once we successful established a call change the configuration
        phrase2 = () => {
            let localFrameCount = 0;
            let remoteFrameCount = 0;
            call1.removeEventListener(call1Listener);
            call2.removeEventListener(call2Listener);
            const call1ListenerP2 = (sender, args) => {
                if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                    console.debug("call1 ConfigurationComplete");
                }
                else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.FrameUpdate) {
                    console.debug("call1 FrameUpdate");
                    localFrameCount++;
                }
            };
            call1.addEventListener(call1ListenerP2);
            const call2ListenerP2 = (sender, args) => {
                if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                    console.debug("call2 ConfigurationComplete");
                    call1.Listen(address);
                }
                else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.MediaUpdate) {
                    console.debug("call2 MediaUpdate");
                    const remoteMediaUpdateReceived = args;
                    expect(remoteMediaUpdateReceived).toBeDefined();
                    expect(remoteMediaUpdateReceived).not.toBeNull();
                    expect(remoteMediaUpdateReceived.ConnectionId.id).toBe(call2ToCall1.id);
                    expect(remoteMediaUpdateReceived.IsRemote).toBe(true);
                    expect(remoteMediaUpdateReceived.VideoElement).toBeDefined();
                    expect(remoteMediaUpdateReceived.VideoElement).not.toBeNull();
                }
                else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.FrameUpdate) {
                    console.debug("call2 FrameUpdate");
                    remoteFrameCount++;
                    if (remoteFrameCount >= maxFrames) {
                        expect(localFrameCount).toBeGreaterThanOrEqual(maxFrames);
                        call1.Dispose();
                        call2.Dispose();
                        done();
                    }
                }
            };
            call2.addEventListener(call2ListenerP2);
            configSend.Video = true;
            call1.Configure(configSend);
        };
        setInterval(() => {
            call1.Update();
            call2.Update();
        }, 50);
        call1.Configure(configSend);
    });
    /**
     * Same as above but with audio enabled. This means the MediaStream already exists when video turns on
     * and the track must be added later.
     */
    it("CallReconfiguration_audio_on_video_off_to_on", (done) => {
        let call1ToCall2;
        let call2ToCall1;
        var address = "webunittest";
        var frameCount = 0;
        var maxFrames = 10;
        var nconfig = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetworkConfig();
        nconfig.SignalingUrl = "wss://s.y-not.app:443/test";
        nconfig.KeepSignalingAlive = true;
        var call1 = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserWebRtcCall(nconfig);
        var call2 = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserWebRtcCall(nconfig);
        var configSend = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        configSend.Audio = true;
        configSend.Video = false;
        configSend.FrameUpdates = false;
        var configRec = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        configRec.Audio = false;
        configRec.Video = false;
        configRec.FrameUpdates = true;
        let mediaUpdateReceived = null;
        let phrase2;
        let call1Listener = (sender, args) => {
            if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                console.debug("call1 ConfigurationComplete");
                call2.Configure(configRec);
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.WaitForIncomingCall) {
                console.debug("call1 WaitForIncomingCall");
                call2.Call(address);
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.CallAccepted) {
                let ar = args;
                call1ToCall2 = ar.ConnectionId;
            }
        };
        call1.addEventListener(call1Listener);
        let call2Listener = (sender, args) => {
            if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                console.debug("call2 ConfigurationComplete");
                call1.Listen(address);
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.CallAccepted) {
                let ar = args;
                call2ToCall1 = ar.ConnectionId;
                expect(call2.HasAudioTrack(call2ToCall1)).toBeTrue();
                expect(call2.HasVideoTrack(call2ToCall1)).toBeFalse();
                console.debug("call2 CallAcceptedEventArgs");
                setTimeout(() => {
                    phrase2();
                }, 500);
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.MediaUpdate) {
                console.debug("call2 MediaUpdate");
                mediaUpdateReceived = args;
                expect(mediaUpdateReceived.VideoElement).not.toBeNull();
                //TODO: check if we have audio but not video active
                //typings missing?
                //mediaUpdateReceived.VideoElement.audioTracks
            }
            else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.FrameUpdate) {
                console.debug("call2 FrameUpdate");
                fail("FrameUpdate triggered without video");
            }
        };
        call2.addEventListener(call2Listener);
        //we trigger this once we successful established a call change the configuration
        phrase2 = () => {
            let localFrameCount = 0;
            let remoteFrameCount = 0;
            call1.removeEventListener(call1Listener);
            call2.removeEventListener(call2Listener);
            const call1ListenerP2 = (sender, args) => {
                if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                    console.debug("call1 ConfigurationComplete");
                }
                else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.FrameUpdate) {
                    console.debug("call1 FrameUpdate");
                    localFrameCount++;
                }
            };
            call1.addEventListener(call1ListenerP2);
            const call2ListenerP2 = (sender, args) => {
                if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.ConfigurationComplete) {
                    console.debug("call2 ConfigurationComplete");
                    call1.Listen(address);
                }
                else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.MediaUpdate) {
                    console.debug("call2 MediaUpdate");
                    const remoteMediaUpdateReceived = args;
                    expect(remoteMediaUpdateReceived).toBeDefined();
                    expect(remoteMediaUpdateReceived).not.toBeNull();
                    expect(remoteMediaUpdateReceived.ConnectionId.id).toBe(call2ToCall1.id);
                    expect(remoteMediaUpdateReceived.IsRemote).toBe(true);
                    expect(remoteMediaUpdateReceived.VideoElement).toBeDefined();
                    expect(remoteMediaUpdateReceived.VideoElement).not.toBeNull();
                }
                else if (args.Type === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CallEventType.FrameUpdate) {
                    console.debug("call2 FrameUpdate");
                    remoteFrameCount++;
                    if (remoteFrameCount >= maxFrames) {
                        //expect(localFrameCount).toBeGreaterThanOrEqual(maxFrames);
                        call1.Dispose();
                        call2.Dispose();
                        done();
                    }
                }
            };
            call2.addEventListener(call2ListenerP2);
            configSend.Video = true;
            call1.Configure(configSend);
        };
        setInterval(() => {
            call1.Update();
            call2.Update();
        }, 50);
        call1.Configure(configSend);
    });
});


/***/ }),

/***/ "./src/test/DeviceApiTest.ts":
/*!***********************************!*\
  !*** ./src/test/DeviceApiTest.ts ***!
  \***********************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   DeviceApiTest_export: () => (/* binding */ DeviceApiTest_export)
/* harmony export */ });
/* harmony import */ var _awrtc_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../awrtc/index */ "./src/awrtc/index.ts");
var __awaiter = (undefined && undefined.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
//current setup needs to load everything as a module

function DeviceApiTest_export() {
}
describe("DeviceApiTest", () => {
    beforeEach(() => {
        jasmine.DEFAULT_TIMEOUT_INTERVAL = 10000;
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.Reset();
    });
    afterEach(() => {
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.Reset();
    });
    function printall() {
        console.log("current DeviceApi.Devices:");
        for (let k in _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.VideoDevices) {
            let v = _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.VideoDevices[k];
            console.log(v.Id + " defaultLabel:" + v.fallbackLabel + " label:" + v.Name + " guessed:" + v.isLabelGuessed);
        }
    }
    it("update", (done) => {
        let update1complete = false;
        let update2complete = false;
        let deviceCount = 0;
        expect(Object.keys(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.VideoDevices).length).toBe(0);
        //first without device labels
        let updatecall1 = () => {
            expect(update1complete).toBe(false);
            expect(update2complete).toBe(false);
            console.debug("updatecall1");
            printall();
            let devices1 = _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.VideoDevices;
            deviceCount = Object.keys(devices1).length;
            expect(deviceCount).toBeGreaterThan(0);
            let key1 = Object.keys(devices1)[0];
            //these tests don't work anymore due to forcing permissions for devices in
            //unit tests. 
            //In a real browser we don't have access to device names until GetUserMedia
            //returned. Meaning the API will fill in the names using "videoinput 1"
            //"videoinput 2" and so on. 
            //Now the tests force permissions = true so we already have full
            //access at the start
            /*
            expect(devices1[key1].label).toBe("videoinput 1");
            expect(devices1[key1].isLabelGuessed).toBe(true);
            if(deviceCount > 1)
            {
                let key2 = Object.keys(devices1)[1];
                expect(devices1[key2].label).toBe("videoinput 2");
                expect(devices1[key2].isLabelGuessed).toBe(true);
            }
            */
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.RemOnChangedHandler(updatecall1);
            //second call with device labels
            let updatecall2 = () => {
                console.debug("updatecall2");
                printall();
                //check if the handler work properly
                expect(update1complete).toBe(true);
                expect(update2complete).toBe(false);
                //sadly can't simulate fixed device names for testing
                let devices2 = _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.VideoDevices;
                expect(Object.keys(devices2).length).toBe(deviceCount);
                let key2 = Object.keys(devices2)[0];
                //should have original label now
                expect(devices2[key1].Name).not.toBe("videodevice 1");
                //and not be guessed anymore
                expect(devices2[key1].isLabelGuessed).toBe(false, "Chrome fails this now. Likely due to file://. Check for better test setup");
                update2complete = true;
                _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.Reset();
                expect(Object.keys(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.VideoDevices).length).toBe(0);
                done();
            };
            update1complete = true;
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.AddOnChangedHandler(updatecall2);
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.RequestUpdate();
        };
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.AddOnChangedHandler(updatecall1);
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.Update();
    });
    it("capi_update", (done) => {
        let update1complete = false;
        let update2complete = false;
        let deviceCount = 0;
        const devices_length_unitialized = (0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_GetVideoDevices_Length)();
        expect(devices_length_unitialized).toBe(0);
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.AddOnChangedHandler(() => {
            let dev_length = (0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_GetVideoDevices_Length)();
            expect(dev_length).not.toBe(0);
            expect(dev_length).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.SharedInstance.GetVideoDevices().length);
            let keys = Object.keys(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.VideoDevices);
            let counter = 0;
            for (let k of keys) {
                let expectedVal = _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.VideoDevices[k].Name;
                let actual = (0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_GetVideoDevices)(counter);
                expect(actual).toBe(expectedVal);
                counter++;
            }
            done();
        });
        (0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_DeviceApi_Update)();
    });
    it("isMediaAvailable", () => {
        const res = _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.IsUserMediaAvailable();
        expect(res).toBe(true);
    });
    it("getUserMedia", () => __awaiter(void 0, void 0, void 0, function* () {
        let stream = yield _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.getBrowserUserMedia({ audio: true });
        expect(stream).not.toBeNull();
        expect(stream.getVideoTracks().length).toBe(0);
        expect(stream.getAudioTracks().length).toBe(1);
        stream = yield _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.getBrowserUserMedia({ video: true });
        expect(stream).not.toBeNull();
        expect(stream.getAudioTracks().length).toBe(0);
        expect(stream.getVideoTracks().length).toBe(1);
    }));
    it("getAssetMedia", () => __awaiter(void 0, void 0, void 0, function* () {
        let config = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        config.Audio = true;
        config.Video = false;
        let stream = yield _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.getAssetUserMedia(config);
        expect(stream).not.toBeNull();
        expect(stream.getVideoTracks().length).toBe(0);
        expect(stream.getAudioTracks().length).toBe(1);
        config = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        config.Audio = false;
        config.Video = true;
        stream = yield _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.getAssetUserMedia(config);
        expect(stream).not.toBeNull();
        expect(stream.getAudioTracks().length).toBe(0);
        expect(stream.getVideoTracks().length).toBe(1);
    }));
    it("getAssetMedia_invalid", () => __awaiter(void 0, void 0, void 0, function* () {
        let config = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        config.Audio = false;
        config.Video = true;
        config.VideoDeviceName = "invalid name";
        let error = null;
        let stream = null;
        console.log("Expecting error message: Failed to find deviceId for label invalid name");
        try {
            stream = yield _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.getAssetUserMedia(config);
        }
        catch (err) {
            error = err;
        }
        expect(stream).toBeNull();
        expect(error).toBeTruthy();
    }));
    //check for a specific bug causing promise catch not to trigger correctly
    //due to error in ToConstraints
    it("getAssetMedia_invalid_promise", (done) => {
        let config = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        config.Audio = false;
        config.Video = true;
        config.VideoDeviceName = "invalid name";
        let result = null;
        result = _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.getAssetUserMedia(config);
        result.then(() => {
            fail("getAssetUserMedia returned but was expected to fail");
        }).catch((error) => {
            expect(error).toBeTruthy();
            done();
        });
    });
    it("UpdateAsync", () => __awaiter(void 0, void 0, void 0, function* () {
        expect(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.GetVideoDevices().length).toBe(0);
        yield _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.UpdateAsync();
        expect(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.GetVideoDevices().length).toBeGreaterThan(0);
        expect(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.GetVideoDevices().length).toBe((0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_GetVideoDevices_Length)());
    }));
    /*
    it("Devices", async () => {

        DeviceApi.RequestUpdate

        let config = new MediaConfig();
        config.Audio = false;
        config.Video = true;
        config.VideoDeviceName = "invalid name"
        let error = null;
        let stream :MediaStream = null;
        console.log("Expecting error message: Failed to find deviceId for label invalid name");
        try
        {
            stream = await DeviceApi.getAssetUserMedia(config);
        }catch(err){
            error = err;
        }
        expect(stream).toBeNull();
        expect(error).toBeTruthy();
    });
*/
});


/***/ }),

/***/ "./src/test/HelperTest.ts":
/*!********************************!*\
  !*** ./src/test/HelperTest.ts ***!
  \********************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony import */ var _awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../awrtc/network/Helper */ "./src/awrtc/network/Helper.ts");
var __awaiter = (undefined && undefined.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};

describe('SLog', () => {
    beforeEach(() => {
        // Reset the time prefix feature to a known state before each test
        _awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.SetTimePrefix(false);
    });
    it('add time prefix', () => {
        _awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.SetTimePrefix(true);
        const logSpy = spyOn(console, 'log');
        _awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.Log('Test message');
        expect(logSpy).toHaveBeenCalledWith(jasmine.stringMatching(/^\[\d+ ms\] Test message$/));
        logSpy.calls.reset();
    });
    it('do not add time prefix', () => {
        _awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.SetTimePrefix(false);
        const logSpy = spyOn(console, 'log');
        _awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.Log('Test message');
        expect(logSpy).toHaveBeenCalledWith('Test message');
        logSpy.calls.reset();
    });
    it('time prefix correct time', () => __awaiter(void 0, void 0, void 0, function* () {
        _awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.SetTimePrefix(true);
        const delay = 100; // ms
        const logSpy = spyOn(console, 'log');
        yield new Promise(resolve => setTimeout(resolve, delay));
        _awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.Log('Delayed message');
        expect(logSpy).toHaveBeenCalledWith(jasmine.stringMatching(/^\[\d+ ms\] Delayed message$/));
        const loggedTime = parseInt(logSpy.calls.first().args[0].match(/\[(\d+) ms\]/)[1]);
        expect(loggedTime).toBeGreaterThanOrEqual(delay);
        expect(loggedTime).toBeLessThan(delay + 20); // Allowing a small margin of error
        logSpy.calls.reset();
    }));
});


/***/ }),

/***/ "./src/test/LocalNetworkTest.ts":
/*!**************************************!*\
  !*** ./src/test/LocalNetworkTest.ts ***!
  \**************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   LocalNetworkTest: () => (/* binding */ LocalNetworkTest)
/* harmony export */ });
/* harmony import */ var _awrtc_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../awrtc/index */ "./src/awrtc/index.ts");
/* harmony import */ var helper_IBasicNetworkTest__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! helper/IBasicNetworkTest */ "./src/test/helper/IBasicNetworkTest.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/


class LocalNetworkTest extends helper_IBasicNetworkTest__WEBPACK_IMPORTED_MODULE_1__.IBasicNetworkTest {
    setup() {
        super.setup();
        //special tests
    }
    _CreateNetworkImpl() {
        return new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.LocalNetwork();
    }
}
describe("LocalNetworkTest", () => {
    it("TestEnvironment", () => {
        expect(null).toBeNull();
    });
    var test = new LocalNetworkTest();
    test.setup();
});


/***/ }),

/***/ "./src/test/MediaNetworkTest.ts":
/*!**************************************!*\
  !*** ./src/test/MediaNetworkTest.ts ***!
  \**************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   MediaNetworkTest: () => (/* binding */ MediaNetworkTest)
/* harmony export */ });
/* harmony import */ var _awrtc_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../awrtc/index */ "./src/awrtc/index.ts");
/* harmony import */ var _awrtc_network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../awrtc/network/IWebRtcNetwork */ "./src/awrtc/network/IWebRtcNetwork.ts");
var __awaiter = (undefined && undefined.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
/*
Copyright (c) 2022, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/


class TestVideoSource {
    constructor(color) {
        this.fps = 30;
        this.interval = null;
        this.color = color;
        let canvas = document.createElement("canvas");
        document.body.appendChild(canvas);
        canvas.width = 8;
        canvas.height = 8;
        this.canvas = canvas;
        this.MakeFrame();
    }
    MakeFrame() {
        let ctx = this.canvas.getContext("2d");
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        //make blue for debugging purposes
        ctx.fillStyle = this.color;
        ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    }
    //Auto refresh is needed because Chrome does not generate
    //frames if the canvas isn't actively used (even if a stream is set to capture 30 fps from it)
    AutoRefresh() {
        this.interval = window.setInterval(() => {
            this.MakeFrame();
        }, 1 / this.fps);
    }
    Stop() {
        if (this.interval != null) {
            clearInterval(this.interval);
            this.interval = null;
        }
    }
}
class MediaNetworkTest {
    constructor() {
        this.mVideoSourceBlue = null;
        this.mVideoSourceRed = null;
        this.mIntervals = [];
        this.createdNetworks = [];
    }
    createDefault() {
        let netConfig = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetworkConfig();
        netConfig.SignalingUrl = null;
        let createdNetwork = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserMediaNetwork(netConfig);
        this.createdNetworks.push(createdNetwork);
        return createdNetwork;
    }
    testInterval(callback, ms) {
        const id = setInterval(callback, ms);
        this.mIntervals.push(id);
    }
    setup() {
        beforeEach(() => {
            jasmine.DEFAULT_TIMEOUT_INTERVAL = 1000000;
            this.mVideoSourceBlue = new TestVideoSource("blue");
            this.mVideoSourceBlue.AutoRefresh();
            this.mVideoSourceRed = new TestVideoSource("red");
            this.mVideoSourceRed.AutoRefresh();
            this.mIntervals = [];
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.SharedInstance.VideoInput.AddCanvasDevice(this.mVideoSourceBlue.canvas, "blue", this.mVideoSourceBlue.canvas.width, this.mVideoSourceBlue.canvas.height, this.mVideoSourceBlue.fps);
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.SharedInstance.VideoInput.AddCanvasDevice(this.mVideoSourceRed.canvas, "red", this.mVideoSourceRed.canvas.width, this.mVideoSourceRed.canvas.height, this.mVideoSourceRed.fps);
        });
        afterEach(() => {
            //delete all existing networks
            for (let net of this.createdNetworks)
                net.Dispose();
            this.createdNetworks = new Array();
            //clear all custom video devices
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.ResetSharedInstance();
            //stop intervals
            this.mIntervals.forEach(x => {
                clearInterval(x);
            });
            this.mIntervals = [];
            this.mVideoSourceBlue.Stop();
            this.mVideoSourceRed.Stop();
        });
        function sleep(ms) {
            return new Promise(resolve => setTimeout(resolve, ms));
        }
        /*
        TODO: A single test that connects two peers with different settings and then performs
        renegotation to test any changes
        1. audio off, video off -> both on
        2. audio off, video off -> video on -> audio on
        3. audio off, video off -> audio on -> video on
        4. audio on, video on -> audio off
        5. audio on, video on -> video off
        6. audio on, video on -> audio off, video off
        7. change of video device
        */
        it("Reconfigure_Remote", () => __awaiter(this, void 0, void 0, function* () {
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserMediaStream.DEBUG_SHOW_ELEMENTS = true;
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLog.SetLogLevel(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLogLevel.Info);
            let mediaConfig = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
            mediaConfig.Video = true;
            mediaConfig.VideoDeviceName = "blue";
            let net1 = this.createDefault();
            net1.Configure(mediaConfig);
            let net2 = this.createDefault();
            while (net1.GetConfigurationState() === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.InProgress) {
                net1.Update();
                yield sleep(10);
            }
            console.log("configure done");
            expect(net1.GetConfigurationState()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.Successful);
            let localFrame = null;
            while (localFrame == null) {
                net1.Update();
                localFrame = net1.TryGetFrame(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID);
                yield sleep(10);
            }
            console.log("Frame received. Expecting blue:");
            expect(localFrame.Height).toBeGreaterThan(0);
            expect(localFrame.Width).toBeGreaterThan(0);
            expect(localFrame.Buffer).not.toBeNull();
            expect(localFrame.Buffer[0]).toBeLessThan(5);
            expect(localFrame.Buffer[1]).toBeLessThan(5);
            expect(localFrame.Buffer[2]).toBeGreaterThan(250);
            expect(localFrame.Buffer[3]).toBe(255);
            console.log("Reconfigure to red video source");
            mediaConfig.VideoDeviceName = "red";
            net1.Configure(mediaConfig);
            while (net1.GetConfigurationState() === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.InProgress) {
                net1.Update();
                yield sleep(10);
            }
            console.log("configure done");
            expect(net1.GetConfigurationState()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.Successful);
            localFrame = null;
            while (localFrame == null) {
                net1.Update();
                localFrame = net1.TryGetFrame(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID);
                yield sleep(10);
            }
            console.log("Frame received. Expecting red:");
            //expect red
            expect(localFrame.Buffer[0]).toBeGreaterThan(250);
            expect(localFrame.Buffer[1]).toBeLessThan(5);
            expect(localFrame.Buffer[2]).toBeLessThan(5);
            expect(localFrame.Buffer[3]).toBe(255);
            console.log("test done");
        }));
        it("Reconfigure_Local", () => __awaiter(this, void 0, void 0, function* () {
            let mediaConfig = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
            mediaConfig.Video = true;
            mediaConfig.VideoDeviceName = "blue";
            let network = this.createDefault();
            network.Configure(mediaConfig);
            while (network.GetConfigurationState() === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.InProgress) {
                network.Update();
                yield sleep(10);
            }
            console.log("configure done");
            expect(network.GetConfigurationState()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.Successful);
            let localFrame = null;
            while (localFrame == null) {
                network.Update();
                localFrame = network.TryGetFrame(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID);
                yield sleep(10);
            }
            console.log("Frame received. Expecting blue:");
            expect(localFrame.Height).toBeGreaterThan(0);
            expect(localFrame.Width).toBeGreaterThan(0);
            expect(localFrame.Buffer).not.toBeNull();
            expect(localFrame.Buffer[0]).toBeLessThan(5);
            expect(localFrame.Buffer[1]).toBeLessThan(5);
            expect(localFrame.Buffer[2]).toBeGreaterThan(250);
            expect(localFrame.Buffer[3]).toBe(255);
            console.log("Reconfigure to red video source");
            mediaConfig.VideoDeviceName = "red";
            network.Configure(mediaConfig);
            while (network.GetConfigurationState() === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.InProgress) {
                network.Update();
                yield sleep(10);
            }
            console.log("configure done");
            expect(network.GetConfigurationState()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.Successful);
            localFrame = null;
            while (localFrame == null) {
                network.Update();
                localFrame = network.TryGetFrame(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID);
                yield sleep(10);
            }
            console.log("Frame received. Expecting red:");
            //expect red
            expect(localFrame.Buffer[0]).toBeGreaterThan(250);
            expect(localFrame.Buffer[1]).toBeLessThan(5);
            expect(localFrame.Buffer[2]).toBeLessThan(5);
            expect(localFrame.Buffer[3]).toBe(255);
            console.log("test done");
        }));
        it("FrameUpdates", (done) => {
            let mediaConfig = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
            mediaConfig.Video = true;
            let network = this.createDefault();
            network.Configure(mediaConfig);
            this.testInterval(() => {
                network.Update();
                let localFrame = network.TryGetFrame(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID);
                if (localFrame != null) {
                    expect(localFrame.Height).toBeGreaterThan(0);
                    expect(localFrame.Width).toBeGreaterThan(0);
                    expect(localFrame.Buffer).not.toBeNull();
                    done();
                }
                network.Flush();
            }, 10);
        });
        it("StreamAddedEventLocal", (done) => {
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserMediaStream.DEBUG_SHOW_ELEMENTS = true;
            let mediaConfig = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
            mediaConfig.Video = true;
            let network = this.createDefault();
            network.Configure(mediaConfig);
            this.testInterval(() => {
                network.Update();
                let evt = null;
                while ((evt = network.DequeueRtcEvent()) != null) {
                    console.log("Stream added", evt);
                    expect(evt.EventType).toBe(_awrtc_network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_1__.RtcEventType.StreamAdded);
                    expect(evt.Args.videoHeight).toBeGreaterThan(0);
                    expect(evt.Args.videoWidth).toBeGreaterThan(0);
                    done();
                }
                network.Flush();
            }, 10);
        });
        it("StreamAddedEventRemote", (done) => {
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserMediaStream.DEBUG_SHOW_ELEMENTS = true;
            let testaddress = "testaddress" + Math.random();
            let sender = this.createDefault();
            let receiver = this.createDefault();
            let configureComplete = false;
            let senderFrame = false;
            let receiverFrame = false;
            const config = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
            config.Video = true;
            sender.Configure(config);
            this.testInterval(() => {
                sender.Update();
                receiver.Update();
                if (configureComplete == false) {
                    let state = sender.GetConfigurationState();
                    if (state == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.Successful) {
                        configureComplete = true;
                        sender.StartServer(testaddress);
                    }
                    else if (state == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.Failed) {
                        fail();
                    }
                }
                let sndEvt = sender.Dequeue();
                if (sndEvt != null) {
                    console.log("sender event: " + sndEvt);
                    if (sndEvt.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitialized) {
                        receiver.Connect(testaddress);
                    }
                }
                let recEvt = receiver.Dequeue();
                if (recEvt != null) {
                    console.log("receiver event: " + recEvt);
                }
                let evt = null;
                while ((evt = sender.DequeueRtcEvent()) != null) {
                    expect(evt.EventType).toBe(_awrtc_network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_1__.RtcEventType.StreamAdded);
                    expect(evt.Args.videoHeight).toBeGreaterThan(0);
                    expect(evt.Args.videoWidth).toBeGreaterThan(0);
                    senderFrame = true;
                    console.log("sender received first frame");
                }
                while ((evt = receiver.DequeueRtcEvent()) != null) {
                    expect(evt.EventType).toBe(_awrtc_network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_1__.RtcEventType.StreamAdded);
                    expect(evt.Args.videoHeight).toBeGreaterThan(0);
                    expect(evt.Args.videoWidth).toBeGreaterThan(0);
                    receiverFrame = true;
                    console.log("receiver received first frame");
                }
                sender.Flush();
                receiver.Flush();
                if (senderFrame && receiverFrame)
                    done();
            }, 40);
        }, 15000);
        it("get_stats", () => __awaiter(this, void 0, void 0, function* () {
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserMediaStream.DEBUG_SHOW_ELEMENTS = true;
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLog.SetLogLevel(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLogLevel.Info);
            let mediaConfig = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
            mediaConfig.Video = true;
            mediaConfig.VideoDeviceName = "blue";
            let net1 = this.createDefault();
            net1.Configure(mediaConfig);
            let net2 = this.createDefault();
            while (net1.GetConfigurationState() === _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.InProgress) {
                net1.Update();
                yield sleep(10);
            }
            console.log("configure done");
            expect(net1.GetConfigurationState()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfigurationState.Successful);
            const address = "test" + Date.now();
            net1.StartServer(address);
            const sleeptime = 10;
            let waittime = 0;
            //fail if we didn't reach a successful test condition after this time
            const connectionTimeout = 5000;
            let net1_to_net2 = _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID;
            let net2_to_net1 = _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID;
            let stats_event = null;
            let connected1 = false;
            let connected2 = false;
            //connect
            //let a few frames play and then request statistics
            while (waittime < connectionTimeout && !(connected1 && connected2)) {
                net1.Update();
                net2.Update();
                let evt = null;
                while ((evt = net1.Dequeue())) {
                    if (evt.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitialized) {
                        net1_to_net2 = evt.ConnectionId;
                        net2_to_net1 = net2.Connect(address);
                    }
                    if (evt.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection) {
                        connected1 = true;
                    }
                    else {
                        //fail?
                    }
                }
                while ((evt = net2.Dequeue())) {
                    if (evt.Type == _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection) {
                        connected2 = true;
                    }
                    else {
                        //fail?
                    }
                }
                //expect no stat events yet
                const evt1 = net1.DequeueRtcEvent();
                if (evt1)
                    expect(evt1.EventType).not.toBe(_awrtc_network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_1__.RtcEventType.Stats);
                const evt2 = net2.DequeueRtcEvent();
                if (evt2)
                    expect(evt2.EventType).not.toBe(_awrtc_network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_1__.RtcEventType.Stats);
                yield sleep(sleeptime);
                waittime += sleeptime;
            }
            expect(waittime).withContext("Connection was not establish within timeout").toBeLessThan(connectionTimeout);
            const statsTimeout = 5000;
            waittime = 0;
            net1.RequestStats();
            while (waittime < statsTimeout) {
                net1.Update();
                net2.Update();
                //expect to receive a stats event within timeout
                const evt = net1.DequeueRtcEvent();
                if (evt != null && evt.EventType == _awrtc_network_IWebRtcNetwork__WEBPACK_IMPORTED_MODULE_1__.RtcEventType.Stats) {
                    stats_event = evt;
                    break;
                }
                yield sleep(sleeptime);
                waittime += sleeptime;
            }
            expect(waittime).withContext("StatReports did not arrive within timeout").toBeLessThan(statsTimeout);
            expect(stats_event).withContext("Network 1 did not receive a stats event.").not.toBeNull();
            expect(stats_event.Reports.length).withContext("Network 1 did receive an event but it has no reports attached.").toBeGreaterThan(0);
        }));
    }
}
describe("MediaNetworkTest", () => {
    it("TestEnvironment", () => {
        expect(null).toBeNull();
    });
    var test = new MediaNetworkTest();
    test.setup();
});


/***/ }),

/***/ "./src/test/MediaTest.ts":
/*!*******************************!*\
  !*** ./src/test/MediaTest.ts ***!
  \*******************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   MediaTest_export: () => (/* binding */ MediaTest_export)
/* harmony export */ });
/* harmony import */ var _awrtc_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../awrtc/index */ "./src/awrtc/index.ts");
/* harmony import */ var VideoInputTest__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! VideoInputTest */ "./src/test/VideoInputTest.ts");
var __awaiter = (undefined && undefined.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};


function MediaTest_export() {
}
describe("MediaTest", () => {
    beforeEach((done) => {
        let handler = () => {
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.RemOnChangedHandler(handler);
            done();
        };
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.AddOnChangedHandler(handler);
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.Update();
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.ResetSharedInstance();
    });
    it("SharedInstance", () => {
        expect(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.SharedInstance).toBeTruthy();
        let instance1 = _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.SharedInstance;
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.ResetSharedInstance();
        expect(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.SharedInstance).not.toBe(instance1);
    });
    it("GetVideoDevices", () => {
        const media = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media();
        let devs = media.GetVideoDevices();
        expect(devs).toBeTruthy();
        expect(devs.length).toBeGreaterThan(0);
    });
    it("GetUserMedia", () => __awaiter(void 0, void 0, void 0, function* () {
        const media = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media();
        let config = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        config.Video = true;
        config.Audio = false;
        let stream = yield media.getUserMedia(config);
        expect(stream).not.toBeNull();
        expect(stream.getAudioTracks().length).toBe(0);
        expect(stream.getVideoTracks().length).toBe(1);
        stream = null;
        let err = null;
        config.VideoDeviceName = "invalid name";
        console.log("Expecting error message: Failed to find deviceId for label invalid name");
        try {
            stream = yield media.getUserMedia(config);
        }
        catch (error) {
            err = error;
        }
        expect(err).not.toBeNull();
        expect(stream).toBeNull();
    }));
    it("GetUserMedia_videoinput", () => __awaiter(void 0, void 0, void 0, function* () {
        const name = "test_canvas";
        const media = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media();
        const config = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        config.Audio = false;
        config.Video = true;
        const canvas = (0,VideoInputTest__WEBPACK_IMPORTED_MODULE_1__.MakeTestCanvas)();
        media.VideoInput.AddCanvasDevice(canvas, name, canvas.width, canvas.height, 30);
        const streamCamera = yield media.getUserMedia(config);
        expect(streamCamera).not.toBeNull();
        expect(streamCamera.getAudioTracks().length).toBe(0);
        expect(streamCamera.getVideoTracks().length).toBe(1);
        config.VideoDeviceName = name;
        const streamCanvas = yield media.getUserMedia(config);
        expect(streamCanvas).not.toBeNull();
        expect(streamCanvas.getAudioTracks().length).toBe(0);
        expect(streamCanvas.getVideoTracks().length).toBe(1);
        const streamCanvas2 = yield media.getUserMedia(config);
        expect(streamCanvas2).not.toBeNull();
        expect(streamCanvas2.getAudioTracks().length).toBe(0);
        expect(streamCanvas2.getVideoTracks().length).toBe(1);
    }));
    it("GetUserMedia_videoinput_and_audio", () => __awaiter(void 0, void 0, void 0, function* () {
        const name = "test_canvas";
        const media = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media();
        const config = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.MediaConfig();
        config.Audio = true;
        config.Video = true;
        const canvas = (0,VideoInputTest__WEBPACK_IMPORTED_MODULE_1__.MakeTestCanvas)();
        media.VideoInput.AddCanvasDevice(canvas, name, canvas.width, canvas.height, 30);
        config.VideoDeviceName = name;
        let stream = null;
        try {
            stream = yield media.getUserMedia(config);
        }
        catch (err) {
            console.error(err);
            fail(err);
        }
        expect(stream).not.toBeNull();
        expect(stream.getAudioTracks().length).toBe(1);
        expect(stream.getVideoTracks().length).toBe(1);
        config.VideoDeviceName = "invalid name";
        stream = null;
        let error_result = null;
        try {
            stream = yield media.getUserMedia(config);
        }
        catch (err) {
            error_result = err;
        }
        expect(error_result).not.toBeNull();
        expect(stream).toBeNull();
    }), 15000);
    //CAPI needs to be changed to use Media only instead the device API
    it("MediaCapiVideoInput", () => __awaiter(void 0, void 0, void 0, function* () {
        //empty normal device api
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.Reset();
        expect((0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_GetVideoDevices_Length)()).toBe(0);
        const name = "test_canvas";
        const canvas = (0,VideoInputTest__WEBPACK_IMPORTED_MODULE_1__.MakeTestCanvas)();
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.SharedInstance.VideoInput.AddCanvasDevice(canvas, name, canvas.width, canvas.height, 30);
        expect((0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_GetVideoDevices_Length)()).toBe(1);
        expect((0,_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.CAPI_Media_GetVideoDevices)(0)).toBe(name);
    }));
});
describe("MediaStreamTest", () => {
    beforeEach((done) => {
        let handler = () => {
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.RemOnChangedHandler(handler);
            done();
        };
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.AddOnChangedHandler(handler);
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.DeviceApi.Update();
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.Media.ResetSharedInstance();
    });
    class TestStreamContainer {
        constructor() {
            let canvas = document.createElement("canvas");
            document.body.appendChild(canvas);
            canvas.width = 64;
            canvas.height = 64;
            let ctx = canvas.getContext("2d");
            //make blue for debugging purposes
            ctx.fillStyle = "blue";
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            this.canvas = canvas;
            this.stream = canvas.captureStream();
        }
        MakeFrame(color) {
            let ctx = this.canvas.getContext("2d");
            ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            //make blue for debugging purposes
            ctx.fillStyle = color;
            ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        }
    }
    function MakeTestStreamContainer() {
        return new TestStreamContainer();
    }
    //TODO: need proper way to wait and check with async/ await
    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
    function WaitFor() {
        return __awaiter(this, void 0, void 0, function* () {
        });
    }
    it("buffer_and_trygetframe", () => __awaiter(void 0, void 0, void 0, function* () {
        _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserMediaStream.DEBUG_SHOW_ELEMENTS = true;
        const testcontainer = MakeTestStreamContainer();
        const stream = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserMediaStream(true);
        stream.UpdateTrack(testcontainer.stream.getTracks()[0]);
        //frames are not available at the start until fully loaded
        let frame = stream.TryGetFrame();
        expect(frame).toBeNull();
        yield sleep(100);
        stream.Update();
        //waited for the internals to get initialized. We should have a frame now
        frame = stream.TryGetFrame();
        expect(frame).not.toBeNull();
        ;
        //and a buffer
        let buffer = frame.Buffer;
        console.log(buffer);
        expect(buffer).not.toBeNull();
        ;
        //expected to be blue
        let r = buffer[0];
        let g = buffer[1];
        let b = buffer[2];
        let a = buffer[3];
        //browser might change the color slightly so use less / Greater instead of exact numbers
        expect(r).toBeLessThan(5);
        expect(g).toBeLessThan(5);
        expect(b).toBeGreaterThan(250);
        expect(a).toBeGreaterThan(250);
        //we removed the frame now. this should be null
        frame = stream.TryGetFrame();
        expect(frame).toBeNull();
        //make a new frame with different color
        testcontainer.MakeFrame("#FFFF00");
        yield sleep(100);
        stream.Update();
        //get new frame
        frame = stream.TryGetFrame();
        expect(frame).not.toBeNull();
        ;
        buffer = frame.Buffer;
        expect(buffer).not.toBeNull();
        ;
        //should be different color now
        r = buffer[0];
        g = buffer[1];
        b = buffer[2];
        a = buffer[3];
        expect(r).toBeGreaterThan(250);
        expect(g).toBeGreaterThan(250);
        expect(b).toBeLessThan(5);
        expect(a).toBeGreaterThan(250);
    }));
    function createTexture(gl) {
        const texture = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, texture);
        // Because images have to be download over the internet
        // they might take a moment until they are ready.
        // Until then put a single pixel in the texture so we can
        // use it immediately. When the image has finished downloading
        // we'll update the texture with the contents of the image.
        const level = 0;
        const internalFormat = gl.RGBA;
        const width = 1;
        const height = 1;
        const border = 0;
        const srcFormat = gl.RGBA;
        const srcType = gl.UNSIGNED_BYTE;
        const pixel = new Uint8Array([0, 0, 255, 255]); // opaque blue
        gl.texImage2D(gl.TEXTURE_2D, level, internalFormat, width, height, border, srcFormat, srcType, pixel);
        return texture;
    }
    it("texture", () => __awaiter(void 0, void 0, void 0, function* () {
        //blue test container to stream from
        const testcontainer = MakeTestStreamContainer();
        const stream = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.BrowserMediaStream(true);
        stream.UpdateTrack(testcontainer.stream.getTracks()[0]);
        //document.body.appendChild(testcontainer.canvas);
        //waited for the internals to get initialized. We should have a frame now
        yield sleep(100);
        stream.Update();
        let frame = stream.PeekFrame();
        expect(frame).not.toBeNull();
        //create another canvas but with WebGL context
        //this is where we copy the texture to
        let canvas = document.createElement("canvas");
        canvas.width = testcontainer.canvas.width;
        canvas.height = testcontainer.canvas.height;
        //document.body.appendChild(canvas);
        let gl = canvas.getContext("webgl2");
        //testing only. draw this one red
        gl.clearColor(1, 0, 0, 1);
        gl.clear(gl.COLOR_BUFFER_BIT);
        //create new texture and copy the image into it
        let texture = createTexture(gl);
        let res = frame.ToTexture(gl, texture);
        expect(res).toBe(true);
        //we attach our test texture to a frame buffer, then read from it to copy the data back from the GPU
        //into an array dst_buffer
        const dst_buffer = new Uint8Array(testcontainer.canvas.width * testcontainer.canvas.height * 4);
        const fb = gl.createFramebuffer();
        gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
        gl.readPixels(0, 0, testcontainer.canvas.width, testcontainer.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, dst_buffer);
        //check if we have the expected blue color we use to setup the testcontainer canvas
        let r = dst_buffer[0];
        let g = dst_buffer[1];
        let b = dst_buffer[2];
        let a = dst_buffer[3];
        expect(r).toBe(0);
        expect(g).toBe(0);
        expect(b).toBe(255);
        expect(a).toBe(255);
        //TODO: could compare whole src / dst buffer to check if something is cut off
        //const compare_buffer = frame.Buffer;
    }));
});


/***/ }),

/***/ "./src/test/VideoInputTest.ts":
/*!************************************!*\
  !*** ./src/test/VideoInputTest.ts ***!
  \************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   ExtractData: () => (/* binding */ ExtractData),
/* harmony export */   MakeBrokenTestCanvas: () => (/* binding */ MakeBrokenTestCanvas),
/* harmony export */   MakeTestCanvas: () => (/* binding */ MakeTestCanvas),
/* harmony export */   MakeTestImage: () => (/* binding */ MakeTestImage),
/* harmony export */   VideoInputTest_export: () => (/* binding */ VideoInputTest_export)
/* harmony export */ });
/* harmony import */ var _awrtc_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../awrtc/index */ "./src/awrtc/index.ts");

function VideoInputTest_export() {
}
function MakeTestCanvas(w, h) {
    if (w == null)
        w = 4;
    if (h == null)
        h = 4;
    let canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    let ctx = canvas.getContext("2d");
    //make blue for debugging purposes
    ctx.fillStyle = "blue";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    return canvas;
}
function MakeBrokenTestCanvas() {
    let canvas = document.createElement("canvas");
    return canvas;
}
/**Create test chessboard like image with pattern
 * Black White
 * White Black
 *
 * So each corner can be tested for correct results.
 *
 * @param src_width
 * @param src_height
 */
function MakeTestImage(src_width, src_height) {
    let src_size = src_width * src_height * 4;
    let src_data = new Uint8ClampedArray(src_size);
    for (let y = 0; y < src_height; y++) {
        for (let x = 0; x < src_width; x++) {
            let pos = y * src_width + x;
            let xp = x >= src_width / 2;
            let yp = y >= src_height / 2;
            let val = 0;
            if (xp || yp)
                val = 255;
            if (xp && yp)
                val = 0;
            src_data[pos * 4 + 0] = val;
            src_data[pos * 4 + 1] = val;
            src_data[pos * 4 + 2] = val;
            src_data[pos * 4 + 3] = 255;
        }
    }
    var src_img = new ImageData(src_data, src_width, src_height);
    return src_img;
}
function ExtractData(video) {
    var canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    let dst_context = canvas.getContext('2d');
    dst_context.drawImage(video, 0, 0, canvas.width, canvas.height);
    let dst_img = dst_context.getImageData(0, 0, canvas.width, canvas.height);
    return dst_img;
}
describe("VideoInputTest", () => {
    beforeEach(() => {
    });
    it("AddRem", () => {
        let name = "test_canvas";
        let vi = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.VideoInput();
        let canvas = document.createElement("canvas");
        expect(vi.HasDevice(name)).toBe(false);
        vi.AddCanvasDevice(canvas, name, canvas.width, canvas.height, 30);
        expect(vi.HasDevice(name)).toBe(true);
        vi.RemoveDevice(name);
        expect(vi.HasDevice(name)).toBe(false);
    });
    it("GetDeviceNames", () => {
        let name = "test_canvas";
        let name2 = "test_canvas2";
        let vi = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.VideoInput();
        let canvas = document.createElement("canvas");
        let names = vi.GetDeviceNames();
        expect(names).toBeTruthy();
        expect(names.length).toBe(0);
        vi.AddCanvasDevice(canvas, name, canvas.width, canvas.height, 30);
        names = vi.GetDeviceNames();
        expect(names).toBeTruthy();
        expect(names.length).toBe(1);
        expect(names[0]).toBe(name);
        vi.AddCanvasDevice(canvas, name, canvas.width, canvas.height, 30);
        names = vi.GetDeviceNames();
        expect(names).toBeTruthy();
        expect(names.length).toBe(1);
        expect(names[0]).toBe(name);
        vi.AddCanvasDevice(canvas, name2, canvas.width, canvas.height, 30);
        names = vi.GetDeviceNames();
        expect(names).toBeTruthy();
        expect(names.length).toBe(2);
        expect(names.sort()).toEqual([name, name2].sort());
    });
    it("GetStream", () => {
        let name = "test_canvas";
        let vi = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.VideoInput();
        let canvas = MakeTestCanvas();
        let stream = vi.GetStream(name);
        expect(stream).toBeNull();
        vi.AddCanvasDevice(canvas, name, canvas.width, canvas.height, 30);
        stream = vi.GetStream(name);
        expect(stream).toBeTruthy();
    });
    it("AddCanvasDevice_no_scaling", (done) => {
        let name = "test_canvas";
        let vi = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.VideoInput();
        const src_width = 40;
        const src_height = 30;
        let canvas = MakeTestCanvas(src_width, src_height);
        vi.AddCanvasDevice(canvas, name, canvas.width, canvas.height, 30);
        let stream = vi.GetStream(name);
        expect(stream).toBeTruthy();
        let videoOutput = document.createElement("video");
        videoOutput.onloadedmetadata = () => {
            expect(videoOutput.videoWidth).toBe(src_width);
            expect(videoOutput.videoHeight).toBe(src_height);
            done();
        };
        videoOutput.srcObject = stream;
    }, 1000);
    it("AddCanvasDevice_scaling", (done) => {
        let debug = false;
        let name = "test_canvas";
        let vi = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.VideoInput();
        const src_width = 64;
        const src_height = 64;
        const dst_width = 32;
        const dst_height = 32;
        let canvas = MakeTestCanvas(src_width, src_height);
        let srcContext = canvas.getContext("2d");
        var src_img = MakeTestImage(src_width, src_height);
        srcContext.putImageData(src_img, 0, 0);
        if (debug)
            document.body.appendChild(canvas);
        vi.AddCanvasDevice(canvas, name, dst_width, dst_height, 30);
        let stream = vi.GetStream(name);
        expect(stream).toBeTruthy();
        let videoOutput = document.createElement("video");
        if (debug)
            document.body.appendChild(videoOutput);
        videoOutput.onloadedmetadata = () => {
            //chrome only returns image data once play is called
            //firefox works without play
            videoOutput.play();
            expect(videoOutput.videoWidth).toBe(dst_width);
            expect(videoOutput.videoHeight).toBe(dst_height);
            let dst_img_data = ExtractData(videoOutput);
            //upper left
            expect(dst_img_data.data[0]).toBe(0);
            //upper right
            expect(dst_img_data.data[((dst_width - 1) * 4)]).toBe(255);
            //lower left
            expect(dst_img_data.data[((dst_height - 1) * dst_width) * 4]).toBe(255);
            //lower right
            expect(dst_img_data.data[(dst_height * dst_width - 1) * 4]).toBe(0);
            vi.RemoveDevice(name);
            done();
        };
        videoOutput.srcObject = stream;
    }, 2000);
    //not yet clear how this can be handled
    //this test will trigger an error in firefox
    xit("GetStream_no_context", () => {
        let name = "test_canvas";
        let vi = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.VideoInput();
        let canvas = MakeBrokenTestCanvas();
        //if we try to record from a canvas before
        //a context was accessed it will fail. 
        //uncommenting this line fixes the bug
        //but this is out of our control / within user code
        //let ctx = canvas.getContext("2d");
        let stream = vi.GetStream(name);
        expect(stream).toBeNull();
        vi.AddCanvasDevice(canvas, name, canvas.width, canvas.height, 30);
        stream = vi.GetStream(name);
        expect(stream).toBeTruthy();
    });
    //not yet clear how this can be handled
    //this test will trigger an error in firefox
    it("AddRemDevice", () => {
        let name = "test_canvas";
        const w = 640;
        const h = 480;
        const fps = 30;
        let vi = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.VideoInput();
        let stream = vi.GetStream(name);
        expect(stream).toBeNull();
        vi.AddDevice(name, w, h, fps);
        let res = vi.GetDeviceNames().indexOf(name);
        expect(res).toBe(0);
        vi.RemoveDevice(name);
        let res2 = vi.GetDeviceNames().indexOf(name);
        expect(res2).toBe(-1);
    });
    it("Device_int_array", () => {
        let name = "test_canvas";
        const w = 2;
        const h = 2;
        const fps = 30;
        let arr = new Uint8ClampedArray([
            1, 2, 3, 255,
            4, 5, 6, 255,
            7, 8, 9, 255,
            10, 11, 12, 255,
            13, 14, 15, 255
        ]);
        let vi = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.VideoInput();
        vi.AddDevice(name, w, h, fps);
        let stream = vi.GetStream(name);
        expect(stream).toBeTruthy();
        const clamped = new Uint8ClampedArray(arr.buffer, 4, 4 * 4);
        const res = vi.UpdateFrame(name, clamped, w, h, _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.VideoInputType.ARGB, 0, false);
        expect(res).toBe(true);
        let result_canvas = vi.canvasDevices[name].canvas;
        expect(result_canvas.width).toBe(w);
        expect(result_canvas.height).toBe(h);
        let result_img = result_canvas.getContext("2d").getImageData(0, 0, result_canvas.width, result_canvas.height);
        const result_arr = new Uint8Array(result_img.data.buffer);
        const base_arr = new Uint8Array(arr.buffer, 4, 4 * 4);
        expect(base_arr).toEqual(result_arr);
    });
    it("Device_full", () => {
        let src_canvas = MakeTestCanvas();
        let src_ctx = src_canvas.getContext("2d");
        src_ctx.fillStyle = "yellow";
        src_ctx.fillRect(0, 0, src_canvas.width, src_canvas.height);
        let name = "test_canvas";
        const w = 2;
        const h = 2;
        const fps = 30;
        src_canvas.width = w;
        src_canvas.height = h;
        let vi = new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.VideoInput();
        let src_img = src_ctx.getImageData(0, 0, src_canvas.width, src_canvas.height);
        vi.AddDevice(name, w, h, fps);
        let stream = vi.GetStream(name);
        expect(stream).toBeTruthy();
        const res = vi.UpdateFrame(name, src_img.data, src_img.width, src_img.height, _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.VideoInputType.ARGB, 0, false);
        expect(res).toBe(true);
        //test if the internal array was set correctly
        let result_canvas = vi.canvasDevices[name].canvas;
        expect(result_canvas.width).toBe(src_canvas.width);
        expect(result_canvas.height).toBe(src_canvas.height);
        let result_img = result_canvas.getContext("2d").getImageData(0, 0, result_canvas.width, result_canvas.height);
        expect(result_img.width).toBe(src_img.width);
        expect(result_img.height).toBe(src_img.height);
        expect(result_img.data).toEqual(src_img.data);
    });
});


/***/ }),

/***/ "./src/test/WebRtcNetworkTest.ts":
/*!***************************************!*\
  !*** ./src/test/WebRtcNetworkTest.ts ***!
  \***************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   WebRtcNetworkTest: () => (/* binding */ WebRtcNetworkTest)
/* harmony export */ });
/* harmony import */ var WebsocketNetworkTest__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! WebsocketNetworkTest */ "./src/test/WebsocketNetworkTest.ts");
/* harmony import */ var helper_IBasicNetworkTest__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! helper/IBasicNetworkTest */ "./src/test/helper/IBasicNetworkTest.ts");
/* harmony import */ var _awrtc_index__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ../awrtc/index */ "./src/awrtc/index.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/



class WebRtcNetworkTest extends helper_IBasicNetworkTest__WEBPACK_IMPORTED_MODULE_1__.IBasicNetworkTest {
    constructor() {
        super(...arguments);
        this.mUrl = WebsocketNetworkTest__WEBPACK_IMPORTED_MODULE_0__.WebsocketTest.sUrl;
        //allows each test to overwrite the default behaviour
        this.mUseWebsockets = false;
    }
    setup() {
        beforeEach(() => {
            this.mUrl = WebsocketNetworkTest__WEBPACK_IMPORTED_MODULE_0__.WebsocketTest.sUrl;
            this.mUseWebsockets = WebRtcNetworkTest.mAlwaysUseWebsockets;
        });
        it("GetBufferedAmount", (done) => {
            var srv;
            var address;
            var srvToCltId;
            var clt;
            var cltToSrvId;
            var evt;
            this.thenAsync((finished) => {
                this._CreateServerClient((rsrv, raddress, rsrvToCltId, rclt, rcltToSrvId) => {
                    srv = rsrv;
                    address = raddress;
                    srvToCltId = rsrvToCltId;
                    clt = rclt;
                    cltToSrvId = rcltToSrvId;
                    finished();
                });
            });
            this.then(() => {
                //TODO: more detailed testing by actually triggering the buffer to fill?
                //might be tricky as this is very system dependent
                let buf;
                buf = srv.GetBufferedAmount(srvToCltId, false);
                expect(buf).toBe(0);
                buf = srv.GetBufferedAmount(srvToCltId, true);
                expect(buf).toBe(0);
                buf = clt.GetBufferedAmount(cltToSrvId, false);
                expect(buf).toBe(0);
                buf = clt.GetBufferedAmount(cltToSrvId, true);
                expect(buf).toBe(0);
                done();
            });
            this.start();
        });
        it("SharedAddress", (done) => {
            //turn off websockets and use shared websockets for this test as local network doesn't support shared mode
            this.mUseWebsockets = true;
            this.mUrl = WebsocketNetworkTest__WEBPACK_IMPORTED_MODULE_0__.WebsocketTest.sUrlShared;
            var sharedAddress = "sharedtestaddress";
            var evt;
            var net1;
            var net2;
            this.thenAsync((finished) => {
                net1 = this._CreateNetwork();
                net1.StartServer(sharedAddress);
                this.waitForEvent(net1, finished);
            });
            this.thenAsync((finished) => {
                evt = net1.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_2__.NetEventType.ServerInitialized);
                net2 = this._CreateNetwork();
                net2.StartServer(sharedAddress);
                this.waitForEvent(net2, finished);
            });
            this.thenAsync((finished) => {
                evt = net2.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_2__.NetEventType.ServerInitialized);
                this.waitForEvent(net1, finished);
            });
            this.thenAsync((finished) => {
                evt = net1.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_2__.NetEventType.NewConnection);
                this.waitForEvent(net2, finished);
            });
            this.then(() => {
                evt = net2.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_2__.NetEventType.NewConnection);
                done();
            });
            this.start();
        });
        //connect using only direct local connections (give no ice servers)
        it("ConnectLocalOnly", (done) => {
            var srv;
            var address;
            var clt;
            var cltId;
            var evt;
            this.thenAsync((finished) => {
                srv = this._CreateNetwork();
                this._CreateServerNetwork((rsrv, raddress) => {
                    srv = rsrv;
                    address = raddress;
                    finished();
                });
            });
            this.thenAsync((finished) => {
                clt = this._CreateNetwork();
                cltId = clt.Connect(address);
                this.waitForEvent(clt, finished);
            });
            this.then(() => {
                evt = clt.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_2__.NetEventType.NewConnection);
                expect(evt.ConnectionId.id).toBe(cltId.id);
            });
            this.thenAsync((finished) => {
                this.waitForEvent(srv, finished);
            });
            this.then(() => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_2__.NetEventType.NewConnection);
                expect(evt.ConnectionId.id).not.toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_2__.ConnectionId.INVALID.id);
                done();
            });
            this.start();
        });
        super.setup();
        //special tests
    }
    _CreateNetworkImpl() {
        const config = new _awrtc_index__WEBPACK_IMPORTED_MODULE_2__.NetworkConfig();
        config.IceServers = [WebRtcNetworkTest.sDefaultIceServer];
        if (this.mUseWebsockets) {
            config.SignalingUrl = this.mUrl;
        }
        else {
            config.SignalingUrl = null;
        }
        return new _awrtc_index__WEBPACK_IMPORTED_MODULE_2__.WebRtcNetwork(config);
    }
}
WebRtcNetworkTest.sUrl = 'ws://localhost:12776/test';
WebRtcNetworkTest.sUrlShared = 'ws://localhost:12776/testshared';
WebRtcNetworkTest.sDefaultIceServer = { urls: ["stun:stun.l.google.com:19302"] };
//will set use websocket flag for each test
WebRtcNetworkTest.mAlwaysUseWebsockets = false;
describe("WebRtcNetworkTest", () => {
    it("TestEnvironment", () => {
        expect(null).toBeNull();
    });
    var test = new WebRtcNetworkTest();
    test.mDefaultWaitTimeout = 5000;
    test.setup();
});


/***/ }),

/***/ "./src/test/WebsocketNetworkTest.ts":
/*!******************************************!*\
  !*** ./src/test/WebsocketNetworkTest.ts ***!
  \******************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   WebsocketTest: () => (/* binding */ WebsocketTest)
/* harmony export */ });
/* harmony import */ var _awrtc_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../awrtc/index */ "./src/awrtc/index.ts");
/* harmony import */ var helper_IBasicNetworkTest__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! helper/IBasicNetworkTest */ "./src/test/helper/IBasicNetworkTest.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/


class WebsocketTest extends helper_IBasicNetworkTest__WEBPACK_IMPORTED_MODULE_1__.IBasicNetworkTest {
    setup() {
        super.setup();
        //special tests
        beforeEach(() => {
            this.mUrl = WebsocketTest.sUrl;
            jasmine.DEFAULT_TIMEOUT_INTERVAL = 20000;
        });
        //can only be done manually so far
        xit("Timeout", (done) => {
            //this needs to be a local test server
            //that can be disconnected to test the timeout
            this.mUrl = "ws://192.168.1.3:12776";
            var evt;
            var srv;
            var address;
            this.thenAsync((finished) => {
                this._CreateServerNetwork((rsrv, raddress) => {
                    srv = rsrv;
                    address = raddress;
                    finished();
                });
            });
            this.thenAsync((finished) => {
                console.log("Server ready at " + address);
                expect(srv).not.toBeNull();
                expect(address).not.toBeNull();
                console.debug("Waiting for timeout");
                this.waitForEvent(srv, finished, 120000);
            });
            this.then(() => {
                console.log("Timeout over");
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerClosed);
                expect(srv.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.NotConnected);
                done();
            });
            this.start();
        }, 130000);
        it("SharedAddress", (done) => {
            this.mUrl = WebsocketTest.sUrlShared;
            var sharedAddress = "sharedtestaddress";
            var evt;
            var net1;
            var net2;
            this.thenAsync((finished) => {
                net1 = this._CreateNetwork();
                net1.StartServer(sharedAddress);
                this.waitForEvent(net1, finished);
            });
            this.thenAsync((finished) => {
                evt = net1.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitialized);
                net2 = this._CreateNetwork();
                net2.StartServer(sharedAddress);
                this.waitForEvent(net2, finished);
            });
            this.thenAsync((finished) => {
                evt = net2.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitialized);
                this.waitForEvent(net1, finished);
            });
            this.thenAsync((finished) => {
                evt = net1.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection);
                this.waitForEvent(net2, finished);
            });
            this.then(() => {
                evt = net2.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection);
                done();
            });
            this.start();
        });
        it("BadUrlStartServer", (done) => {
            this.mUrl = WebsocketTest.sBadUrl;
            var evt;
            var srv;
            this.thenAsync((finished) => {
                srv = this._CreateNetwork();
                expect(srv.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.NotConnected);
                srv.StartServer();
                expect(srv.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.Connecting);
                this.waitForEvent(srv, finished);
            });
            this.then(() => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitFailed);
                expect(srv.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.NotConnected);
                done();
            });
            this.start();
        });
        it("BadUrlConnect", (done) => {
            this.mUrl = WebsocketTest.sBadUrl;
            var evt;
            var clt;
            var cltId;
            this.thenAsync((finished) => {
                clt = this._CreateNetwork();
                expect(clt.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.NotConnected);
                cltId = clt.Connect("invalid address");
                expect(clt.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.Connecting);
                this.waitForEvent(clt, finished);
            });
            this.then(() => {
                evt = clt.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ConnectionFailed);
                expect(clt.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.NotConnected);
                done();
            });
            this.start();
        });
        it("WebsocketState", (done) => {
            var srv;
            var address;
            var srvToCltId;
            var clt;
            var cltToSrvId;
            var evt;
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("Creating and connecting two peers.");
            this.thenAsync((finished) => {
                this._CreateServerClient((rsrv, raddress, rsrvToCltId, rclt, rcltToSrvId) => {
                    srv = rsrv;
                    address = raddress;
                    srvToCltId = rsrvToCltId;
                    clt = rclt;
                    cltToSrvId = rcltToSrvId;
                    finished();
                });
            });
            _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("Waiting for the connection to be established");
            this.thenAsync((finished) => {
                //both should be connected
                expect(srv.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.Connected);
                expect(clt.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.Connected);
                _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("Peers connected successfully. Calling Disconnect");
                srv.Disconnect(srvToCltId);
                this.waitForEvent(srv, finished);
            });
            this.thenAsync((finished) => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.Disconnected);
                _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("Peers connected successfully. Calling Disconnect");
                this.waitForEvent(clt, finished);
            });
            this.thenAsync((finished) => {
                evt = clt.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.Disconnected);
                expect(cltToSrvId.id).toBe(evt.ConnectionId.id);
                //after disconnect the client doesn't have any active connections -> expect disconnected
                expect(srv.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.Connected);
                expect(clt.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.NotConnected);
                _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("Disconnected event received. Stopping server");
                srv.StopServer();
                this.waitForEvent(srv, finished);
            });
            this.thenAsync((finished) => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerClosed);
                expect(srv.getStatus()).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketConnectionStatus.NotConnected);
                _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("Server stopped. Restarting server");
                srv.StartServer(address);
                this.waitForEvent(srv, finished);
            });
            this.thenAsync((finished) => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitialized);
                _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("Server ServerInitialized received");
                this._Connect(srv, address, clt, (srvToCltIdOut, cltToSrvIdOut) => {
                    _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.SLog.L("Second connection succeeded");
                    finished();
                });
            });
            this.then(() => {
                done();
            });
            this.start();
        });
    }
    _CreateNetworkImpl() {
        //let url = 'ws://because-why-not.com:12776';
        return new _awrtc_index__WEBPACK_IMPORTED_MODULE_0__.WebsocketNetwork(this.mUrl);
    }
}
//replace with valid url that has a server behind it
//public static sUrl = 'ws://localhost:12776/test';
//public static sUrlShared = 'ws://localhost:12776/testshared';
WebsocketTest.sUrl = 'ws://s.y-not.app';
//public static sUrl = 'ws://192.168.1.3:12776';
WebsocketTest.sUrlShared = 'ws://s.y-not.app/testshared';
//any url to simulate offline server
WebsocketTest.sBadUrl = 'ws://localhost:13776';
describe("WebsocketNetworkTest", () => {
    it("TestEnvironment", () => {
        expect(null).toBeNull();
    });
    beforeEach(() => {
    });
    var test = new WebsocketTest();
    test.setup();
});


/***/ }),

/***/ "./src/test/helper/BasicNetworkTestBase.ts":
/*!*************************************************!*\
  !*** ./src/test/helper/BasicNetworkTestBase.ts ***!
  \*************************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   BasicNetworkTestBase: () => (/* binding */ BasicNetworkTestBase),
/* harmony export */   TestTaskRunner: () => (/* binding */ TestTaskRunner)
/* harmony export */ });
/* harmony import */ var _awrtc_network_index__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../awrtc/network/index */ "./src/awrtc/network/index.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

class TestTaskRunner {
    constructor() {
        this._toDoList = new Array();
    }
    then(syncTask) {
        var wrap = (finished) => {
            syncTask();
            finished();
        };
        this._toDoList.push(wrap);
    }
    thenAsync(task) {
        this._toDoList.push(task);
    }
    start() {
        var task = this._toDoList.shift();
        this._run(task);
    }
    stop() {
    }
    _run(task) {
        task(() => {
            if (this._toDoList.length > 0) {
                setTimeout(() => {
                    this._run(this._toDoList.shift());
                }, 1);
            }
        });
    }
}
class BasicNetworkTestBase {
    constructor() {
        this.mTestRunner = new TestTaskRunner();
        this.mCreatedNetworks = new Array();
        this.mDefaultWaitTimeout = 5000;
    }
    setup() {
        beforeEach(() => {
            this.mTestRunner.stop();
            this.mTestRunner = new TestTaskRunner();
            this.mCreatedNetworks = new Array();
        });
    }
    _CreateNetwork() {
        let net = this._CreateNetworkImpl();
        this.mCreatedNetworks.push(net);
        return net;
    }
    then(syncTask) {
        this.mTestRunner.then(syncTask);
    }
    thenAsync(task) {
        this.mTestRunner.thenAsync(task);
    }
    start() {
        this.mTestRunner.start();
    }
    //public waitForEvent(net: IBasicNetwork) {
    //    var wrap = (finished: Task) => {
    //        var timeout = 1000;
    //        var interval = 100;
    //        var intervalHandle;
    //        intervalHandle = setInterval(() => {
    //            this.UpdateAll();
    //            this.FlushAll();
    //            timeout -= interval;
    //            if (net.Peek() != null) {
    //                clearInterval(intervalHandle);
    //                finished();
    //            } else if (timeout <= 0) {
    //                clearInterval(intervalHandle);
    //                finished();
    //            }
    //        }, interval);
    //    };
    //    this.mTestRunner.thenAsync(wrap);
    //}
    waitForEvent(net, finished, timeout) {
        if (timeout == null)
            timeout = this.mDefaultWaitTimeout;
        var interval = 50;
        var intervalHandle;
        intervalHandle = setInterval(() => {
            this.UpdateAll();
            this.FlushAll();
            timeout -= interval;
            if (net.Peek() != null) {
                clearInterval(intervalHandle);
                finished();
            }
            else if (timeout <= 0) {
                clearInterval(intervalHandle);
                finished();
            }
        }, interval);
    }
    UpdateAll() {
        for (let v of this.mCreatedNetworks) {
            v.Update();
        }
    }
    FlushAll() {
        for (let v of this.mCreatedNetworks) {
            v.Flush();
        }
    }
    ShutdownAll() {
        for (let v of this.mCreatedNetworks) {
            v.Shutdown();
        }
        this.mCreatedNetworks = new Array();
    }
    _CreateServerNetwork(result) {
        var srv = this._CreateNetwork();
        srv.StartServer();
        this.waitForEvent(srv, () => {
            var evt = srv.Dequeue();
            expect(evt).not.toBeNull();
            expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.ServerInitialized);
            expect(evt.Info).not.toBeNull();
            var address = evt.Info;
            result(srv, address);
        });
    }
    _Connect(srv, address, clt, result) {
        var evt;
        var cltToSrvId = clt.Connect(address);
        var srvToCltId;
        this.waitForEvent(clt, () => {
            evt = clt.Dequeue();
            expect(evt).not.toBeNull();
            expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection);
            expect(evt.ConnectionId.id).toBe(cltToSrvId.id);
            this.waitForEvent(srv, () => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_0__.NetEventType.NewConnection);
                expect(evt.ConnectionId.id).not.toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_0__.ConnectionId.INVALID.id);
                srvToCltId = evt.ConnectionId;
                result(srvToCltId, cltToSrvId);
            });
        });
    }
    _CreateServerClient(result) {
        let srv;
        let address;
        let srvToCltId;
        let clt;
        let cltToSrvId;
        this._CreateServerNetwork((rsrv, raddress) => {
            srv = rsrv;
            address = raddress;
            clt = this._CreateNetwork();
            this._Connect(srv, address, clt, (rsrvToCltId, rcltToSrvId) => {
                srvToCltId = rsrvToCltId;
                cltToSrvId = rcltToSrvId;
                result(srv, address, srvToCltId, clt, cltToSrvId);
            });
        });
    }
}


/***/ }),

/***/ "./src/test/helper/IBasicNetworkTest.ts":
/*!**********************************************!*\
  !*** ./src/test/helper/IBasicNetworkTest.ts ***!
  \**********************************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   IBasicNetworkTest: () => (/* binding */ IBasicNetworkTest)
/* harmony export */ });
/* harmony import */ var _BasicNetworkTestBase__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ./BasicNetworkTestBase */ "./src/test/helper/BasicNetworkTestBase.ts");
/* harmony import */ var _awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../awrtc/network/index */ "./src/awrtc/network/index.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/


class IBasicNetworkTest extends _BasicNetworkTestBase__WEBPACK_IMPORTED_MODULE_0__.BasicNetworkTestBase {
    setup() {
        super.setup();
        let originalTimeout = 5000;
        beforeEach(() => {
            _awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.SLog.RequestLogLevel(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.SLogLevel.Info);
            originalTimeout = jasmine.DEFAULT_TIMEOUT_INTERVAL;
            jasmine.DEFAULT_TIMEOUT_INTERVAL = this.mDefaultWaitTimeout + 5000;
        });
        afterEach(() => {
            console.debug("Test shutting down ...");
            this.ShutdownAll();
            originalTimeout = jasmine.DEFAULT_TIMEOUT_INTERVAL;
            jasmine.DEFAULT_TIMEOUT_INTERVAL = this.mDefaultWaitTimeout + 5000;
        });
        //add all reusable tests here
        //TODO: check how to find the correct line where it failed
        it("TestEnvironmentAsync", (done) => {
            let value1 = false;
            let value2 = false;
            this.then(() => {
                expect(value1).toBe(false);
                expect(value2).toBe(false);
                value1 = true;
            });
            this.thenAsync((finished) => {
                expect(value1).toBe(true);
                expect(value2).toBe(false);
                value2 = true;
                finished();
            });
            this.then(() => {
                expect(value1).toBe(true);
                expect(value2).toBe(true);
                done();
            });
            this.start();
        });
        it("Create", () => {
            let clt;
            clt = this._CreateNetwork();
            expect(clt).not.toBe(null);
        });
        it("StartServer", (done) => {
            var evt;
            var srv;
            this.thenAsync((finished) => {
                srv = this._CreateNetwork();
                srv.StartServer();
                this.waitForEvent(srv, finished);
            });
            this.then(() => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ServerInitialized);
                expect(evt.ConnectionId.id).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID.id);
                expect(evt.Info).not.toBe(null);
                done();
            });
            this.start();
        });
        it("StartServerNamed", (done) => {
            var name = "StartServerNamedTest";
            var evt;
            var srv1;
            var srv2;
            srv1 = this._CreateNetwork();
            srv2 = this._CreateNetwork();
            this.thenAsync((finished) => {
                srv1.StartServer(name);
                this.waitForEvent(srv1, finished);
            });
            this.then(() => {
                evt = srv1.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ServerInitialized);
                expect(evt.ConnectionId.id).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID.id);
                expect(evt.Info).toBe(name);
            });
            this.thenAsync((finished) => {
                srv2.StartServer(name);
                this.waitForEvent(srv2, finished);
            });
            this.thenAsync((finished) => {
                //expect the server start to fail because the address is in use
                evt = srv2.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ServerInitFailed);
                expect(evt.ConnectionId.id).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID.id);
                expect(evt.Info).toBe(name);
                //stop the other server to free the address
                srv1.StopServer();
                this.waitForEvent(srv1, finished);
            });
            this.thenAsync((finished) => {
                //expect the server start to fail because the address is in use
                evt = srv1.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ServerClosed);
                expect(evt.ConnectionId.id).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID.id);
                //stop the other server to free the address
                srv2.StartServer(name);
                this.waitForEvent(srv2, finished);
            });
            this.thenAsync((finished) => {
                //expect the server start to fail because the address is in use
                evt = srv2.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ServerInitialized);
                done();
            });
            this.start();
        });
        it("StopServer", (done) => {
            var evt;
            var srv;
            this.thenAsync((finished) => {
                srv = this._CreateNetwork();
                srv.StopServer();
                this.waitForEvent(srv, finished, 100);
            });
            this.then(() => {
                evt = srv.Dequeue();
                expect(evt).toBeNull();
                done();
            });
            this.start();
        });
        it("StopServer2", (done) => {
            var evt;
            var srv;
            this.thenAsync((finished) => {
                srv = this._CreateNetwork();
                srv.StartServer();
                this.waitForEvent(srv, finished);
            });
            this.then(() => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ServerInitialized);
                expect(evt.ConnectionId.id).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID.id);
                expect(evt.Info).not.toBe(null);
            });
            this.thenAsync((finished) => {
                srv.StopServer();
                this.waitForEvent(srv, finished);
            });
            this.then(() => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ServerClosed);
                expect(evt.ConnectionId.id).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID.id);
                //enforce address in this to prepare having multiple addresses?
                //expect(evt.Info).not.toBe(null);
                done();
            });
            this.start();
        });
        it("_CreateServerNetwork", (done) => {
            var srv;
            var address;
            this.thenAsync((finished) => {
                this._CreateServerNetwork((rsrv, raddress) => {
                    srv = rsrv;
                    address = raddress;
                    finished();
                });
            });
            this.then(() => {
                expect(srv).not.toBeNull();
                expect(address).not.toBeNull();
                done();
            });
            this.start();
        });
        it("ConnectFail", (done) => {
            var evt;
            var clt;
            var cltId;
            this.thenAsync((finished) => {
                clt = this._CreateNetwork();
                cltId = clt.Connect("invalid address");
                this.waitForEvent(clt, finished);
            });
            this.then(() => {
                evt = clt.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ConnectionFailed);
                expect(evt.ConnectionId.id).toBe(cltId.id);
                done();
            });
            this.start();
        });
        it("ConnectTwo", (done) => {
            var srv;
            var address;
            var clt;
            var cltId;
            var evt;
            this.thenAsync((finished) => {
                srv = this._CreateNetwork();
                this._CreateServerNetwork((rsrv, raddress) => {
                    srv = rsrv;
                    address = raddress;
                    finished();
                });
            });
            this.thenAsync((finished) => {
                clt = this._CreateNetwork();
                cltId = clt.Connect(address);
                this.waitForEvent(clt, finished);
            });
            this.then(() => {
                evt = clt.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.NewConnection);
                expect(evt.ConnectionId.id).toBe(cltId.id);
            });
            this.thenAsync((finished) => {
                this.waitForEvent(srv, finished);
            });
            this.then(() => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.NewConnection);
                expect(evt.ConnectionId.id).not.toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID.id);
                done();
            });
            this.start();
        });
        it("ConnectHelper", (done) => {
            var srv;
            var address;
            var clt;
            var cltToSrvId;
            var srvToCltId;
            var evt;
            this.thenAsync((finished) => {
                this._CreateServerClient((rsrv, raddress, rsrvToCltId, rclt, rcltToSrvId) => {
                    srv = rsrv;
                    address = raddress;
                    srvToCltId = rsrvToCltId;
                    clt = rclt;
                    cltToSrvId = rcltToSrvId;
                    done();
                });
            });
            this.start();
        });
        it("Peek", (done) => {
            var evt;
            var net = this._CreateNetwork();
            var cltId1 = net.Connect("invalid address1");
            var cltId2 = net.Connect("invalid address2");
            var cltId3 = net.Connect("invalid address3");
            this.thenAsync((finished) => {
                this.waitForEvent(net, finished);
            });
            this.thenAsync((finished) => {
                evt = net.Peek();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ConnectionFailed);
                expect(evt.ConnectionId.id).toBe(cltId1.id);
                evt = net.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ConnectionFailed);
                expect(evt.ConnectionId.id).toBe(cltId1.id);
                this.waitForEvent(net, finished);
            });
            this.thenAsync((finished) => {
                evt = net.Peek();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ConnectionFailed);
                expect(evt.ConnectionId.id).toBe(cltId2.id);
                evt = net.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ConnectionFailed);
                expect(evt.ConnectionId.id).toBe(cltId2.id);
                this.waitForEvent(net, finished);
            });
            this.thenAsync((finished) => {
                evt = net.Peek();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ConnectionFailed);
                expect(evt.ConnectionId.id).toBe(cltId3.id);
                evt = net.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ConnectionFailed);
                expect(evt.ConnectionId.id).toBe(cltId3.id);
                done();
            });
            this.start();
        });
        it("Disconnect", (done) => {
            var evt;
            var clt = this._CreateNetwork();
            this.thenAsync((finished) => {
                clt.Disconnect(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID);
                this.waitForEvent(clt, finished, 100);
            });
            this.thenAsync((finished) => {
                evt = clt.Dequeue();
                expect(evt).toBeNull();
                clt.Disconnect(new _awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId(1234));
                this.waitForEvent(clt, finished, 100);
            });
            this.then(() => {
                evt = clt.Dequeue();
                expect(evt).toBeNull();
                done();
            });
            this.start();
        });
        it("DisconnectClient", (done) => {
            var srv;
            var address;
            var srvToCltId;
            var clt;
            var cltToSrvId;
            var evt;
            this.thenAsync((finished) => {
                this._CreateServerClient((rsrv, raddress, rsrvToCltId, rclt, rcltToSrvId) => {
                    srv = rsrv;
                    address = raddress;
                    srvToCltId = rsrvToCltId;
                    clt = rclt;
                    cltToSrvId = rcltToSrvId;
                    finished();
                });
            });
            this.thenAsync((finished) => {
                clt.Disconnect(cltToSrvId);
                this.waitForEvent(clt, finished);
            });
            this.thenAsync((finished) => {
                evt = clt.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(cltToSrvId.id).toBe(evt.ConnectionId.id);
                this.waitForEvent(srv, finished);
            });
            this.then(() => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(srvToCltId.id).toBe(evt.ConnectionId.id);
                done();
            });
            this.start();
        });
        it("DisconnectServer", (done) => {
            var srv;
            var address;
            var srvToCltId;
            var clt;
            var cltToSrvId;
            var evt;
            this.thenAsync((finished) => {
                this._CreateServerClient((rsrv, raddress, rsrvToCltId, rclt, rcltToSrvId) => {
                    srv = rsrv;
                    address = raddress;
                    srvToCltId = rsrvToCltId;
                    clt = rclt;
                    cltToSrvId = rcltToSrvId;
                    finished();
                });
            });
            this.thenAsync((finished) => {
                srv.Disconnect(srvToCltId);
                this.waitForEvent(srv, finished);
            });
            this.thenAsync((finished) => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(srvToCltId.id).toBe(evt.ConnectionId.id);
                this.waitForEvent(clt, finished);
            });
            this.then(() => {
                evt = clt.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(cltToSrvId.id).toBe(evt.ConnectionId.id);
                done();
            });
            this.start();
        });
        it("DisconnectServerMulti", (done) => {
            var srv;
            var address;
            var srvToClt1Id;
            var srvToClt2Id;
            var clt1;
            var clt1ToSrvId;
            var clt2;
            var clt2ToSrvId;
            var evt;
            this.thenAsync((finished) => {
                this._CreateServerClient((rsrv, raddress, rsrvToCltId, rclt, rcltToSrvId) => {
                    srv = rsrv;
                    address = raddress;
                    srvToClt1Id = rsrvToCltId;
                    clt1 = rclt;
                    clt1ToSrvId = rcltToSrvId;
                    finished();
                });
            });
            this.thenAsync((finished) => {
                clt2 = this._CreateNetwork();
                this._Connect(srv, address, clt2, (rsrvToCltId, rcltToSrvId) => {
                    srvToClt2Id = rsrvToCltId;
                    clt2ToSrvId = rcltToSrvId;
                    finished();
                });
            });
            this.thenAsync((finished) => {
                srv.Disconnect(srvToClt1Id);
                srv.Disconnect(srvToClt2Id);
                this.waitForEvent(srv, finished);
            });
            this.thenAsync((finished) => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(srvToClt1Id.id).toBe(evt.ConnectionId.id);
                this.waitForEvent(srv, finished);
            });
            this.thenAsync((finished) => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(srvToClt2Id.id).toBe(evt.ConnectionId.id);
                this.waitForEvent(clt1, finished);
            });
            this.thenAsync((finished) => {
                evt = clt1.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(clt1ToSrvId.id).toBe(evt.ConnectionId.id);
                this.waitForEvent(clt2, finished);
            });
            this.then(() => {
                evt = clt2.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(clt2ToSrvId.id).toBe(evt.ConnectionId.id);
                done();
            });
            this.start();
        });
        it("ShutdownEmpty", (done) => {
            var net;
            var evt;
            net = this._CreateNetwork();
            this.thenAsync((finished) => {
                net.Shutdown();
                this.waitForEvent(net, finished);
            });
            this.then(() => {
                evt = net.Dequeue();
                expect(evt).toBeNull();
                done();
            });
            this.start();
        });
        it("ShutdownServer", (done) => {
            var srv;
            var address;
            var srvToCltId;
            var clt;
            var cltToSrvId;
            var evt;
            this.thenAsync((finished) => {
                this._CreateServerClient((rsrv, raddress, rsrvToCltId, rclt, rcltToSrvId) => {
                    srv = rsrv;
                    address = raddress;
                    srvToCltId = rsrvToCltId;
                    clt = rclt;
                    cltToSrvId = rcltToSrvId;
                    finished();
                });
            });
            this.thenAsync((finished) => {
                srv.Shutdown();
                this.waitForEvent(clt, finished);
            });
            this.thenAsync((finished) => {
                evt = clt.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(cltToSrvId.id).toBe(evt.ConnectionId.id);
                this.waitForEvent(srv, finished);
            });
            this.thenAsync((finished) => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(srvToCltId.id).toBe(evt.ConnectionId.id);
                this.waitForEvent(srv, finished);
            });
            this.thenAsync((finished) => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ServerClosed);
                expect(evt.ConnectionId).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID);
                this.waitForEvent(srv, finished, 100);
            });
            this.then(() => {
                //no further events are suppose to be triggered after shutdown
                evt = srv.Dequeue();
                expect(evt).toBeNull();
                done();
            });
            this.start();
        });
        it("ShutdownClient", (done) => {
            var srv;
            var address;
            var srvToCltId;
            var clt;
            var cltToSrvId;
            var evt;
            this.thenAsync((finished) => {
                this._CreateServerClient((rsrv, raddress, rsrvToCltId, rclt, rcltToSrvId) => {
                    srv = rsrv;
                    address = raddress;
                    srvToCltId = rsrvToCltId;
                    clt = rclt;
                    cltToSrvId = rcltToSrvId;
                    finished();
                });
            });
            this.thenAsync((finished) => {
                clt.Shutdown();
                this.waitForEvent(clt, finished);
            });
            this.thenAsync((finished) => {
                evt = clt.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(cltToSrvId.id).toBe(evt.ConnectionId.id);
                this.waitForEvent(srv, finished);
            });
            this.thenAsync((finished) => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.Disconnected);
                expect(srvToCltId.id).toBe(evt.ConnectionId.id);
                this.waitForEvent(srv, finished, 100);
            });
            this.then(() => {
                evt = srv.Dequeue();
                expect(evt).toBeNull();
                done();
            });
            this.start();
        });
        it("DisconnectInvalid", (done) => {
            var evt;
            var clt = this._CreateNetwork();
            clt.Disconnect(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID);
            clt.Disconnect(new _awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId(1234));
            this.thenAsync((finished) => {
                this.waitForEvent(clt, finished, 100);
            });
            this.then(() => {
                evt = clt.Dequeue();
                expect(evt).toBeNull();
            });
            this.then(() => {
                done();
            });
            this.start();
        });
        it("SendDataTolerateInvalidDestination", (done) => {
            var evt;
            var clt = this._CreateNetwork();
            var testData = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9]);
            this.thenAsync((finished) => {
                clt.SendData(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID, testData, true);
                this.waitForEvent(clt, finished, 100);
            });
            this.then(() => {
                evt = clt.Dequeue();
                expect(evt).toBeNull();
            });
            this.thenAsync((finished) => {
                clt.SendData(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.ConnectionId.INVALID, testData, false);
                this.waitForEvent(clt, finished, 100);
            });
            this.then(() => {
                evt = clt.Dequeue();
                expect(evt).toBeNull();
            });
            this.then(() => {
                done();
            });
            this.start();
        });
        it("SendDataReliable", (done) => {
            var srv;
            var address;
            var srvToCltId;
            var clt;
            var cltToSrvId;
            var evt;
            var testMessage = "SendDataReliable_testmessage1234";
            var testMessageBytes = _awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.Encoding.UTF16.GetBytes(testMessage);
            var receivedTestMessage;
            this.thenAsync((finished) => {
                this._CreateServerClient((rsrv, raddress, rsrvToCltId, rclt, rcltToSrvId) => {
                    srv = rsrv;
                    address = raddress;
                    srvToCltId = rsrvToCltId;
                    clt = rclt;
                    cltToSrvId = rcltToSrvId;
                    finished();
                });
            });
            this.thenAsync((finished) => {
                clt.SendData(cltToSrvId, testMessageBytes, true);
                this.waitForEvent(srv, finished);
            });
            this.thenAsync((finished) => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ReliableMessageReceived);
                expect(srvToCltId.id).toBe(evt.ConnectionId.id);
                receivedTestMessage = _awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.Encoding.UTF16.GetString(evt.MessageData);
                expect(receivedTestMessage).toBe(testMessage);
                srv.SendData(srvToCltId, testMessageBytes, true);
                this.waitForEvent(clt, finished);
            });
            this.then(() => {
                evt = clt.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.ReliableMessageReceived);
                expect(cltToSrvId.id).toBe(evt.ConnectionId.id);
                receivedTestMessage = _awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.Encoding.UTF16.GetString(evt.MessageData);
                expect(receivedTestMessage).toBe(testMessage);
                done();
            });
            this.start();
        });
        it("SendDataUnreliable", (done) => {
            var srv;
            var address;
            var srvToCltId;
            var clt;
            var cltToSrvId;
            var evt;
            var testMessage = "SendDataUnreliable_testmessage1234";
            var testMessageBytes = _awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.Encoding.UTF16.GetBytes(testMessage);
            var receivedTestMessage;
            this.thenAsync((finished) => {
                this._CreateServerClient((rsrv, raddress, rsrvToCltId, rclt, rcltToSrvId) => {
                    srv = rsrv;
                    address = raddress;
                    srvToCltId = rsrvToCltId;
                    clt = rclt;
                    cltToSrvId = rcltToSrvId;
                    finished();
                });
            });
            this.thenAsync((finished) => {
                clt.SendData(cltToSrvId, testMessageBytes, false);
                this.waitForEvent(srv, finished);
            });
            this.thenAsync((finished) => {
                evt = srv.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.UnreliableMessageReceived);
                expect(srvToCltId.id).toBe(evt.ConnectionId.id);
                receivedTestMessage = _awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.Encoding.UTF16.GetString(evt.MessageData);
                expect(receivedTestMessage).toBe(testMessage);
                srv.SendData(srvToCltId, testMessageBytes, false);
                this.waitForEvent(clt, finished);
            });
            this.then(() => {
                evt = clt.Dequeue();
                expect(evt).not.toBeNull();
                expect(evt.Type).toBe(_awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.NetEventType.UnreliableMessageReceived);
                expect(cltToSrvId.id).toBe(evt.ConnectionId.id);
                receivedTestMessage = _awrtc_network_index__WEBPACK_IMPORTED_MODULE_1__.Encoding.UTF16.GetString(evt.MessageData);
                expect(receivedTestMessage).toBe(testMessage);
                done();
            });
            this.start();
        });
    }
}


/***/ })

/******/ 	});
/************************************************************************/
/******/ 	// The module cache
/******/ 	var __webpack_module_cache__ = {};
/******/ 	
/******/ 	// The require function
/******/ 	function __webpack_require__(moduleId) {
/******/ 		// Check if module is in cache
/******/ 		var cachedModule = __webpack_module_cache__[moduleId];
/******/ 		if (cachedModule !== undefined) {
/******/ 			return cachedModule.exports;
/******/ 		}
/******/ 		// Create a new module (and put it into the cache)
/******/ 		var module = __webpack_module_cache__[moduleId] = {
/******/ 			// no module.id needed
/******/ 			// no module.loaded needed
/******/ 			exports: {}
/******/ 		};
/******/ 	
/******/ 		// Execute the module function
/******/ 		__webpack_modules__[moduleId](module, module.exports, __webpack_require__);
/******/ 	
/******/ 		// Return the exports of the module
/******/ 		return module.exports;
/******/ 	}
/******/ 	
/************************************************************************/
/******/ 	/* webpack/runtime/compat get default export */
/******/ 	(() => {
/******/ 		// getDefaultExport function for compatibility with non-harmony modules
/******/ 		__webpack_require__.n = (module) => {
/******/ 			var getter = module && module.__esModule ?
/******/ 				() => (module['default']) :
/******/ 				() => (module);
/******/ 			__webpack_require__.d(getter, { a: getter });
/******/ 			return getter;
/******/ 		};
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/define property getters */
/******/ 	(() => {
/******/ 		// define getter functions for harmony exports
/******/ 		__webpack_require__.d = (exports, definition) => {
/******/ 			for(var key in definition) {
/******/ 				if(__webpack_require__.o(definition, key) && !__webpack_require__.o(exports, key)) {
/******/ 					Object.defineProperty(exports, key, { enumerable: true, get: definition[key] });
/******/ 				}
/******/ 			}
/******/ 		};
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/hasOwnProperty shorthand */
/******/ 	(() => {
/******/ 		__webpack_require__.o = (obj, prop) => (Object.prototype.hasOwnProperty.call(obj, prop))
/******/ 	})();
/******/ 	
/******/ 	/* webpack/runtime/make namespace object */
/******/ 	(() => {
/******/ 		// define __esModule on exports
/******/ 		__webpack_require__.r = (exports) => {
/******/ 			if(typeof Symbol !== 'undefined' && Symbol.toStringTag) {
/******/ 				Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });
/******/ 			}
/******/ 			Object.defineProperty(exports, '__esModule', { value: true });
/******/ 		};
/******/ 	})();
/******/ 	
/************************************************************************/
var __webpack_exports__ = {};
// This entry need to be wrapped in an IIFE because it need to be isolated against other modules in the chunk.
(() => {
/*!********************************!*\
  !*** ./src/test/test_entry.ts ***!
  \********************************/
__webpack_require__.r(__webpack_exports__);
/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   CAPITest_export: () => (/* reexport safe */ _CAPITest__WEBPACK_IMPORTED_MODULE_10__.CAPITest_export),
/* harmony export */   CallTestHelper: () => (/* reexport safe */ _CallTest__WEBPACK_IMPORTED_MODULE_4__.CallTestHelper),
/* harmony export */   DeviceApiTest_export: () => (/* reexport safe */ _DeviceApiTest__WEBPACK_IMPORTED_MODULE_7__.DeviceApiTest_export),
/* harmony export */   ExtractData: () => (/* reexport safe */ _VideoInputTest__WEBPACK_IMPORTED_MODULE_8__.ExtractData),
/* harmony export */   LocalNetworkTest: () => (/* reexport safe */ _LocalNetworkTest__WEBPACK_IMPORTED_MODULE_1__.LocalNetworkTest),
/* harmony export */   MakeBrokenTestCanvas: () => (/* reexport safe */ _VideoInputTest__WEBPACK_IMPORTED_MODULE_8__.MakeBrokenTestCanvas),
/* harmony export */   MakeTestCanvas: () => (/* reexport safe */ _VideoInputTest__WEBPACK_IMPORTED_MODULE_8__.MakeTestCanvas),
/* harmony export */   MakeTestImage: () => (/* reexport safe */ _VideoInputTest__WEBPACK_IMPORTED_MODULE_8__.MakeTestImage),
/* harmony export */   MediaNetworkTest: () => (/* reexport safe */ _MediaNetworkTest__WEBPACK_IMPORTED_MODULE_5__.MediaNetworkTest),
/* harmony export */   MediaTest_export: () => (/* reexport safe */ _MediaTest__WEBPACK_IMPORTED_MODULE_9__.MediaTest_export),
/* harmony export */   VideoInputTest_export: () => (/* reexport safe */ _VideoInputTest__WEBPACK_IMPORTED_MODULE_8__.VideoInputTest_export),
/* harmony export */   WebRtcNetworkTest: () => (/* reexport safe */ _WebRtcNetworkTest__WEBPACK_IMPORTED_MODULE_2__.WebRtcNetworkTest),
/* harmony export */   WebsocketTest: () => (/* reexport safe */ _WebsocketNetworkTest__WEBPACK_IMPORTED_MODULE_3__.WebsocketTest),
/* harmony export */   some_random_export_1: () => (/* reexport safe */ _BrowserApiTest__WEBPACK_IMPORTED_MODULE_6__.some_random_export_1)
/* harmony export */ });
/* harmony import */ var _awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../awrtc/network/Helper */ "./src/awrtc/network/Helper.ts");
/* harmony import */ var _LocalNetworkTest__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./LocalNetworkTest */ "./src/test/LocalNetworkTest.ts");
/* harmony import */ var _WebRtcNetworkTest__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./WebRtcNetworkTest */ "./src/test/WebRtcNetworkTest.ts");
/* harmony import */ var _WebsocketNetworkTest__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./WebsocketNetworkTest */ "./src/test/WebsocketNetworkTest.ts");
/* harmony import */ var _CallTest__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! ./CallTest */ "./src/test/CallTest.ts");
/* harmony import */ var _MediaNetworkTest__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! ./MediaNetworkTest */ "./src/test/MediaNetworkTest.ts");
/* harmony import */ var _BrowserApiTest__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! ./BrowserApiTest */ "./src/test/BrowserApiTest.ts");
/* harmony import */ var _DeviceApiTest__WEBPACK_IMPORTED_MODULE_7__ = __webpack_require__(/*! ./DeviceApiTest */ "./src/test/DeviceApiTest.ts");
/* harmony import */ var _VideoInputTest__WEBPACK_IMPORTED_MODULE_8__ = __webpack_require__(/*! ./VideoInputTest */ "./src/test/VideoInputTest.ts");
/* harmony import */ var _MediaTest__WEBPACK_IMPORTED_MODULE_9__ = __webpack_require__(/*! ./MediaTest */ "./src/test/MediaTest.ts");
/* harmony import */ var _CAPITest__WEBPACK_IMPORTED_MODULE_10__ = __webpack_require__(/*! ./CAPITest */ "./src/test/CAPITest.ts");
/* harmony import */ var _HelperTest__WEBPACK_IMPORTED_MODULE_11__ = __webpack_require__(/*! ./HelperTest */ "./src/test/HelperTest.ts");
/*
Copyright (c) 2019, because-why-not.com Limited
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/












console.log("Starting tests using info log level.");
_awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.SetTimePrefix(true);
_awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLog.SetLogLevel(_awrtc_network_Helper__WEBPACK_IMPORTED_MODULE_0__.SLogLevel.Info);
// Define a custom reporter
var customReporter = {
    specStarted: function (result) {
        console.log('Starting test:', result.fullName);
    },
    specDone: function (result) {
        console.log('test', result.status, ": ", result.description);
    }
};
// Add the custom reporter to Jasmine
jasmine.getEnv().addReporter(customReporter);

})();

/******/ })()
;
//# sourceMappingURL=test.js.map