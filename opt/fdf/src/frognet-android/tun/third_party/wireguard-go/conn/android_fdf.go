//go:build fdfandroid

// [FDF_ANDROID_BUILD_V1] fdf-tun built for Android (a static linux executable run by the app, -tags fdfandroid):
// behave as upstream's Android build -- no IP_PKTINFO sticky sockets and no netlink route listener (Android forbids an
// app that socket: "permission denied"), Android's queue sizes.

package conn

const isAndroid = true
