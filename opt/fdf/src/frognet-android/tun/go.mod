module fawcettinnovations.com/fdf/tun

go 1.23.1

require (
	golang.org/x/sys v0.32.0
	golang.zx2c4.com/wireguard v0.0.0-20250521234502-f333402bd9cb
)

require (
	github.com/google/btree v1.1.2 // indirect
	golang.org/x/crypto v0.37.0 // indirect
	golang.org/x/net v0.39.0 // indirect
	golang.org/x/time v0.7.0 // indirect
	golang.zx2c4.com/wintun v0.0.0-20230126152724-0fa3db229ce2 // indirect
	gvisor.dev/gvisor v0.0.0-20250503011706-39ed1f5ac29c // indirect
)

replace (
	golang.zx2c4.com/wireguard => ./third_party/wireguard-go
	golang.org/x/crypto => ./third_party/x-crypto
	golang.org/x/net => ./third_party/x-net
	golang.org/x/sys => ./third_party/x-sys
	golang.org/x/term => ./third_party/stub/xterm
	golang.org/x/text => ./third_party/stub/xtext
	golang.org/x/time => ./third_party/stub/xtime
	golang.zx2c4.com/wintun => ./third_party/wintun
	gvisor.dev/gvisor => ./third_party/stub/gvisor
	github.com/google/btree => ./third_party/stub/btree
)
