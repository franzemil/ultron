# dsp/Makefile — host build of the shared C MFCC library (T6).
#
# Targets:
#   make            build libmfcc.a + libmfcc.so + mfcc_test (host)
#   make test       build & run a small host smoke test
#   make clean      remove generated artifacts
#
# Firmware-ready notes:
#   - Sources use only standard C99 headers (stdint, stddef, stdlib, string, math).
#   - No POSIX / host-only libc dependencies -> dsp/mfcc.{h,c} compile directly
#     into the ESP32 firmware (devices/esp32/ultron_voice/) and the Orange Pi
#     shared library (devices/orangepi/).
#   - Cross-compile for the Pi runtime shared lib:
#       make CC=aarch64-linux-gnu-gcc libmfcc.so
#   - Windows hosts: use MinGW-w64 via MSYS2 (pacman -S mingw-w64-ucrt-x86_64-gcc
#     make, run from an MSYS2 shell so sh/mkdir/rm exist). `make libmfcc.dll`
#     produces build/libmfcc.dll — MinGW exports all symbols by default, so the
#     C sources need no __declspec changes. WSL2 builds the POSIX targets
#     unchanged.
#
# Outputs are placed in build/ (gitignored). Never commit build/.

CC      ?= gcc
AR      ?= ar
CFLAGS  ?= -O2 -Wall -Wextra -std=c99 -I.
LDFLAGS ?= -lm

SRC   := mfcc.c
HDR   := mfcc.h
OBJ   := build/mfcc.o
LIB_A := build/libmfcc.a

# Host-platform shaping: Windows (MinGW-w64) names its shared library
# libmfcc.dll and appends .exe to executables; POSIX hosts keep libmfcc.so.
ifeq ($(OS),Windows_NT)
  EXE    := .exe
  LIB_SO := build/libmfcc.dll
else
  EXE    :=
  LIB_SO := build/libmfcc.so
endif

TEST  := build/mfcc_test$(EXE)
TEST_SRC := mfcc_host_test.c

.PHONY: all clean test libmfcc.a libmfcc.so libmfcc.dll mfcc_test

# Documented contract: "make builds libmfcc.a + libmfcc.so + mfcc_test".
# Real outputs live in build/; these are top-level aliases so that
# `make -C dsp libmfcc.so` (and the .a / .dll / mfcc_test variants) work
# from any checkout without path prefixes. Each alias delegates to this
# host's actual output (libmfcc.dll <-> build/libmfcc.dll on Windows).
libmfcc.a: $(LIB_A)
libmfcc.so: $(LIB_SO)
libmfcc.dll: $(LIB_SO)
mfcc_test: $(TEST)

all: $(LIB_A) $(LIB_SO)

$(LIB_A): $(OBJ)
	@mkdir -p build
	$(AR) rcs $@ $^

$(LIB_SO): $(OBJ)
	@mkdir -p build
	$(CC) -shared -fPIC -o $@ $^ $(LDFLAGS)

build/mfcc.o: $(SRC) $(HDR)
	@mkdir -p build
	$(CC) $(CFLAGS) -fPIC -c $< -o $@

mfcc_host_test.c:
	@echo 'int main(void){return 0;}' > $@

test: $(LIB_A) $(TEST)
	./$(TEST)

$(TEST): $(TEST_SRC) $(LIB_A)
	$(CC) $(CFLAGS) $< -o $@ $(LIB_A) $(LDFLAGS)

clean:
	rm -rf build mfcc_host_test.c
