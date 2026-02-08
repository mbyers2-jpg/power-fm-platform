/**
 * Ribbon SFU — mediasoup configuration
 */

const os = require('os');

module.exports = {
  // Worker settings
  worker: {
    rtcMinPort: parseInt(process.env.SFU_RTP_MIN_PORT || '40000'),
    rtcMaxPort: parseInt(process.env.SFU_RTP_MAX_PORT || '40200'),
    logLevel: 'warn',
    logTags: ['info', 'ice', 'dtls', 'rtp', 'srtp', 'rtcp'],
  },

  // Number of workers (default: 1 for small deployments)
  numWorkers: parseInt(process.env.SFU_NUM_WORKERS || '1'),

  // Router media codecs
  mediaCodecs: [
    {
      kind: 'audio',
      mimeType: 'audio/opus',
      clockRate: 48000,
      channels: 2,
    },
    {
      kind: 'video',
      mimeType: 'video/VP8',
      clockRate: 90000,
      parameters: {
        'x-google-start-bitrate': 1000,
      },
    },
    {
      kind: 'video',
      mimeType: 'video/VP9',
      clockRate: 90000,
      parameters: {
        'profile-id': 2,
        'x-google-start-bitrate': 1000,
      },
    },
    {
      kind: 'video',
      mimeType: 'video/H264',
      clockRate: 90000,
      parameters: {
        'packetization-mode': 1,
        'profile-level-id': '4d0032',
        'level-asymmetry-allowed': 1,
        'x-google-start-bitrate': 1000,
      },
    },
  ],

  // WebRTC transport settings
  webRtcTransport: {
    listenIps: [
      {
        ip: process.env.SFU_LISTEN_IP || '0.0.0.0',
        announcedIp: process.env.SFU_ANNOUNCED_IP || '127.0.0.1',
      },
    ],
    initialAvailableOutgoingBitrate: 1000000,
    minimumAvailableOutgoingBitrate: 600000,
    maxSctpMessageSize: 262144,
    maxIncomingBitrate: 5000000,

    enableUdp: true,
    enableTcp: true,
    preferUdp: true,
  },

  // Unix socket path for JSON-RPC
  socketPath: process.env.SFU_SOCKET_PATH ||
    require('path').join(__dirname, 'mediasoup.sock'),
};
