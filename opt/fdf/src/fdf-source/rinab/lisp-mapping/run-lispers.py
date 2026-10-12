# Run UNMODIFIED lispers.net in a kernel without IPv6: select lispers.net's own IPv4-only listen path
# (lisp_is_raspbian -> bind 0.0.0.0), and show real tracebacks. No lispers.net source is changed.
import sys, os, runpy
os.chdir(os.path.dirname(os.path.abspath(sys.argv[1]))); sys.path.insert(0, '.')
import lisp
lisp.lisp_is_raspbian = lambda: True
lisp.lisp_set_exception = lambda: None
script = os.path.basename(sys.argv[1]); sys.argv = [script] + sys.argv[2:]
runpy.run_path(script, run_name='__main__')
