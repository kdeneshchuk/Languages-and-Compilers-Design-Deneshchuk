#!/bin/sh
set -e
NAME=${1:-points}
python3 compiler.py tests/io/$NAME.txt $NAME.ll
llc -filetype=obj -relocation-model=pic $NAME.ll -o $NAME.o
clang -fPIE $NAME.o -o $NAME
echo "built ./$NAME"
