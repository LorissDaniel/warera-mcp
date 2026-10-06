#!/bin/sh
set -eu
exec warera-mcp --transport streamable-http --host 0.0.0.0 --port "${PORT:-8080}"
