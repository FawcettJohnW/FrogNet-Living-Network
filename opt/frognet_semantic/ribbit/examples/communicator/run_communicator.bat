@echo off
set HERE=%~dp0
set FROGNET_COMMUNICATOR_HOME=%HERE%
python "%HERE%communicator.py" %*
