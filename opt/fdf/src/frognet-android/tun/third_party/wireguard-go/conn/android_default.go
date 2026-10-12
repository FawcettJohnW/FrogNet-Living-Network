//go:build !fdfandroid

package conn

import "runtime"

const isAndroid = runtime.GOOS == "android"
